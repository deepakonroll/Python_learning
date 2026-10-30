"""Orchestration: gate -> fetch -> evaluate cross -> dedupe -> Telegram -> state.

Exit codes (visible in Task Scheduler's "Last Run Result"):
    0 = ok / skipped (outside session, no signal, or transient delivery failure)
    1 = configuration problem
    2 = (reserved - Telegram failures now skip the run, return 0)
    3 = (reserved - provider failures now skip the run, return 0)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, time, timedelta

from .config import (INTERVAL_MINUTES, TV_SYMBOL_OVERRIDES, Config,
                     ConfigError, strategies_for)
from .control import effective_strategy, process_commands
from .envelope import envelope_lines, envelope_prep
from .market_hours import IST, in_session, is_trading_day, now_ist, session_bounds
from .notify import NotifyError, ping_message, send_telegram
from .providers.base import DataProvider, ProviderError, completed_bars, filter_session
from .providers.kite import KiteProvider
from .providers.tv import TvProvider
from .providers.yahoo import YahooProvider
from .signals import (STRATEGY_LABELS, SignalError, evaluate, prep_flip,
                      prep_flip_message, prep_message)
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
        # Kite leads with exact front-month contracts; Yahoo is the only
        # fallback so alerts never silently switch to TradingView data.
        if not (cfg.kite_api_key and cfg.kite_access_token):
            log.warning("DATA_PROVIDER=kite but KITE_API_KEY/KITE_ACCESS_TOKEN "
                        "not set - kite fetches will fail and fall back")
        yahoo: list[DataProvider] = ([YahooProvider()]  # type: ignore[list-item]
                                     if watch.yahoo_symbol else [])
        return [KiteProvider(cfg.kite_api_key, cfg.kite_access_token),  # type: ignore[list-item]
                *yahoo]
    raise ConfigError(f"unknown DATA_PROVIDER {cfg.data_provider!r}")


def _free_chain(watch) -> list[DataProvider]:
    """TradingView futures -> yahoo spot proxy, ordered per watch (auto)."""
    tv: list[DataProvider] = [TvProvider()]              # type: ignore[list-item]
    yahoo: list[DataProvider] = ([YahooProvider()]       # type: ignore[list-item]
                                 if watch.yahoo_symbol else [])
    # MCX: anonymous TV access is blocked for MCX symbols, so the free
    # yahoo proxy leads there - UNLESS the watch has a TV override
    # (crude -> NYMEX:BZ1!, the continuous Brent futures - real-time):
    # yahoo's BZ=F measured +10 min stale (2026-10-06), so the fresh TV
    # symbol then leads and yahoo is only the fallback. NSE keeps
    # TradingView futures as primary.
    if watch.exchange == "MCX" and watch.key not in TV_SYMBOL_OVERRIDES:
        return yahoo + tv
    return tv + yahoo


def _stale_feed_reason(candles, interval_minutes: int, now: datetime) -> str | None:
    """Why the fetched frame looks like a lagging feed (or None when fresh).

    A current live feed's LAST row is the still-forming bar, or at worst the
    bar that just closed (a run landing exactly on a boundary). If the last
    row started MORE than one full interval before the current bar boundary,
    the feed is running behind: that row is still being formed upstream, so
    treating its OHLC as a 'completed' close produces alert prices that never
    appear on the chart (26 Oct: the alert quoted the 09:55 close as
    22,638.90, the real close was 22,724.70). The run must skip until the
    feed catches up - the state-machine dedupe then fires the real cross
    once, on the final bar, at most a bar late.
    """
    if candles is None or len(candles) == 0:
        return None
    last_start = candles.index[-1]
    if last_start + timedelta(minutes=interval_minutes) > now:
        return None                      # last row IS the current forming bar
    boundary = now.replace(minute=(now.minute // interval_minutes)
                           * interval_minutes, second=0, microsecond=0)
    if last_start >= boundary - timedelta(minutes=interval_minutes):
        return None                      # the just-closed bar - data is final
    age_min = max(0, int((now - last_start).total_seconds() // 60))
    return (f"feed {age_min} min behind {now:%H:%M} (last bar {last_start:%H:%M} "
            f"still forming upstream) - skip this run until it catches up")


def fetch_candles(cfg: Config, providers: list[DataProvider], now: datetime,
                  watch, lookback: int | None = None,
                  min_bars: int | None = None,
                  include_forming: bool = False,
                  check_freshness: bool | None = None):
    """Try each provider in order for this watch; returns (candles, name).

    Session filtering is centralized here (exchange-aware): each provider
    returns raw normalized candles, we keep only bars inside the watch's
    session window and drop the still-forming bar.

    lookback: bar budget (defaults to cfg.lookback_bars; replay passes a big
    number so past dates aren't truncated by the recent-bars tail).
    min_bars: warm-up floor (defaults to cfg.ema_len + 2).
    check_freshness: None -> cfg.freshness_guard (live runs); False disables
    it (replay, which deliberately fetches an old session).
    """
    naive_now = now.astimezone(IST).replace(tzinfo=None) if now.tzinfo else now
    budget = lookback or cfg.lookback_bars
    need = cfg.ema_len + 2 if min_bars is None else min_bars
    errors: list[str] = []
    for provider in providers:
        # yahoo reads the watch's proxy symbol, kite reads the raw watch
        # symbol (its own front-month picker maps MCX:CRUDEOIL to the real
        # INR contract; the TV override is TV-only), tv reads the watch
        # symbol or its override (crude fetches NYMEX:BZ1!, real-time)
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
            if (check_freshness if check_freshness is not None
                    else cfg.freshness_guard):
                stale = _stale_feed_reason(candles,
                                           INTERVAL_MINUTES[cfg.interval],
                                           naive_now)
                if stale:
                    raise ProviderError(stale)
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


class _RetrySignal(Exception):
    """Raised by _run_single / _run_consensus on Telegram send failure
    so the caller (run()) can return 0 and let the next scheduled run retry."""


def _run_single(cfg, store, states, watch, strats, candles, source,
                bar_iso, today, dry_run, now, forming):
    """Single-engine path (ema20 / qqe / env) — original loop logic."""
    watch_ok = False
    for index, strat in enumerate(strats):
        key = watch.key if strat == "ema20" else f"{watch.key}#{strat}"
        stored = states.get(key)
        if (stored is not None and stored.last_processed_bar
                and candles.index[-1]
                < datetime.fromisoformat(stored.last_processed_bar)):
            log.warning("stale window for %s (ends %s, stored %s) - skipped",
                        key, candles.index[-1], stored.last_processed_bar)
            watch_ok = True
            continue
        prev_side = stored.last_side if stored else None
        try:
            event, current_side = evaluate(
                candles, symbol=watch.key, ema_len=cfg.ema_len,
                prev_side=prev_side, source=source, strategy=strat,
                rsi_period=cfg.qqe_rsi_period, sf=cfg.qqe_sf,
                factor=cfg.qqe_factor, envelope_len=cfg.envelope_len,
                envelope_percent=cfg.envelope_percent,
                envelope_exponential=cfg.envelope_exponential)
        except SignalError as exc:
            log.error("signal evaluation failed (%s, %s): %s",
                      watch.key, strat, exc)
            break
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
                prep_bar = stored.prep_bar if stored else ""
                if cfg.prep_alerts:
                    forming_bar = (forming.index[-1].isoformat()
                                   if forming is not None else "")
                    if forming_bar and prep_bar == forming_bar:
                        # A threshold recross within the same live candle is
                        # still one event, even if the pending state flickered.
                        prep_fired = True
                    else:
                        was_fired = prep_fired
                        prep_fired = _prep_alert(
                            cfg, watch.key, candles, forming, current_side,
                            prep_fired, source, strategy=strat)
                        if prep_fired and not was_fired and forming_bar:
                            prep_bar = forming_bar
                store.put(key, SymbolState(
                    last_side=current_side, last_processed_bar=bar_iso,
                    last_event_side=stored.last_event_side if stored else "",
                    last_event_bar=stored.last_event_bar if stored else "",
                    history=stored.history if stored else [],
                    last_seen_date=today, prep_fired=prep_fired,
                    prep_bar=prep_bar))
                if new_day and index == 0:
                    _heartbeat(cfg, current_side, bar_iso, source,
                               strats[0], watch.key)
            continue
        text = event.message(
            now=now, interval_minutes=INTERVAL_MINUTES[cfg.interval],
            prep_confirmed=bool(stored and stored.prep_fired))
        if dry_run:
            log.info("[dry-run] would send:\n%s", text)
            continue
        try:
            assert cfg.telegram_token and cfg.telegram_chat_id
            send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
        except NotifyError as exc:
            log.error("telegram send failed (%s, will retry next run): %s",
                      watch.key, exc)
            raise _RetrySignal()
        state = stored or SymbolState(last_side=current_side)
        state.last_side = current_side
        state.last_processed_bar = bar_iso
        state.last_seen_date = today
        state.record_event(event.side, event.bar_time.isoformat())
        state.prep_fired = False
        state.prep_bar = ""
        store.put(key, state)
        log.info("sent %s alert [%s] for %s (bar %s)",
                 event.side, strat, watch.key,
                 event.bar_time.strftime("%H:%M"))
    return watch_ok


def _run_consensus(cfg, store, states, watch, strats, candles, source,
                   bar_iso, today, dry_run, now, forming, ok):
    """Consensus mode (~both): alert only when ALL engines agree on the
    same direction.  Individual engine sides are persisted for warm-up
    continuity and diagnosis; the #both key tracks the agreed direction."""
    watch_ok = False
    both_key = f"{watch.key}#both"
    consensus_stored = states.get(both_key)
    if (consensus_stored is not None and consensus_stored.last_processed_bar
            and candles.index[-1]
            < datetime.fromisoformat(consensus_stored.last_processed_bar)):
        log.warning("stale window for %s (ends %s, stored %s) - skipped",
                    both_key, candles.index[-1],
                    consensus_stored.last_processed_bar)
        return True
    consensus_prev = consensus_stored.last_side if consensus_stored else None
    engine_sides: dict[str, str] = {}
    engine_events: dict[str, object] = {}
    any_error = False
    for strat in strats:
        key = watch.key if strat == "ema20" else f"{watch.key}#{strat}"
        stored = states.get(key)
        prev_side = stored.last_side if stored else None
        try:
            event, current_side = evaluate(
                candles, symbol=watch.key, ema_len=cfg.ema_len,
                prev_side=prev_side, source=source, strategy=strat,
                rsi_period=cfg.qqe_rsi_period, sf=cfg.qqe_sf,
                factor=cfg.qqe_factor, envelope_len=cfg.envelope_len,
                envelope_percent=cfg.envelope_percent,
                envelope_exponential=cfg.envelope_exponential)
        except SignalError as exc:
            log.error("signal evaluation failed (%s, %s): %s",
                      watch.key, strat, exc)
            any_error = True
            break
        watch_ok = True
        engine_sides[strat] = current_side
        engine_events[strat] = event
        if not dry_run:
            store.put(key, SymbolState(
                last_side=current_side, last_processed_bar=bar_iso,
                last_event_side=(stored.last_event_side if stored else ""),
                last_event_bar=(stored.last_event_bar if stored else ""),
                history=(stored.history if stored else []),
                last_seen_date=today,
                prep_fired=(stored.prep_fired if stored else False),
                prep_bar=(stored.prep_bar if stored else "")))
            if stored is None:
                log.info("baseline: %s [%s] side=%s (no alert on first run)",
                         key, strat, current_side)
    if any_error or not engine_sides:
        return watch_ok
    return _consensus_decide(
        watch, strats, engine_sides, engine_events, candles, source,
        bar_iso, today, dry_run, store, states, cfg, consensus_stored,
        consensus_prev, both_key, watch_ok,
        now=now, forming=forming)


def _consensus_decide(watch, strats, engine_sides, engine_events, candles,
                      source, bar_iso, today, dry_run, store, states, cfg,
                      consensus_stored, consensus_prev, both_key, watch_ok,
                      now=None, forming=None):
    """Check engine agreement, fire consensus alert if all agree & direction changed."""
    from .signals import CrossEvent
    sides_set = set(engine_sides.values())
    consensus_event = None
    if len(sides_set) == 1:
        agreed_side = sides_set.pop()
        if consensus_prev is None:
            log.info("baseline: %s consensus side=%s (no alert on first run)",
                     both_key, agreed_side)
            consensus_cur = agreed_side
        elif agreed_side != consensus_prev:
            consensus_cur = agreed_side
            alert_side = "BUY" if consensus_cur == "UP" else "SELL"
            parts = []
            for strat in strats:
                ev = engine_events.get(strat)
                lbl = STRATEGY_LABELS.get(strat, strat)
                if ev and ev.rule:
                    parts.append(f"{lbl}: {ev.rule}")
                else:
                    parts.append(f"{lbl}: side={engine_sides[strat]}")
            combined_rule = " | ".join(parts)
            close = float(candles["Close"].iloc[-1])
            bar_time = candles.index[-1].to_pydatetime()
            consensus_event = CrossEvent(
                symbol=watch.key, side=alert_side,
                prev_side=consensus_prev, bar_time=bar_time,
                close=close, ema=0.0, source=source,
                strategy="both", rule=combined_rule)
        else:
            consensus_cur = agreed_side
            log.info("no consensus flip - %s side=%s bar=%s",
                     both_key, consensus_cur, bar_iso)
    else:
        consensus_cur = consensus_prev
        diag = ", ".join(f"{s}={engine_sides[s]}" for s in strats)
        if consensus_cur is None:
            log.info("engines disagree for %s (%s) - waiting for first agreement",
                     watch.key, diag)
        else:
            log.info("engines disagree for %s (%s) - consensus stays %s",
                     watch.key, diag, consensus_cur)

    if not dry_run:
        new_day = (consensus_stored is None
                   or consensus_stored.last_seen_date != today)
        # ⚠️ forming-bar heads-up for the consensus itself: when NO confirmed
        # flip fired this bar but the LIVE bar would flip the consensus if it
        # closed right now, send a one-per-episode PENDING alert first (same
        # idea as the single-engine prep). Re-arms when the pending flip
        # disappears; a real flip clears it too.
        prep_fired = (consensus_stored.prep_fired
                      if consensus_stored else False)
        prep_bar = consensus_stored.prep_bar if consensus_stored else ""
        if consensus_event is None and cfg.prep_alerts and forming is not None \
                and consensus_prev is not None:
            forming_bar = forming.index[-1].isoformat()
            live = float(forming["Close"].iloc[-1])
            if prep_bar == forming_bar:
                # Ignore a same-candle recross after its preview was sent.
                prep_fired = True
            else:
                pending = prep_flip(
                    candles, live, consensus_prev, "both",
                    ema_len=cfg.ema_len,
                    rsi_period=cfg.qqe_rsi_period,
                    sf=cfg.qqe_sf, factor=cfg.qqe_factor,
                    ema_buffer_pct=cfg.prep_ema_buffer_pct,
                    qqe_buffer=cfg.prep_qqe_buffer)
                if pending is None:
                    prep_fired = False
                elif not prep_fired:
                    direction, detail = pending
                    text = prep_flip_message(
                        watch.key, direction, live, detail,
                        forming.index[-1].to_pydatetime(), source,
                        consensus_prev, "both")
                    try:
                        assert cfg.telegram_token and cfg.telegram_chat_id
                        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
                    except NotifyError as exc:
                        log.warning("consensus prep alert failed for %s (%s) - "
                                    "will retry", watch.key, exc)
                    except AssertionError:
                        pass
                    else:
                        prep_fired = True
                        prep_bar = forming_bar
                        log.info("consensus prep alert sent for %s (%s live %.2f)",
                                 watch.key, direction, live)
        # Don't advance the consensus state before a confirmed alert is sent.
        # A failed send must leave the old consensus side in place for retry.
        if consensus_event is None and consensus_cur is not None:
            store.put(both_key, SymbolState(
                last_side=consensus_cur, last_processed_bar=bar_iso,
                last_event_side=(consensus_stored.last_event_side
                                 if consensus_stored else ""),
                last_event_bar=(consensus_stored.last_event_bar
                                if consensus_stored else ""),
                history=(consensus_stored.history if consensus_stored else []),
                last_seen_date=today, prep_fired=prep_fired,
                prep_bar=prep_bar))
            if new_day:
                _heartbeat(cfg, consensus_cur, bar_iso, source, "both", watch.key)

    if consensus_event is not None:
        text = consensus_event.message(
            now=now, interval_minutes=INTERVAL_MINUTES[cfg.interval],
            prep_confirmed=bool(consensus_stored and consensus_stored.prep_fired))
        if dry_run:
            log.info("[dry-run] would send:\n%s", text)
        else:
            try:
                assert cfg.telegram_token and cfg.telegram_chat_id
                send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
            except NotifyError as exc:
                log.error("telegram send failed (%s, will retry): %s",
                          watch.key, exc)
                raise _RetrySignal()
            assert consensus_cur is not None
            cs = consensus_stored or SymbolState(last_side=consensus_cur)
            cs.last_side = consensus_cur
            cs.last_processed_bar = bar_iso
            cs.last_seen_date = today
            cs.prep_fired = False
            cs.prep_bar = ""
            cs.record_event(consensus_event.side,
                            consensus_event.bar_time.isoformat())
            store.put(both_key, cs)
            if new_day:
                _heartbeat(cfg, consensus_cur, bar_iso, source, "both", watch.key)
            log.info("sent %s alert [both] for %s (bar %s)",
                     consensus_event.side, watch.key,
                     consensus_event.bar_time.strftime("%H:%M"))
    return watch_ok


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
    try:
        if not dry_run and not process_commands(cfg, store):
            log.info("alerts disabled via Telegram /disable - nothing to do")
            return 0
    except NotifyError as exc:
        # Telegram is down for polling - don't kill the run; alerts can still
        # be evaluated (sends will be retried on the next successful run).
        log.warning("Telegram command poll failed: %s - proceeding without it", exc)

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

    # Purge stale strategy keys (e.g. MCX:CRUDEOIL#env left over after a
    # ~env → ~both switch).  A key is stale when it matches a watch prefix
    # but doesn't correspond to any engine currently configured for that watch.
    # For ~both watches, the #both consensus key is also expected.
    _expected_keys: set[str] = set()
    for _w in cfg.watches:
        _eff = effective if override else (_w.strategy or effective)
        _strats = strategies_for(_eff)
        for _s in _strats:
            _expected_keys.add(_w.key if _s == "ema20" else f"{_w.key}#{_s}")
        if len(_strats) > 1:               # ~both → consensus key
            _expected_keys.add(f"{_w.key}#both")
    _stale = [k for k in states if k not in _expected_keys
              and any(k.startswith(_w.key) for _w in cfg.watches)]
    if _stale:
        _raw = store._load_raw()
        for _k in _stale:
            _raw.pop(_k, None)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        _tmp = store.path.with_suffix(".tmp")
        _tmp.write_text(json.dumps(_raw, indent=2), encoding="utf-8")
        os.replace(_tmp, store.path)
        states = store.load()          # reload after purge
        log.info("purged stale state keys: %s", ", ".join(_stale))

    today = now.date().isoformat()
    ok = failed = 0

    # 09:02-09:10 window: the ~09:05 world-markets snapshot (global index
    # closes vs prev day, GIFT Nifty, prior-session FII/DII) - once per trading
    # day (world_date). Every source is best-effort, so the send can't be
    # eaten by one dead feed. The 08:45-08:55 external-cron dispatches fall in
    # the pre-open quiet window (see the manual-note gate) and do nothing.
    if (not dry_run and time(9, 2) <= now.time() < time(9, 10)
            and is_trading_day(now.date(), cfg.holidays)
            and store.get_control().get("world_date") != today):
        if _world_update(cfg, now):
            store.set_control({**store.get_control(), "world_date": today})
        return 0

    # 09:17-09:25 window: the ~09:20 opening-levels snapshot - NIFTY/SENSEX
    # spot + India VIX + ATM straddle premiums (BS-indicative) - once per
    # trading day (levels_date). Best-effort like the world update: any
    # missing row is omitted, never fatal.
    if (not dry_run and time(9, 17) <= now.time() < time(9, 26)
            and is_trading_day(now.date(), cfg.holidays)
            and store.get_control().get("levels_date") != today):
        if _levels_update(cfg, now):
            store.set_control({**store.get_control(), "levels_date": today})
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
        is_consensus = len(strats) > 1          # ~both → consensus gate

        if is_consensus:
            try:
                watch_ok = _run_consensus(
                    cfg, store, states, watch, strats, candles, source,
                    bar_iso, today, dry_run, now, forming, ok)
            except _RetrySignal:
                # Telegram delivery failed - state NOT saved, so next run
                # retries the same bar.  Return 0 to keep the cron job green;
                # the warning log is still visible for diagnosis.
                return 0
        else:
            try:
                watch_ok = _run_single(
                    cfg, store, states, watch, strats, candles, source,
                    bar_iso, today, dry_run, now, forming)
            except _RetrySignal:
                # Telegram delivery failed - state NOT saved, so next run
                # retries the same bar.  Return 0 to keep the cron job green;
                # the warning log is still visible for diagnosis.
                return 0

        if watch_ok:
            ok += 1
        else:
            failed += 1

    if ok == 0 and failed > 0:
        # All providers failed (typically feed lag / freshness guard).  Skip
        # this run rather than hard-failing: the next scheduled run retries,
        # and the state-machine dedupe guarantees the cross fires at most one
        # bar late once the feed catches up.  Returning 0 keeps the cron job
        # green; the warning log is still visible for diagnosis.
        log.warning("no watch could be evaluated this run (%d failed) - skipped, will retry", failed)
        return 0
    if (ok == 0 and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and now.time() < session_bounds("MCX")[0]
            and not (time(8, 45) <= now.time() < time(9, 10))):  # pre-open quiet window
        # Pre-open manual runs reply with proof of life. Post-close runs and the
        # external cron's own dispatches (also workflow_dispatch!) stay silent -
        # otherwise every dead-tail tick would spam a note.
        _manual_note(cfg, effective, len(cfg.watches))
    return 0


def _prep_alert(cfg: Config, key: str, candles, forming, current_side: str,
                prep_fired: bool, source: str,
                strategy: str = "env") -> bool:
    """⚠️ forming-bar flip heads-up - one per approach episode.

    evaluate() stays the only path that moves state (a completed full-bar
    break); this only says the LIVE forming bar would flip if it closed right
    now, so the signal is seen BEFORE the bar closes (EMA20/QQE/both get the
    same heads-up the envelope already had). The episode re-arms when the
    pending flip disappears (prep_flip() returns None). Retuned: prep_fired.
    """
    if forming is None:
        return prep_fired
    live = float(forming["Close"].iloc[-1])
    if strategy == "env":
        # envelope band semantics - re-arm when a completed bar is back inside
        _, up_s, lo_s = envelope_lines(candles["Close"], cfg.envelope_len,
                                       cfg.envelope_percent,
                                       cfg.envelope_exponential)
        up, lo = float(up_s.iloc[-1]), float(lo_s.iloc[-1])
        if up == up and lo == lo and lo <= float(candles["Close"].iloc[-1]) <= up:
            prep_fired = False              # completed bar back inside: re-arm
        if prep_fired:
            return prep_fired
        got = envelope_prep(candles["Close"], live, current_side,
                            cfg.envelope_len, cfg.envelope_percent,
                            cfg.envelope_exponential)
        if got is None or not (cfg.telegram_token and cfg.telegram_chat_id):
            return prep_fired
        direction, level = got
        text = prep_message(key, direction, live, level, cfg.envelope_percent,
                            forming.index[-1].to_pydatetime(), source,
                            current_side)
    else:
        # ema20 / qqe / both: same contract, generic rule from signals.prep_flip
        got = prep_flip(candles, live, current_side, strategy,
                        ema_len=cfg.ema_len, rsi_period=cfg.qqe_rsi_period,
                        sf=cfg.qqe_sf, factor=cfg.qqe_factor,
                        ema_buffer_pct=cfg.prep_ema_buffer_pct,
                        qqe_buffer=cfg.prep_qqe_buffer)
        if got is None:
            return False                     # no pending flip right now
        direction, detail = got
        # the heads-up is only useful while the flip is still pending and the
        # completed side hasn't confirmed it - if the last completed bar has
        # actually crossed, evaluate() will fire the real alert and this
        # episode is over.
        if prep_fired or not (cfg.telegram_token and cfg.telegram_chat_id):
            return prep_fired
        text = prep_flip_message(key, direction, live, detail,
                                 forming.index[-1].to_pydatetime(), source,
                                 current_side, strategy)
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.warning("prep alert failed for %s (%s) - will retry", key, exc)
        return prep_fired
    log.info("prep alert sent for %s (%s live %.2f)", key, direction, live)
    return True


def _world_update(cfg: Config, now: datetime) -> bool:
    """09:05 morning snapshot: 7 index closes vs prev day + GIFT Nifty +
    prior-session FII/DII flows (nse_alerts/world.py). Best-effort fetches -
    a missing GIFT/FII-DII block is omitted, never fatal. True = sent (the
    caller records world_date; a failed send retries on the next tick)."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    from nse_alerts import world
    rows = world.fetch_yahoo_rows()
    text = world.build_message(
        rows,
        gift=world.fetch_gift(),
        fiidii=world.fetch_fii_dii(),
    )
    if not text:
        log.warning("world update: no Yahoo rows (%d) - skipping send", len(rows))
        return False
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.error("world update failed (%s) - next tick retries", exc)
        return False
    log.info("world update sent (%s; %d indices)", now.strftime("%a"), len(rows))
    return True


def _levels_update(cfg: Config, now: datetime) -> bool:
    """09:20 opening-levels snapshot: NIFTY/SENSEX spot + India VIX + ATM
    straddle premiums (nse_alerts/snapshot.py). Best-effort - a missing row
    is omitted, never fatal. True = sent (the caller records levels_date; a
    failed send retries on the next tick)."""
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    from nse_alerts import snapshot
    text = snapshot.build_message(snapshot.fetch_levels(), now)
    if not text:
        log.warning("levels update: no spot data - skipping send")
        return False
    try:
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    except NotifyError as exc:
        log.error("levels update failed (%s) - next tick retries", exc)
        return False
    log.info("levels update sent (%s)", now.strftime("%a"))
    return True


def _trend_alerts(cfg: Config, store: StateStore, candles, now: datetime,
                  today: str) -> None:
    """Nifty distance-from-open tripwires (Nifty drives ALL days, Sensex
    included - per the trading plan). ⚔️ 0.45% ~= the 2-strike zone: no
    averaging. 🔴 0.8% = trend day: pure day-status heads-up - the entries
    themselves are NEVER gated (every consensus signal is delivered; a trend
    day just tells you the day is a strong one). Thresholds tuned on 60d of
    NIFTY 5m (3/3 big days caught). One fire per level per day; a failed send
    retries next run; failures never touch signal state."""
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
                 "flatten path · stops live · exits per plan"):
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

    Uses the same evaluate() path and watch window as the live run. For a
    windowed watch, the first completed bar before the window is included to
    model the side the first live scan observes.
    """
    configure_logging(verbose, cfg.log_file)
    watch = cfg.watches[0]
    # cutoff = that day's session grace-end, so every bar that day is closed
    cutoff = datetime.combine(target, session_bounds(watch.exchange)[2])
    try:
        # big lookback: replay needs the past date inside the fetched window
        candles, source = fetch_candles(cfg, build_providers(cfg, watch),
                                        cutoff, watch, lookback=5000,
                                        check_freshness=False)
    except ProviderError as exc:
        print(f"replay: no data ({exc})")
        return []

    day_bars = candles[candles.index.date == target]
    if day_bars.empty:
        print(f"replay: no bars on {target.isoformat()} "
              f"(weekend/holiday or feed gap) - try DATA_PROVIDER=yahoo")
        return []

    session_open, session_close, _ = watch.session or session_bounds(watch.exchange)
    day_times = day_bars.index.time
    before_open = day_bars[[bar_time < session_open for bar_time in day_times]].tail(1)
    in_window = day_bars[[session_open <= bar_time < session_close
                          for bar_time in day_times]]
    replay_index = before_open.index.append(in_window.index).sort_values().unique()
    replay_bars = day_bars.loc[replay_index]

    # Live runs retain the side from the last bar evaluated in the prior
    # watch window. Ignore later bars outside that window when choosing replay
    # state, but keep the earlier candles needed to warm up the indicators.
    prior_bars = candles[candles.index.date < target]
    prior_times = prior_bars.index.time
    prior_window = prior_bars[[session_open <= bar_time < session_close
                               for bar_time in prior_times]]
    if prior_window.empty:
        pre = prior_bars
    else:
        pre = candles[candles.index <= prior_window.index[-1]]

    replay_positions = [candles.index.get_loc(bar_time)
                        for bar_time in replay_bars.index]
    # strategy precedence mirrors live: Telegram override > ~suffix > config
    control = StateStore(cfg.state_file).get_control()
    eff = control.get("strategy") or watch.strategy or cfg.strategy
    strats = strategies_for(eff)

    all_events: list = []
    is_consensus = len(strats) > 1

    if is_consensus:
        # Consensus replay: walk both engines bar-by-bar, alert only
        # when ALL engines agree and the agreed direction changes.
        print(f"Replay {watch.key} · {target.isoformat()} · {cfg.interval} "
              f"both (consensus) · feed={source} · {len(replay_bars)} bars "
              f"evaluated in {session_open:%H:%M}-{session_close:%H:%M} window")
        for strat in strats:
            if strat == "env":
                min_pre = cfg.envelope_len + 2
            elif strat == "ema20":
                min_pre = cfg.ema_len + 2
            else:
                min_pre = 72
            if len(pre) < min_pre:
                print(f"insufficient warm-up before the session ({len(pre)} bars) - "
                      f"try DATA_PROVIDER=yahoo (deeper history)")
                return []

        engine_prevs: dict[str, str] = {}
        for strat in strats:
            kwargs = dict(symbol=watch.key, ema_len=cfg.ema_len, strategy=strat,
                          rsi_period=cfg.qqe_rsi_period, sf=cfg.qqe_sf,
                          factor=cfg.qqe_factor, source=source,
                          envelope_len=cfg.envelope_len,
                          envelope_percent=cfg.envelope_percent,
                          envelope_exponential=cfg.envelope_exponential)
            _, entry = evaluate(pre, prev_side=None, **kwargs)
            engine_prevs[strat] = entry
            print(f"  {STRATEGY_LABELS.get(strat, strat)} entering side: {entry}")

        agreed_pre = set(engine_prevs.values())
        consensus_prev: str | None = (next(iter(agreed_pre))
                                      if len(agreed_pre) == 1 else None)
        for i in replay_positions:
            engine_sides: dict[str, str] = {}
            for strat in strats:
                kwargs = dict(symbol=watch.key, ema_len=cfg.ema_len,
                              strategy=strat, rsi_period=cfg.qqe_rsi_period,
                              sf=cfg.qqe_sf, factor=cfg.qqe_factor,
                              source=source, envelope_len=cfg.envelope_len,
                              envelope_percent=cfg.envelope_percent,
                              envelope_exponential=cfg.envelope_exponential)
                _, side = evaluate(candles.iloc[:i + 1],
                                   prev_side=engine_prevs[strat], **kwargs)
                engine_prevs[strat] = side
                engine_sides[strat] = side

            sides_set = set(engine_sides.values())
            if len(sides_set) == 1:
                agreed = sides_set.pop()
                if consensus_prev is not None and agreed != consensus_prev:
                    alert = "BUY" if agreed == "UP" else "SELL"
                    bar_t = candles.index[i].to_pydatetime()
                    diag = ", ".join(
                        f"{STRATEGY_LABELS.get(s, s)}={engine_sides[s]}"
                        for s in strats)
                    from .signals import CrossEvent
                    ev = CrossEvent(
                        symbol=watch.key, side=alert,
                        prev_side=consensus_prev, bar_time=bar_t,
                        close=float(candles["Close"].iloc[i]),
                        ema=0.0, source=source, strategy="both",
                        rule=f"consensus {alert}: {diag}")
                    all_events.append(ev)
                    print(f"  {bar_t:%H:%M}  {alert:<4}  {ev.rule}")
                consensus_prev = agreed
            else:
                diag = ", ".join(f"{s}={engine_sides[s]}" for s in strats)
                # engines disagree - consensus stays, no alert
                if consensus_prev is None:
                    # first bar with disagreement - no baseline yet
                    pass

        print(f"-> {len(all_events)} consensus cross(es) that day")
    else:
        for strat in strats:
            label = STRATEGY_LABELS.get(strat, strat)
            print(f"Replay {watch.key} · {target.isoformat()} · {cfg.interval} "
                  f"{label} · feed={source} · {len(replay_bars)} bars "
                  f"evaluated in {session_open:%H:%M}-{session_close:%H:%M} window")
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

            events, prev = [], entry
            for i in replay_positions:
                event, prev = evaluate(candles.iloc[:i + 1], prev_side=prev, **kwargs)
                if event:
                    events.append(event)
                    print(f"  {event.bar_time:%H:%M}  {event.side:<4}  {event.rule}")

            print(f"-> {len(events)} cross(es) that day · ending side {prev}")
            all_events.extend(events)

    # chronological order when both engines fire on the same day
    all_events.sort(key=lambda e: e.bar_time)
    return all_events

