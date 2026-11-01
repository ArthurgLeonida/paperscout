"""Runtime settings loaded from environment variables (and an optional .env file).

Secrets (S2_API_KEY) are held only in memory and are never logged or written
to the database. See ``Settings.redacted()`` for the loggable view.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

S2_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
S2_RECS_BASE = "https://api.semanticscholar.org/recommendations/v1"


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines; never overrides the real environment."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def default_home() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "paperscout"
    return Path.home() / ".local" / "share" / "paperscout"


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class CacheTTL:
    """Response-cache lifetimes in seconds, per endpoint family."""

    search: int = 24 * 3600
    paper: int = 7 * 24 * 3600
    recommendations: int = 24 * 3600
    references: int = 7 * 24 * 3600


@dataclass(frozen=True)
class Settings:
    home: Path
    s2_api_key: str | None = None
    s2_graph_base: str = S2_GRAPH_BASE
    s2_recs_base: str = S2_RECS_BASE
    s2_min_interval: float = 1.1
    s2_max_retries: int = 4
    s2_timeout: float = 30.0
    s2_max_backoff: float = 60.0
    recent_days: int = 90
    cache_ttl: CacheTTL = field(default_factory=CacheTTL)

    @property
    def db_path(self) -> Path:
        return self.home / "paperscout.sqlite3"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @property
    def notes_dir(self) -> Path:
        return self.home / "notes"

    def redacted(self) -> dict:
        return {
            "home": str(self.home),
            "s2_api_key": "set" if self.s2_api_key else "not set",
            "s2_graph_base": self.s2_graph_base,
            "s2_recs_base": self.s2_recs_base,
            "s2_min_interval": self.s2_min_interval,
            "s2_max_retries": self.s2_max_retries,
            "recent_days": self.recent_days,
        }

    def with_home(self, home: Path) -> "Settings":
        return replace(self, home=home)


def load_settings(cwd: Path | None = None) -> Settings:
    load_dotenv((cwd or Path.cwd()) / ".env")
    home_env = os.environ.get("PAPERSCOUT_HOME")
    home = Path(home_env).expanduser().resolve() if home_env else default_home()
    return Settings(
        home=home,
        s2_api_key=os.environ.get("S2_API_KEY") or None,
        s2_min_interval=_float("PAPERSCOUT_S2_MIN_INTERVAL", 1.1),
        s2_max_retries=_int("PAPERSCOUT_S2_MAX_RETRIES", 4),
        s2_timeout=_float("PAPERSCOUT_S2_TIMEOUT", 30.0),
        recent_days=_int("PAPERSCOUT_RECENT_DAYS", 90),
    )
