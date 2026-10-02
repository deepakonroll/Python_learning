"""Orchestration: gate -> fetch -> evaluate cross -> dedupe -> Telegram -> state.

Exit codes (visible in Task Scheduler's "Last Run Result"):
    0 = ok / skipped (outside session, no signal)
    1 = configuration problem
    2 = Telegram delivery failed (state NOT saved -> retried next minute)
    3 = all data providers failed
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, time

from .config import INTERVAL_MINUTES, Config, ConfigError, strategies_for
from .control import effective_strategy, process_commands
from .market_hours import IST, in_session, is_trading_day, now_ist, session_bounds
from .notify import NotifyError, ping_message, send_telegram
from .providers.base import DataProvider, ProviderError, completed_bars, filter_session
from .providers.kite import KiteProvider
from .providers.tv import TvProvider
from .providers.yahoo import YahooProvider
from .signals import STRATEGY_LABELS, SignalError, evaluate
from .state import StateStore, SymbolState

log = logging.getLogger("nse_alerts")


def configure_logging(verbose: bool = False, log_file=None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,                       # fresh config on every one-shot run
    )


def build_providers(cfg: Config, watch) -> list[DataProvider]:
    """Ordered fallback chain per watch. auto = TradingView -> yahoo proxy."""
    if cfg.data_provider == "auto":
        tv: list[DataProvider] = [TvProvider()]              # type: ignore[list-item]
        yahoo: list[DataProvider] = ([YahooProvider()]       # type: ignore[list-item]
                                     if watch.yahoo_symbol else [])
        # MCX: anonymous TV access is blocked for MCX, so the free yahoo proxy
        # leads there; NSE keeps TradingView futures as primary.
        return yahoo + tv if watch.exchange == "MCX" else tv + yahoo
    if cfg.data_provider == "tv":
        return [TvProvider()]                                # type: ignore[list-item]
    if cfg.data_provider == "yahoo":
        if not watch.yahoo_symbol:
            raise ConfigError(f"no yahoo proxy configured for {watch.key}")
        return [YahooProvider()]                             # type: ignore[list-item]
    if cfg.data_provider == "kite":
        return [KiteProvider(cfg.kite_api_key, cfg.kite_access_token)]
    raise ConfigError(f"unknown DATA_PROVIDER {cfg.data_provider!r}")


def fetch_candles(cfg: Config, providers: list[DataProvider], now: datetime,
                  watch, lookback: int | None = None):
    """Try each provider in order for this watch; returns (candles, name).

    Session filtering is centralized here (exchange-aware): each provider
    returns raw normalized candles, we keep only bars inside the watch's
    session window and drop the still-forming bar.

    lookback: bar budget (defaults to cfg.lookback_bars; replay passes a big
    number so past dates aren't truncated by the recent-bars tail).
    """
    naive_now = now.astimezone(IST).replace(tzinfo=None) if now.tzinfo else now
    budget = lookback or cfg.lookback_bars
    errors: list[str] = []
    for provider in providers:
        # yahoo reads the watch's proxy symbol; others read the watch symbol
        symbol = watch.yahoo_symbol if provider.name == "yahoo" else watch.tv_symbol
        if provider.name == "yahoo" and not symbol:
            continue
        try:
            candles = provider.fetch(symbol, cfg.interval, budget)
            candles = filter_session(candles, watch.exchange)
            candles = completed_bars(candles, INTERVAL_MINUTES[cfg.interval], naive_now)
            if len(candles) < cfg.ema_len + 2:
                raise ProviderError(
                    f"only {len(candles)} completed bars (need {cfg.ema_len + 2})")
            if errors:
                log.warning("using fallback provider %r after: %s",
                            provider.name, "; ".join(errors))
            return candles, provider.name
        except ProviderError as exc:
            errors.append(f"{provider.name}: {exc}")
            log.warning("provider %s failed for %s -> %s",
                        provider.name, watch.key, exc)
    raise ProviderError(f"all providers failed for {watch.key}: " + " | ".join(errors))


def run(cfg: Config, *, dry_run: bool = False, test_notify: bool = False,
        verbose: bool = False) -> int:
    configure_logging(verbose, cfg.log_file)

    if test_notify:
        return _send_test(cfg, dry_run)

    now = now_ist()
    if not dry_run and not (cfg.telegram_token and cfg.telegram_chat_id):
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - fill nse_alerter/.env "
                  "(verify with --test-notify) before enabling the schedule")
        return 1

    # Owner commands (/disable /enable /status) - polled before the session gate
    # so they answer anytime a run happens; dry runs never poll (getUpdates is a
    # consuming read reserved for the live scheduler).
    store = StateStore(cfg.state_file)
    if not dry_run and not process_commands(cfg, store):
        log.info("alerts disabled via Telegram /disable - nothing to do")
        return 0

    # Telegram /strategy override beats env/default - mobile-first switching;
    # a per-watch '~strategy' suffix (SYMBOLS) applies ONLY when no global
    # override is active, so one tap still steers everything.
    effective = effective_strategy(cfg, store)
    override = store.get_control().get("strategy")
    log.debug("effective strategy: %s (watches: %s)",
              effective, ", ".join(
                  w.key + (f"~{w.strategy}" if w.strategy else "")
                  for w in cfg.watches))

    states = store.load()
    today = now.date().isoformat()
    ok = failed = 0

    # 08:45-09:00 window: one plan card per trading day (external cron ticks
    # 45,50,55 8 + GitHub backup). Early return - every session is still shut,
    # which also keeps the pre-open manual note quiet on card ticks.
    if (not dry_run and time(8, 45) <= now.time() < time(9, 0)
            and is_trading_day(now.date(), cfg.holidays)
            and store.get_control().get("card_date") != today):
        if _plan_card(cfg, now):
            store.set_control({**store.get_control(), "card_date": today})
        return 0

    for watch in cfg.watches:
        if not dry_run and not in_session(now, cfg.holidays, watch.exchange,
                                          watch.session):
            log.debug("outside %s session for %s (%s IST)",
                      watch.exchange, watch.key, now.strftime("%H:%M"))
            continue
        try:
            candles, source = fetch_candles(
                cfg, build_providers(cfg, watch), now, watch)
        except ProviderError as exc:
            log.error("%s -> skipped this run", exc)
            failed += 1
            continue
        bar_iso = candles.index[-1].isoformat()
        if not dry_run and watch.exchange == "NSE":
            _trend_alerts(cfg, store, candles, now, today)
        watch_ok = False
        strats = strategies_for(effective if override
                                else (watch.strategy or effective))
        for index, strat in enumerate(strats):
            # dedicated state key per (watch, strategy) so toggling never mixes
            key = watch.key if strat == "ema20" else f"{watch.key}#{strat}"
            stored = states.get(key)
            if (stored is not None and stored.last_processed_bar
                    and candles.index[-1]
                    < datetime.fromisoformat(stored.last_processed_bar)):
                # feed returned a window OLDER than what we already processed
                # (mid-day feed hiccup) - evaluating it would fabricate flips.
                # Equal bars are fine: same data -> same side, and the
                # new-day heartbeat still gets its chance.
                log.warning("stale window for %s (ends %s, stored %s) - skipped",
                            key, candles.index[-1], stored.last_processed_bar)
                watch_ok = True
                continue
            prev_side = stored.last_side if stored else None
            try:
                event, current_side = evaluate(
                    candles,
                    symbol=watch.key,
                    ema_len=cfg.ema_len,
                    prev_side=prev_side,
                    source=source,
                    strategy=strat,
                    rsi_period=cfg.qqe_rsi_period,
                    sf=cfg.qqe_sf,
                    factor=cfg.qqe_factor,
                    envelope_len=cfg.envelope_len,
                    envelope_percent=cfg.envelope_percent,
                    envelope_exponential=cfg.envelope_exponential,
                )
            except SignalError as exc:
                log.error("signal evaluation failed (%s, %s): %s",
                          watch.key, strat, exc)
                break                               # counts as failed below
            watch_ok = True

            if event is None:
                if stored is None:
                    log.info("baseline%s: %s [%s] side=%s (no alert on first run)",
                             " (dry-run, not saved)" if dry_run else "",
                             key, strat, current_side)
                else:
                    log.info("no cross - %s [%s] side=%s bar=%s",
                             key, strat, current_side, bar_iso)
                if not dry_run:
                    new_day = stored is None or stored.last_seen_date != today
                    store.put(key, SymbolState(
                        last_side=current_side,
                        last_processed_bar=bar_iso,
                        last_event_side=stored.last_event_side if stored else "",
                        last_event_bar=stored.last_event_bar if stored else "",
                        history=stored.history if stored else [],
                        last_seen_date=today,
                    ))
                    if new_day and index == 0:
                        _heartbeat(cfg, current_side, bar_iso, source,
                                   strats[0], watch.key)  # once/day per watch
                continue

            text = event.message()
            if dry_run:
                log.info("[dry-run] would send:\n%s", text)
                continue

            try:
                assert cfg.telegram_token and cfg.telegram_chat_id  # guarded above
                send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
            except NotifyError as exc:
                log.error("telegram send failed (%s, will retry next run): %s",
                          watch.key, exc)
                return 2                   # that state untouched -> retried

            state = stored or SymbolState(last_side=current_side)
            state.last_side = current_side
            state.last_processed_bar = bar_iso
            state.last_seen_date = today    # the alert itself proves liveness
            state.record_event(event.side, event.bar_time.isoformat())
            store.put(key, state)
            log.info("sent %s alert [%s] for %s (bar %s)",
                     event.side, strat, watch.key,
                     event.bar_time.strftime("%H:%M"))

        if watch_ok:
            ok += 1
        else:
            failed += 1

    if ok == 0 and failed > 0:
        log.error("no watch could be evaluated this run (%d failed)", failed)
        return 3
    if (ok == 0 and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and now.time() < session_bounds("MCX")[0]
            and not (time(8, 45) <= now.time() < time(9, 0))):  # card window
        # Pre-open manual runs reply with proof of life. Post-close runs and the
        # external cron's own dispatches (also workflow_dispatch!) stay silent -
        # otherwise every dead-tail tick would spam a note.
        _manual_note(cfg, effective, len(cfg.watches))
    return 0


_SESSION_BY_WEEKDAY = {                      # Nifty expiry Tue · Sensex Thu
    0: ("NIFTY", "regular · 1 day to Tuesday expiry"),
    1: ("NIFTY", "EXPIRY DAY"),
    2: ("SENSEX", "regular · 1 day to Thursday expiry"),
    3: ("SENSEX", "EXPIRY DAY"),
    4: ("NIFTY", "regular · 3 days to Tuesday expiry"),
}


def _plan_card(cfg: Config, now: datetime) -> bool:
    """08:45 discipline card - static rules, no market data, so a flaky feed
    can never eat it. True = sent (the caller records card_date for the
    once-per-day dedupe; failures retry on the next tick at 8:50, 8:55)."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    instrument, session = _SESSION_BY_WEEKDAY[now.weekday()]
    expiry = ("\n• EXPIRY: the premium-doubles stop applies as usual "
              "(no SL-free runs) · adjust from strength only"
              if "EXPIRY" in session else "")
    text = (f"📋 PLAN · {now:%a %d %b} · {instrument} — {session}\n"
            "• Intraday only · one structure · first entry after 09:45\n"
            "• Filter: premium ≥ ₹15 · delta ≤ 0.35\n"
            "• Size: lots = ₹10,000 ÷ (premium × lot size)\n"
            "• Exit: premium DOUBLES → exit ALL legs (order at fill)\n"
            "          else flat ALL by 15:15\n"
            "• New legs only in profit · adjust only while ≥ -2k\n"
            "• Hard flatten at -10k → session over\n"
            "• Nifty watch: ⚔️ ±0.45% = NO averaging · 🔴 ±0.8% = trend day, "
            "no re-entry" + expiry)
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.error("plan card failed (%s) - next tick retries", exc)
        return False
    log.info("plan card sent (%s %s)", now.strftime("%a"), session)
    return True


def _trend_alerts(cfg: Config, store: StateStore, candles, now: datetime,
                  today: str) -> None:
    """Nifty distance-from-open tripwires (Nifty drives ALL days, Sensex
    included - per the trading plan). ⚔️ 0.45% ~= the 2-strike zone: no
    averaging. 🔴 0.8% = trend day: flatten path, no re-entry. Thresholds
    tuned on 60d of NIFTY 5m (3/3 big days caught). One fire per level per
    day; a failed send retries next run; failures never touch signal state."""
    day = candles[candles.index.date == now.date()]
    if day.empty:
        return
    open_px = float(day["Open"].iloc[0])
    dev = (day["Close"] - open_px).abs() / open_px * 100.0
    peak = float(dev.max())
    at = dev.idxmax()
    side = "up" if float(day["Close"].loc[at]) >= open_px else "down"
    ctrl = store.get_control()
    if ctrl.get("trend_date") != today:
        ctrl = {**ctrl, "trend_date": today, "zone_sent": False,
                "trend_sent": False}
    updates = {}
    if peak >= 0.45 and not ctrl.get("zone_sent") and _trend_send(
            cfg, f"⚔️ NIFTY {peak:.2f}% from open ({side}) · ZONE\n"
                 "if short: NO averaging · stops live · exits per plan"):
        updates["zone_sent"] = True
    if peak >= 0.8 and not ctrl.get("trend_sent") and _trend_send(
            cfg, f"🔴 TREND DAY · NIFTY {peak:.2f}% from open ({side})\n"
                 "flatten path · NO re-entry today"):
        updates["trend_sent"] = True
    if updates:
        store.set_control({**ctrl, **updates})


def _trend_send(cfg: Config, text: str) -> bool:
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.warning("trend alert failed (%s) - will retry", exc)
        return False
    return True


def _manual_note(cfg: Config, strategy: str, watch_count: int) -> None:
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return
    text = (f"🧪 manual run · {now_ist():%H:%M} IST · out of session\n"
            f"NSE 09:15-15:35 · MCX 09:00-23:35 (Mon-Fri) · "
            f"strategy={strategy} · watches={watch_count}")
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.warning("manual note failed: %s", exc)


def _send_test(cfg: Config, dry_run: bool) -> int:
    text = ping_message(cfg.symbol, cfg.interval, cfg.strategy)
    if dry_run:
        log.info("[dry-run] would send:\n%s", text)
        return 0
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        log.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in nse_alerter/.env "
                  "first (steps are in the README)")
        return 1
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.error("test send failed: %s", exc)
        return 2
    log.info("test message delivered - check your Telegram")
    return 0


