"""STRATEGY toggle: default qqe, aliases, validation, active_strategies()."""

import pytest

from nse_alerts import config as config_mod
from nse_alerts.config import ConfigError, load_config
from tests.conftest import make_config


def _load(monkeypatch, **env):
    monkeypatch.setattr(config_mod, "load_dotenv", lambda *a, **k: None)
    for key in ("STRATEGY", "QQE_RSI_PERIOD", "QQE_SF", "QQE_FACTOR", "SYMBOLS",
                "ENVELOPE_LEN", "ENVELOPE_PERCENT", "ENVELOPE_EXPONENTIAL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_config()


def test_default_strategy_is_qqe(monkeypatch):
    cfg = _load(monkeypatch)
    assert cfg.strategy == "qqe"
    assert cfg.active_strategies() == ["qqe"]
    assert (cfg.qqe_rsi_period, cfg.qqe_sf, cfg.qqe_factor) == (14, 5, 4.238)


def test_ema_alias_and_both(monkeypatch):
    assert _load(monkeypatch, STRATEGY="ema").active_strategies() == ["ema20"]
    assert _load(monkeypatch, STRATEGY="EMA20").active_strategies() == ["ema20"]
    assert _load(monkeypatch, STRATEGY="both").active_strategies() == ["ema20", "qqe"]


def test_invalid_strategy_rejected(monkeypatch):
    with pytest.raises(ConfigError, match="STRATEGY"):
        _load(monkeypatch, STRATEGY="macd")


def test_qqe_params_overridable(monkeypatch):
    cfg = _load(monkeypatch, QQE_RSI_PERIOD="21", QQE_FACTOR="5.5")
    assert cfg.qqe_rsi_period == 21 and cfg.qqe_factor == 5.5
    with pytest.raises(ConfigError, match="QQE_FACTOR"):
        _load(monkeypatch, QQE_FACTOR="fast")


def test_state_keys_are_per_strategy():
    cfg = make_config(Path_tmp := __import__("pathlib").Path("."), strategy="both")
    assert cfg.active_strategies() == ["ema20", "qqe"]


# --- SYMBOLS watch list -------------------------------------------------------

def test_default_symbols_is_nifty_plus_crude_ng_off(monkeypatch):
    cfg = _load(monkeypatch)
    assert [w.key for w in cfg.watches] == ["NIFTY1!", "MCX:CRUDEOIL"]
    assert cfg.symbol == "NIFTY1!"                     # primary = first watch
    assert cfg.yahoo_symbol == "^NSEI"
    assert cfg.watches[0].strategy is None             # NIFTY follows STRATEGY
    assert cfg.watches[1].strategy == "env"            # crude runs Magic Envelope
    assert cfg.watches[1].exchange == "MCX"
    assert cfg.watches[1].yahoo_symbol == "BZ=F"        # Brent proxy built in
    assert cfg.watches[1].session is not None            # crude gated 17:00-22:00

    # re-enabling NG stays a pure config change:
    from nse_alerts.config import parse_watches
    assert parse_watches("NIFTY1!,MCX:CRUDEOIL,MCX:NATURALGAS")[2].yahoo_symbol == "NG=F"


def test_symbols_env_overrides(monkeypatch):
    cfg = _load(monkeypatch, SYMBOLS="NIFTY1!")
    assert [w.key for w in cfg.watches] == ["NIFTY1!"]


def test_parse_watches_explicit_forms():
    from nse_alerts.config import parse_watches

    ws = parse_watches(" NIFTY1!>^NSEI , MCX:GOLD>GC=F ")
    assert ws[0].key == "NIFTY1!" and ws[0].exchange == "NSE"
    assert ws[0].yahoo_symbol == "^NSEI"
    assert ws[1].key == "MCX:GOLD" and ws[1].exchange == "MCX"
    assert ws[1].yahoo_symbol == "GC=F" and ws[1].label == "GOLD"


def test_parse_watches_unknown_bare_symbol_rejected():
    from nse_alerts.config import parse_watches

    with pytest.raises(ConfigError, match="EXCHANGE:SYMBOL"):
        parse_watches("BANANA1!")


def test_per_watch_strategy_suffix_parsing():
    from nse_alerts.config import parse_watches

    ws = parse_watches("NIFTY1!,MCX:CRUDEOIL~env,MCX:GOLD~envelope>GC=F")
    assert ws[0].strategy is None
    assert ws[1].strategy == "env" and ws[1].key == "MCX:CRUDEOIL"   # suffix not in key
    assert ws[1].tv_symbol == "MCX:CRUDEOIL"                          # fetch symbol clean
    assert ws[2].strategy == "env" and ws[2].yahoo_symbol == "GC=F"   # alias + proxy combo

    with pytest.raises(ConfigError, match="per-watch strategy"):
        parse_watches("MCX:CRUDEOIL~macd")


def test_envelope_params_validated(monkeypatch):
    cfg = _load(monkeypatch, ENVELOPE_LEN="30", ENVELOPE_PERCENT="0.5",
                ENVELOPE_EXPONENTIAL="true")
    assert (cfg.envelope_len, cfg.envelope_percent, cfg.envelope_exponential) \
        == (30, 0.5, True)
    with pytest.raises(ConfigError, match="ENVELOPE_PERCENT"):
        _load(monkeypatch, ENVELOPE_PERCENT="0")
    with pytest.raises(ConfigError, match="ENVELOPE_EXPONENTIAL"):
        _load(monkeypatch, ENVELOPE_EXPONENTIAL="maybe")


def test_env_strategy_global_and_alias(monkeypatch):
    assert _load(monkeypatch, STRATEGY="env").active_strategies() == ["env"]
    assert _load(monkeypatch, STRATEGY="envelope").active_strategies() == ["env"]
    assert _load(monkeypatch, STRATEGY="magic").active_strategies() == ["env"]


def test_per_watch_session_window_parsing():
    from nse_alerts.config import parse_watches

    ws = parse_watches("NIFTY1!,MCX:CRUDEOIL~env@17:00-22:00")
    assert ws[0].session is None                          # NIFTY keeps NSE hours
    open_t, close_t, grace = ws[1].session                # crude gated 17:00-22:05
    assert (open_t.hour, open_t.minute, close_t.hour, close_t.minute) == (17, 0, 22, 0)
    assert (grace.hour, grace.minute) == (22, 5)
    assert ws[1].key == "MCX:CRUDEOIL"                    # suffix stays out of key
    assert ws[1].tv_symbol == "MCX:CRUDEOIL"              # fetch symbol clean
    assert ws[1].strategy == "env"                        # '~env' still applies

    with pytest.raises(ConfigError, match="HH:MM"):
        parse_watches("MCX:CRUDEOIL@5pm-10pm")
