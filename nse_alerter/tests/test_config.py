"""STRATEGY toggle: default qqe, aliases, validation, active_strategies()."""

import pytest

from nse_alerts import config as config_mod
from nse_alerts.config import ConfigError, load_config
from tests.conftest import make_config


def _load(monkeypatch, **env):
    monkeypatch.setattr(config_mod, "load_dotenv", lambda *a, **k: None)
    for key in ("STRATEGY", "QQE_RSI_PERIOD", "QQE_SF", "QQE_FACTOR"):
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