def _heartbeat(cfg: Config, side: str, bar_iso: str, source: str,
               strategy_label: str | None = None,
               symbol: str | None = None) -> None:
    """One liveness ping per day per watch (and on the watch's first run), so
    a silent feed is impossible to miss. Failures never affect alerts."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return
    text = (f"🔎 monitoring live · {now_ist():%d %b %Y}\n"
            f"{symbol or cfg.symbol} side={side} · "
            f"rule={strategy_label or cfg.strategy} · "
            f"last bar {bar_iso} · src={source}")
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.warning("heartbeat failed (alerts unaffected): %s", exc)


def replay(cfg: Config, target: date, *, verbose: bool = False) -> list:
    """Walk one past session bar-by-bar and print every cross that would have
    fired. Sends nothing, writes no state - safe any day, any time.

    Uses the exact same evaluate() path as the live run, so what you see here
    is what you'd have been alerted on.
    """
    configure_logging(verbose, cfg.log_file)
    watch = cfg.watches[0]
    # cutoff = that day's session grace-end, so every bar that day is closed
    cutoff = datetime.combine(target, session_bounds(watch.exchange)[2])
    try:
        # big lookback: replay needs the past date inside the fetched window
        candles, source = fetch_candles(cfg, build_providers(cfg, watch),
                                        cutoff, watch, lookback=5000)
    except ProviderError as exc:
        print(f"replay: no data ({exc})")
        return []

    day_bars = candles[candles.index.date == target]
    if day_bars.empty:
        print(f"replay: no bars on {target.isoformat()} "
              f"(weekend/holiday or feed gap) - try DATA_PROVIDER=yahoo")
        return []

    pre = candles[candles.index < day_bars.index[0]]
    # strategy precedence mirrors live: Telegram override > ~suffix > config
    control = StateStore(cfg.state_file).get_control()
    eff = control.get("strategy") or watch.strategy or cfg.strategy
    strat = "ema20" if eff == "both" else eff       # replay walks the primary engine
    label = STRATEGY_LABELS.get(strat, strat)
    print(f"Replay {watch.key} · {target.isoformat()} · {cfg.interval} "
          f"{label} · feed={source} · {len(day_bars)} bars that day"
          + ("  [strategy=both -> showing ema20]" if eff == "both" else ""))
    if strat == "env":
        min_pre = cfg.envelope_len + 2
    elif strat == "ema20":
        min_pre = cfg.ema_len + 2
    else:
        min_pre = 72                               # QQE warm-up
    if len(pre) < min_pre:
        print(f"insufficient warm-up before the session ({len(pre)} bars) - "
              f"try DATA_PROVIDER=yahoo (deeper history)")
        return []

    kwargs = dict(symbol=watch.key, ema_len=cfg.ema_len, strategy=strat,
                  rsi_period=cfg.qqe_rsi_period, sf=cfg.qqe_sf,
                  factor=cfg.qqe_factor, source=source,
                  envelope_len=cfg.envelope_len,
                  envelope_percent=cfg.envelope_percent,
                  envelope_exponential=cfg.envelope_exponential)
    _, entry = evaluate(pre, prev_side=None, **kwargs)
    print(f"entering side: {entry}")

    events, prev, pos = [], entry, len(pre)
    for i in range(pos, len(candles)):
        event, prev = evaluate(candles.iloc[:i + 1], prev_side=prev, **kwargs)
        if event:
            events.append(event)
            print(f"  {event.bar_time:%H:%M}  {event.side:<4}  {event.rule}")

    print(f"-> {len(events)} cross(es) that day · ending side {prev}")
    return events

