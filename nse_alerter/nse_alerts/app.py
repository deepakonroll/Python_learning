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
from datetime import date, datetime, time, timedelta

from . import expiry
from .config import (INTERVAL_MINUTES, TV_SYMBOL_OVERRIDES, Config,
                     ConfigError, Watch, strategies_for)
from .control import effective_strategy, process_commands
from .envelope import envelope_lines, envelope_prep
from .market_hours import IST, in_session, is_trading_day, now_ist, session_bounds
from .notify import NotifyError, ping_message, send_telegram
from .providers.base import DataProvider, ProviderError, completed_bars, filter_session
from .providers.kite import KiteProvider
from .providers.tv import TvProvider
from .providers.yahoo import YahooProvider
from .signals import STRATEGY_LABELS, SignalError, evaluate, prep_message
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
        return _free_chain(watch)
    if cfg.data_provider == "tv":
        return [TvProvider()]                                # type: ignore[list-item]
    if cfg.data_provider == "yahoo":
        if not watch.yahoo_symbol:
            raise ConfigError(f"no yahoo proxy configured for {watch.key}")
        return [YahooProvider()]                             # type: ignore[list-item]
    if cfg.data_provider == "kite":
        # Kite leads so alerts read the exact front-month contracts, but the
        # free chain STAYS behind it: the access token dies every trading day,
        # and a stale one must degrade to TV/yahoo - never darken the alerts.
        if not (cfg.kite_api_key and cfg.kite_access_token):
            log.warning("DATA_PROVIDER=kite but KITE_API_KEY/KITE_ACCESS_TOKEN "
                        "not set - kite fetches will fail and fall back")
        return [KiteProvider(cfg.kite_api_key, cfg.kite_access_token),  # type: ignore[list-item]
                *_free_chain(watch)]
    raise ConfigError(f"unknown DATA_PROVIDER {cfg.data_provider!r}")


def _free_chain(watch) -> list[DataProvider]:
    """TradingView futures -> yahoo spot proxy, ordered per watch (auto)."""
    tv: list[DataProvider] = [TvProvider()]              # type: ignore[list-item]
    yahoo: list[DataProvider] = ([YahooProvider()]       # type: ignore[list-item]
                                 if watch.yahoo_symbol else [])
    # MCX: anonymous TV access is blocked for MCX symbols, so the free
    # yahoo proxy leads there - UNLESS the watch has a TV override
    # (crude -> TVC:UKOIL, real-time): yahoo's BZ=F measured +10 min
    # stale (2026-10-06), so the fresh TV symbol then leads and yahoo
    # is only the fallback. NSE keeps TradingView futures as primary.
    if watch.exchange == "MCX" and watch.key not in TV_SYMBOL_OVERRIDES:
        return yahoo + tv
    return tv + yahoo


