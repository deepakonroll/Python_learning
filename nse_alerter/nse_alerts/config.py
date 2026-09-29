"""Environment-driven configuration for the NSE alerter.

Java equivalent: @ConfigurationProperties + application.yml - all tunables live
in one place (the .env file), the code only ever sees a typed Config object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# App root = the nse_alerter/ folder (parent of the nse_alerts/ package).
BASE_DIR = Path(__file__).resolve().parents[1]

# interval key -> minutes (the rule is 5m, others allowed for experimentation)
INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "1d": 1440,
}

DATA_PROVIDERS = ("auto", "tv", "yahoo", "kite")
STRATEGIES = ("qqe", "ema20", "both")          # qqe = default (QQE signals port)
STRATEGY_ALIASES = {"ema": "ema20", "qqe-signals": "qqe"}


def normalize_strategy(value: str) -> str | None:
    """Canonical strategy name, or None when invalid ('' -> None)."""
    canonical = STRATEGY_ALIASES.get(value, value)
    return canonical if canonical in STRATEGIES else None


class ConfigError(RuntimeError):
    """Invalid or missing configuration."""


@dataclass(frozen=True)
class Config:
    symbol: str                 # primary symbol + state key (e.g. NIFTY1!)
    yahoo_symbol: str           # spot proxy used by the yahoo fallback
    interval: str               # "5m"
    ema_len: int                # 20
    lookback_bars: int          # how many bars to fetch (EMA warm-up)
    data_provider: str          # auto | tv | yahoo | kite
    strategy: str               # qqe | ema20 | both  (active strategies)
    qqe_rsi_period: int
    qqe_sf: int
    qqe_factor: float
    telegram_token: str | None
    telegram_chat_id: str | None
    kite_api_key: str | None
    kite_access_token: str | None
    state_file: Path
    holidays: frozenset[date]
    log_file: Path | None

    def active_strategies(self) -> list[str]:
        """Which engines run this pass, primary first (heartbeat uses [0])."""
        if self.strategy == "both":
            return ["ema20", "qqe"]
        return [self.strategy]


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _resolve(path: Path) -> Path:
    """Relative paths resolve against the app folder, not the process CWD
    (Task Scheduler and cron start with a different working directory)."""
    return path if path.is_absolute() else (BASE_DIR / path)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name}={value} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not a number") from exc
    if value <= minimum:
        raise ConfigError(f"{name}={value} must be > {minimum}")
    return value


def _parse_holidays(raw: str) -> frozenset[date]:
    holidays: set[date] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            holidays.add(date.fromisoformat(chunk))
        except ValueError as exc:
            raise ConfigError(f"HOLIDAYS contains bad date {chunk!r} (want YYYY-MM-DD)") from exc
    return frozenset(holidays)


def load_config() -> Config:
    """Read .env (if present) then process environment. Existing env vars win,
    so cloud deployments can inject everything via secrets instead of a file."""
    load_dotenv(BASE_DIR / ".env", override=False)

    interval = _env("INTERVAL", "5m").lower()
    if interval not in INTERVAL_MINUTES:
        raise ConfigError(f"INTERVAL={interval!r} not one of {sorted(INTERVAL_MINUTES)}")

    provider = _env("DATA_PROVIDER", "auto").lower()
    if provider not in DATA_PROVIDERS:
        raise ConfigError(f"DATA_PROVIDER={provider!r} not one of {DATA_PROVIDERS}")

    strategy = _env("STRATEGY", "qqe").lower()          # default: QQE signals
    strategy = normalize_strategy(strategy)
    if strategy is None:
        raise ConfigError(f"STRATEGY={_env('STRATEGY')!r} not one of {STRATEGIES}")

    state_file = _resolve(Path(_env("STATE_FILE", str(BASE_DIR / "state.json"))))
    log_file = _env("LOG_FILE")
    log_file = _resolve(Path(log_file)) if log_file else None
    symbol = _env("SYMBOL", "NIFTY1!")
    if not symbol:
        raise ConfigError("SYMBOL must not be empty")

    return Config(
        symbol=symbol,
        yahoo_symbol=_env("YAHOO_SYMBOL", "^NSEI"),
        interval=interval,
        ema_len=_env_int("EMA_LEN", 20),
        lookback_bars=_env_int("LOOKBACK_BARS", 300, minimum=30),
        data_provider=provider,
        strategy=strategy,
        qqe_rsi_period=_env_int("QQE_RSI_PERIOD", 14),
        qqe_sf=_env_int("QQE_SF", 5),
        qqe_factor=_env_float("QQE_FACTOR", 4.238),
        telegram_token=_env("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=_env("TELEGRAM_CHAT_ID") or None,
        kite_api_key=_env("KITE_API_KEY") or None,
        kite_access_token=_env("KITE_ACCESS_TOKEN") or None,
        state_file=state_file,
        holidays=_parse_holidays(_env("HOLIDAYS")),
        log_file=Path(log_file) if log_file else None,
    )
