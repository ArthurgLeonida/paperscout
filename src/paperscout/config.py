"""Runtime settings loaded from environment variables and optional .env files.

Secrets (S2_API_KEY) are held only in memory and are never logged or written
to the database. ``Settings.redacted()`` is the only loggable view.

.env lookup order (first value wins; real environment variables always win):
1. ``.env`` in the current working directory
2. ``.env`` in the paperscout project root (when running from a source checkout)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

S2_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
S2_RECS_BASE = "https://api.semanticscholar.org/recommendations/v1"
S2_KEY_FORM = "https://www.semanticscholar.org/product/api#api-key-form"
ARXIV_API = "https://export.arxiv.org/api/query"
# arXiv API terms of use: "no more than one request every three seconds".
ARXIV_MIN_INTERVAL = 3.0
PROVIDERS = ("auto", "arxiv", "semantic-scholar")


def normalize_provider(value: str) -> str:
    key = value.strip().lower()
    key = {"s2": "semantic-scholar", "semanticscholar": "semantic-scholar"}.get(key, key)
    if key not in PROVIDERS:
        raise ValueError(f"Unknown provider {value!r}; use one of: {', '.join(PROVIDERS)}")
    return key


def load_dotenv(path: Path) -> list[str]:
    """Minimal .env loader. Returns the names it set; never overrides existing variables.

    Supports ``KEY=value``, ``export KEY=value``, quoted values, ``#`` comment
    lines, trailing `` # comments`` on unquoted values, and a UTF-8 BOM
    (as written by some Windows editors). Empty values are ignored.
    """
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def project_root() -> Path | None:
    """The source checkout root when paperscout runs from one (editable install)."""
    root = Path(__file__).resolve().parents[2]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and 'name = "paperscout"' in pyproject.read_text(encoding="utf-8"):
        return root
    return None


def env_candidates(cwd: Path) -> list[Path]:
    paths = [cwd / ".env"]
    root = project_root()
    if root is not None and (root / ".env").resolve() != (cwd / ".env").resolve():
        paths.append(root / ".env")
    return paths


def profile_dir(cwd: Path | None = None) -> Path:
    """Where /setup keeps the private research background (git-ignored ``profile/``)."""
    local = (cwd or Path.cwd()) / "profile"
    root = project_root()
    if not local.is_dir() and root is not None:
        return root / "profile"
    return local


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
    s2_max_retries: int = 3
    s2_timeout: float = 30.0
    s2_max_backoff: float = 60.0
    s2_max_requests: int = 40  # hard cap on HTTP attempts per command (retries included)
    recent_days: int = 90
    provider: str = "auto"  # auto | arxiv | semantic-scholar
    arxiv_base: str = ARXIV_API
    arxiv_min_interval: float = ARXIV_MIN_INTERVAL
    arxiv_max_retries: int = 2
    arxiv_max_requests: int = 10
    cache_ttl: CacheTTL = field(default_factory=CacheTTL)
    key_source: str = "not set"  # "environment" | ".env: <path>" | "not set"
    env_files_loaded: tuple[str, ...] = ()

    @property
    def effective_provider(self) -> str:
        """``auto`` means Semantic Scholar when a key is configured, otherwise arXiv."""
        if self.provider != "auto":
            return self.provider
        return "semantic-scholar" if self.s2_api_key else "arxiv"

    @property
    def db_path(self) -> Path:
        return self.home / "paperscout.sqlite3"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @property
    def notes_dir(self) -> Path:
        return self.home / "notes"

    def _key_status(self) -> str:
        if not self.s2_api_key:
            return "not set"
        return "set" if self.key_source == "not set" else f"set ({self.key_source})"

    def redacted(self) -> dict:
        return {
            "home": str(self.home),
            "s2_api_key": self._key_status(),
            "env_files_loaded": list(self.env_files_loaded),
            "s2_graph_base": self.s2_graph_base,
            "s2_recs_base": self.s2_recs_base,
            "s2_min_interval": self.s2_min_interval,
            "s2_max_retries": self.s2_max_retries,
            "s2_max_requests": self.s2_max_requests,
            "recent_days": self.recent_days,
            "provider": f"{self.effective_provider} (setting: {self.provider})",
            "arxiv_min_interval": self.arxiv_min_interval,
            "arxiv_max_requests": self.arxiv_max_requests,
        }


def load_settings(cwd: Path | None = None) -> Settings:
    cwd = cwd or Path.cwd()
    key_in_env = bool(os.environ.get("S2_API_KEY"))
    loaded_files, key_file = [], None
    for path in env_candidates(cwd):
        names = load_dotenv(path)
        if path.is_file():
            loaded_files.append(str(path))
        if "S2_API_KEY" in names:
            key_file = str(path)
    key = os.environ.get("S2_API_KEY") or None
    source = "environment" if key_in_env else (f".env: {key_file}" if key_file else "not set")
    home_env = os.environ.get("PAPERSCOUT_HOME")
    home = Path(home_env).expanduser().resolve() if home_env else default_home()
    # Without a key, requests share a frequently throttled pool: fail fast instead of hammering it.
    default_retries = 3 if key else 2
    return Settings(
        home=home,
        s2_api_key=key,
        s2_min_interval=_float("PAPERSCOUT_S2_MIN_INTERVAL", 1.1),
        s2_max_retries=_int("PAPERSCOUT_S2_MAX_RETRIES", default_retries),
        s2_timeout=_float("PAPERSCOUT_S2_TIMEOUT", 30.0),
        s2_max_requests=_int("PAPERSCOUT_S2_MAX_REQUESTS", 40),
        recent_days=_int("PAPERSCOUT_RECENT_DAYS", 90),
        provider=normalize_provider(os.environ.get("PAPERSCOUT_PROVIDER", "auto")),
        arxiv_min_interval=max(ARXIV_MIN_INTERVAL, _float("PAPERSCOUT_ARXIV_MIN_INTERVAL", ARXIV_MIN_INTERVAL)),
        arxiv_max_requests=_int("PAPERSCOUT_ARXIV_MAX_REQUESTS", 10),
        key_source=source if key else "not set",
        env_files_loaded=tuple(loaded_files),
    )