def fetch_candles(cfg: Config, providers: list[DataProvider], now: datetime,
                  watch, lookback: int | None = None,
                  min_bars: int | None = None,
                  include_forming: bool = False):
    """Try each provider in order for this watch; returns (candles, name).

    Session filtering is centralized here (exchange-aware): each provider
    returns raw normalized candles, we keep only bars inside the watch's
    session window and drop the still-forming bar.

    lookback: bar budget (defaults to cfg.lookback_bars; replay passes a big
    number so past dates aren't truncated by the recent-bars tail).
    min_bars: warm-up floor (defaults to cfg.ema_len + 2). The 09:45 expiry
    alert only needs the last completed bar, so it passes 1.
    """
    naive_now = now.astimezone(IST).replace(tzinfo=None) if now.tzinfo else now
    budget = lookback or cfg.lookback_bars
    need = cfg.ema_len + 2 if min_bars is None else min_bars
    errors: list[str] = []
    for provider in providers:
        # yahoo reads the watch's proxy symbol, kite reads the raw watch
        # symbol (its own front-month picker maps MCX:CRUDEOIL to the real
        # INR contract; the TV override is TV-only), tv reads the watch
        # symbol or its override (crude fetches TVC:UKOIL, real-time)
        if provider.name == "yahoo":
            symbol = watch.yahoo_symbol
        elif provider.name == "kite":
            symbol = watch.tv_symbol
        else:
            symbol = TV_SYMBOL_OVERRIDES.get(watch.key, watch.tv_symbol)
        if provider.name == "yahoo" and not symbol:
            continue
        try:
            candles = provider.fetch(symbol, cfg.interval, budget)
            candles = filter_session(candles, watch.exchange)
            closed = completed_bars(candles, INTERVAL_MINUTES[cfg.interval],
                                    naive_now)
            if len(closed) < need:
                raise ProviderError(
                    f"only {len(closed)} completed bars (need {need})")
            if include_forming and len(closed) < len(candles):
                # Hand the live row back too (never future-started glitch
                # rows): the caller splits with split_forming() - signals see
                # completed bars only, the env prep alert watches the forming
                # one. Validated against `closed` above, so the split can never
                # starve evaluate() below its warm-up floor.
                closed = candles[candles.index <= naive_now]
            if errors:
                log.warning("using fallback provider %r after: %s",
                            provider.name, "; ".join(errors))
            return closed, provider.name
        except ProviderError as exc:
            errors.append(f"{provider.name}: {exc}")
            log.warning("provider %s failed for %s -> %s",
                        provider.name, watch.key, exc)
    raise ProviderError(f"all providers failed for {watch.key}: " + " | ".join(errors))


