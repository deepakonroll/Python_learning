"""Orchestration: gate -> fetch -> evaluate cross -> dedupe -> Telegram -> state.

Exit codes (visible in Task Scheduler's "Last Run Result"):
    0 = ok / skipped (outside session, no signal)
    1 = configuration problem
    2 = Telegram delivery failed (state NOT saved -> retried next minute)
    3 = all data providers failed
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, time

from .config import INTERVAL_MINUTES, Config, ConfigError
from .control import effective_strategy, process_commands
from .market_hours import IST, in_session, now_ist
from .notify import KEYBOARD, NotifyError, ping_message, send_telegram
from .providers.base import DataProvider, ProviderError, completed_bars
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


def build_providers(cfg: Config) -> list[DataProvider]:
    """Ordered fallback chain. auto = TradingView futures -> yfinance spot."""
    if cfg.data_provider == "auto":
        return [TvProvider(), YahooProvider()]        # type: ignore[list-item]
    if cfg.data_provider == "tv":
        return [TvProvider()]                         # type: ignore[list-item]
    if cfg.data_provider == "yahoo":
        return [YahooProvider()]                      # type: ignore[list-item]
    if cfg.data_provider == "kite":
        return [KiteProvider(cfg.kite_api_key, cfg.kite_access_token)]
    raise ConfigError(f"unknown DATA_PROVIDER {cfg.data_provider!r}")


def fetch_candles(cfg: Config, providers: list[DataProvider], now: datetime):
    """Try each provider in order; returns (candles, provider_name)."""
    naive_now = now.astimezone(IST).replace(tzinfo=None) if now.tzinfo else now
    errors: list[str] = []
    for provider in providers:
        # yahoo always reads the configured spot proxy, others read SYMBOL
        symbol = cfg.yahoo_symbol if provider.name == "yahoo" else cfg.symbol
        try:
            candles = provider.fetch(symbol, cfg.interval, cfg.lookback_bars)
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
            log.warning("provider %s failed -> %s", provider.name, exc)
    raise ProviderError("all providers failed: " + " | ".join(errors))


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

    # Telegram /strategy override beats env/default - mobile-first switching
    effective = effective_strategy(cfg, store)

    if not dry_run and not in_session(now, cfg.holidays):
        log.debug("outside NSE session (%s IST) - nothing to do", now.strftime("%H:%M"))
        return 0

    try:
        candles, source = fetch_candles(cfg, build_providers(cfg), now)
    except ProviderError as exc:
        log.error("%s", exc)
        return 3

    states = store.load()
    bar_iso = candles.index[-1].isoformat()
    today = now.date().isoformat()
    # one engine, or both when the effective strategy is 'both'
    strategies = ["ema20", "qqe"] if effective == "both" else [effective]
    log.debug("effective strategy: %s", effective)

    for index, strat in enumerate(strategies):
        # dedicated state key per strategy so toggling never mixes baselines
        key = cfg.symbol if strat == "ema20" else f"{cfg.symbol}#{strat}"
        stored = states.get(key)
        prev_side = stored.last_side if stored else None
        try:
            event, current_side = evaluate(
                candles,
                symbol=cfg.symbol,
                ema_len=cfg.ema_len,
                prev_side=prev_side,
                source=source,
                strategy=strat,
                rsi_period=cfg.qqe_rsi_period,
                sf=cfg.qqe_sf,
                factor=cfg.qqe_factor,
            )
        except SignalError as exc:
            log.error("signal evaluation failed (%s): %s", strat, exc)
            return 3

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
                               effective)     # once/day, labels the mode
            continue

        text = event.message()
        if dry_run:
            log.info("[dry-run] would send:\n%s", text)
            continue

        try:
            assert cfg.telegram_token and cfg.telegram_chat_id   # guarded above
            send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text,
                          reply_markup=KEYBOARD)
        except NotifyError as exc:
            log.error("telegram send failed (%s, will retry next run): %s",
                      strat, exc)
            return 2                       # state untouched -> retried next run

        state = stored or SymbolState(last_side=current_side)
        state.last_side = current_side
        state.last_processed_bar = bar_iso
        state.last_seen_date = today        # the alert itself proves liveness
        state.record_event(event.side, event.bar_time.isoformat())
        store.put(key, state)
        log.info("sent %s alert [%s] for %s (bar %s)",
                 event.side, strat, cfg.symbol, event.bar_time.strftime("%H:%M"))
    return 0


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
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text,
                      reply_markup=KEYBOARD)
    except NotifyError as exc:
        log.error("test send failed: %s", exc)
        return 2
    log.info("test message delivered - check your Telegram")
    return 0


def _heartbeat(cfg: Config, side: str, bar_iso: str, source: str,
               strategy_label: str | None = None) -> None:
    """One liveness ping per trading day (and on the very first run), so a
    silent pipeline is impossible to miss. Failures never affect alerts."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return
    text = (f"🔎 monitoring live · {now_ist():%d %b %Y}\n"
            f"{cfg.symbol} side={side} · rule={strategy_label or cfg.strategy} · "
            f"last bar {bar_iso} · src={source}")
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text,
                      reply_markup=KEYBOARD)   # buttons arrive with the heartbeat
    except NotifyError as exc:
        log.warning("heartbeat failed (alerts unaffected): %s", exc)


def replay(cfg: Config, target: date, *, verbose: bool = False) -> list:
    """Walk one past session bar-by-bar and print every cross that would have
    fired. Sends nothing, writes no state - safe any day, any time.

    Uses the exact same evaluate() path as the live run, so what you see here
    is what you'd have been alerted on.
    """
    configure_logging(verbose, cfg.log_file)
    cutoff = datetime.combine(target, time(15, 35))     # everything that day is closed
    try:
        candles, source = fetch_candles(cfg, build_providers(cfg), cutoff)
    except ProviderError as exc:
        print(f"replay: no data ({exc})")
        return []

    day_bars = candles[candles.index.date == target]
    if day_bars.empty:
        print(f"replay: no bars on {target.isoformat()} "
              f"(weekend/holiday or feed gap) - try DATA_PROVIDER=yahoo")
        return []

    pre = candles[candles.index < day_bars.index[0]]
    # replay honours the same effective strategy as live runs (Telegram override)
    eff = effective_strategy(cfg, StateStore(cfg.state_file))
    strat = "ema20" if eff == "both" else eff       # replay walks the primary engine
    label = STRATEGY_LABELS.get(strat, strat)
    print(f"Replay {cfg.symbol} · {target.isoformat()} · {cfg.interval} "
          f"{label} · feed={source} · {len(day_bars)} bars that day"
          + ("  [strategy=both -> showing ema20]" if eff == "both" else ""))
    if len(pre) < (cfg.ema_len + 2 if strat == "ema20" else 72):
        print(f"insufficient warm-up before the session ({len(pre)} bars) - "
              f"try DATA_PROVIDER=yahoo (deeper history)")
        return []

    kwargs = dict(symbol=cfg.symbol, ema_len=cfg.ema_len, strategy=strat,
                  rsi_period=cfg.qqe_rsi_period, sf=cfg.qqe_sf,
                  factor=cfg.qqe_factor, source=source)
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