def split_forming(candles, interval_minutes: int, now: datetime):
    """(completed bars, forming row or None).

    fetch_candles(include_forming=True) hands the live row back too; signal
    evaluation must see completed bars ONLY (the envelope's full-bar rule),
    while the prep alert watches the forming one. Mirror of completed_bars().
    """
    if candles is None or candles.empty:
        return candles, None
    naive = now.astimezone(IST).replace(tzinfo=None) if now.tzinfo else now
    if candles.index[-1] + timedelta(minutes=interval_minutes) > naive:
        return candles.iloc[:-1], candles.iloc[[-1]]
    return candles, None


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

    # 08:45-09:10 window: one plan card per trading day (external cron ticks
    # 45,50,55 8 + GitHub backup + the 09:00/09:05 main-cron ticks). The 09:10
    # end is a CI-startup grace: a dispatch TRIGGERED at 08:59 only EXECUTES
    # after ~09:00. Early return - sessions are shut, so the pre-open manual
    # note stays quiet on card ticks.
    if (not dry_run and time(8, 45) <= now.time() < time(9, 10)
            and is_trading_day(now.date(), cfg.holidays)
            and store.get_control().get("card_date") != today):
        if _plan_card(cfg, now):
            store.set_control({**store.get_control(), "card_date": today})
        return 0

    # 09:45 entry alert, every trading day (ENTRY_SESSIONS: NIFTY Mon/Tue/Fri,
    # SENSEX Wed/Thu): strike_rule() decision + premiums + 2x leg stops +
    # portfolio stop/target + 15:15 square-off, once per day. Entering the
    # block consumes this tick (mirrors the plan card); a failed send leaves
    # the key unset so the next 5-min tick retries until 10:15.
    instrument = (expiry.EXPIRY_ROTATION.get(now.weekday())
                  if is_trading_day(now.date(), cfg.holidays) else None)
    if (not dry_run and instrument
            and expiry.ALERT_FROM <= now.time() < expiry.ALERT_UNTIL
            and store.get_control().get("expiry_alert_date") != today):
        if _expiry_alert(cfg, now, instrument):
            store.set_control({**store.get_control(),
                               "expiry_alert_date": today})
        return 0

    for watch in cfg.watches:
        if not dry_run and not in_session(now, cfg.holidays, watch.exchange,
                                          watch.session):
            log.debug("outside %s session for %s (%s IST)",
                      watch.exchange, watch.key, now.strftime("%H:%M"))
            continue
        try:
            candles, source = fetch_candles(
                cfg, build_providers(cfg, watch), now, watch,
                include_forming=True)
        except ProviderError as exc:
            log.error("%s -> skipped this run", exc)
            failed += 1
            continue
        # signals see completed bars only; the forming row feeds the env prep
        # heads-up (the full-bar rule must never evaluate a live bar)
        candles, forming = split_forming(candles,
                                         INTERVAL_MINUTES[cfg.interval], now)
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
                    prep_fired = stored.prep_fired if stored else False
                    if cfg.prep_alerts and strat == "env":
                        prep_fired = _prep_alert(
                            cfg, watch.key, candles, forming, current_side,
                            prep_fired, source)
                    store.put(key, SymbolState(
                        last_side=current_side,
                        last_processed_bar=bar_iso,
                        last_event_side=stored.last_event_side if stored else "",
                        last_event_bar=stored.last_event_bar if stored else "",
                        history=stored.history if stored else [],
                        last_seen_date=today,
                        prep_fired=prep_fired,
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
            and not (time(8, 45) <= now.time() < time(9, 10))):  # card window
        # Pre-open manual runs reply with proof of life. Post-close runs and the
        # external cron's own dispatches (also workflow_dispatch!) stay silent -
        # otherwise every dead-tail tick would spam a note.
        _manual_note(cfg, effective, len(cfg.watches))
    return 0


def _prep_alert(cfg: Config, key: str, candles, forming, current_side: str,
                prep_fired: bool, source: str) -> bool:
    """⚠️ envelope pre-flip heads-up - one per approach episode.

    evaluate() stays the only path that moves state (a completed full-bar
    break); this only says the LIVE forming bar would flip if it closed right
    now, so the band test is seen BEFORE the '+' forms. The episode re-arms
    when a completed bar closes back inside the band; a failed send keeps the
    flag down so the next tick retries. Returns the updated prep_fired flag.
    """
    _, up_s, lo_s = envelope_lines(candles["Close"], cfg.envelope_len,
                                   cfg.envelope_percent,
                                   cfg.envelope_exponential)
    up, lo = float(up_s.iloc[-1]), float(lo_s.iloc[-1])
    if up == up and lo == lo and lo <= float(candles["Close"].iloc[-1]) <= up:
        prep_fired = False              # last completed bar back inside: re-arm
    if prep_fired or forming is None:
        return prep_fired               # episode consumed / no live row (yahoo)
    live = float(forming["Close"].iloc[-1])
    got = envelope_prep(candles["Close"], live, current_side,
                        cfg.envelope_len, cfg.envelope_percent,
                        cfg.envelope_exponential)
    if got is None or not (cfg.telegram_token and cfg.telegram_chat_id):
        return prep_fired
    direction, level = got
    text = prep_message(key, direction, live, level, cfg.envelope_percent,
                        forming.index[-1].to_pydatetime(), source,
                        current_side)
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.warning("prep alert failed for %s (%s) - will retry", key, exc)
        return prep_fired
    log.info("prep alert sent for %s (%s live %.2f vs band %.2f)", key,
             direction, live, level)
    return True


# static 09:45 rule line per weekday (VIX-ladder sweep, 2026-10-09): every
# weekday is an entry day under expiry.EXPIRY_ROTATION.
_ENTRY_LINE = {
    0: "• 09:45 · NIFTY closer 0.95% (1.9 sess to Tue) · floor ₹18 · stop −0.5C",
    1: "• 09:45 · NIFTY 0DTE · enter only if credit ≥ ₹18 · stop −1.0C · else SKIP",
    2: "• 09:45 · SENSEX closer 0.95% (1.9 sess to Thu) · floor ₹40 · stop −0.5C",
    3: "• 09:45 · SENSEX 0DTE · NO ENTRY (measured E<0) · envelope only",
    4: "• 09:45 · NIFTY closer 0.95% (2.9 sess to Tue) · floor ₹18 · stop −0.5C",
}


def _plan_card(cfg: Config, now: datetime) -> bool:
    """08:45 discipline card (v5) - static rules, no market data, so a flaky
    feed can never eat it. Every weekday leads with the day's strangle plan
    (Thu = NO ENTRY reminder); the envelope-pilot rules ride along because the
    envelope runs daily. True = sent (the caller records card_date for the
    once-per-day dedupe; failures retry on the next tick at 8:50, 8:55, 9:00,
    9:05)."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    wd = now.weekday()
    instrument = expiry.EXPIRY_ROTATION[wd]
    spec = expiry.EXPIRY_SPECS[instrument]
    title = ("NIFTY 0-DTE STRANGLE" if wd == 1
             else "SENSEX STRANGLE · NO ENTRY" if wd == 3
             else f"{instrument} intraday STRANGLE")
    text = (
        f"📋 PLAN · {now:%a %d %b} · {title}\n"
        f"• 09:45 alert → rule CLOSER 0.95% CE + PE "
        f"(step {spec['step']} · lot {spec['lot']})\n"
        + _ENTRY_LINE[wd] + "\n"
        "• Size 20 lots fixed (1,300 NIFTY / 400 SENSEX qty) · ONE entry · NO adds\n"
        "• STOP 2× on EITHER leg → exit BOTH (orders at fill) · never remove\n"
        "• Portfolio: stop −0.5C (−1.0C on NIFTY 0-DTE) · target = 15:15 decay\n"
        "• Square off ALL by 15:15 · no re-entry · journal every trade\n"
        "• Envelope ON daily (pilot): BLUE cross → sell ATM PE · "
        "RED cross → sell ATM CE (1 lot)\n"
        "• Entry ≤ 2 bars after the alert · ATM = nearest 50-pt strike\n"
        "• Exit: REVERSE cross or flat ALL by 15:15 · backstop: premium "
        "DOUBLES → exit (order at fill)\n"
        "• Max 2 envelope trades/day · NIFTY + crude @17:00-22:00 · "
        "charts on NIFTY ONLY\n"
        "• Ladder: -7k = WARN · -10k = EXIT, session over\n"
        "• Tripwires live: ⚔️ ±0.45% = context · 🔴 ±0.8% = trend day"
    )
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.error("plan card failed (%s) - next tick retries", exc)
        return False
    log.info("plan card sent (%s %s)", now.strftime("%a"), instrument)
    return True


def _expiry_watch(cfg: Config, instrument: str):
    """Prefer the owner's configured watch (custom SYMBOLS/proxy), else the
    built-in spec (NIFTY -> NSE:NIFTY1!, SENSEX -> BSE:SENSEX)."""
    if instrument == "NIFTY":
        for watch in cfg.watches:
            if watch.key.upper().startswith("NIFTY"):
                return watch
    spec = expiry.EXPIRY_SPECS[instrument]
    return Watch(key=spec["tv"], label=instrument, exchange=spec["exchange"],
                 tv_symbol=spec["tv"], yahoo_symbol=spec["yahoo"])


def _expiry_alert(cfg: Config, now: datetime, instrument: str) -> bool:
    """09:45 entry alert: spot -> strike_rule() -> premiums -> stops/size.
    True = sent (the caller records expiry_alert_date for the once-per-day
    dedupe); False = a later tick inside 09:45-10:15 retries."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    watch = _expiry_watch(cfg, instrument)
    try:
        candles, source = fetch_candles(cfg, build_providers(cfg, watch), now,
                                        watch, lookback=30, min_bars=1)
    except ProviderError as exc:
        log.error("expiry alert: no %s spot (%s) - retrying next tick",
                  instrument, exc)
        return False
    spot = float(candles["Close"].iloc[-1])
    bar_time = candles.index[-1].to_pydatetime()
    iv, iv_src = expiry.fetch_india_vix()
    text = expiry.build_alert(instrument, spot, bar_time, now, iv, iv_src,
                              source)
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.error("expiry alert failed (%s) - retrying next tick", exc)
        return False
    log.info("expiry entry alert sent (%s, spot %.0f, src=%s)",
             instrument, spot, source)
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

