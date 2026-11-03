"""The researcher background that /setup builds (private, git-ignored ``profile/``).

Prose files are read by Claude (loaded every session through ``CLAUDE.local.md``);
``profile.json`` is the machine-readable part the deterministic ranking uses.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from paperscout.config import profile_dir, project_root

PROSE_FILES = (
    "01-researcher-background.md",
    "02-research-goals.md",
    "03-reading-style.md",
    "04-recommendation-criteria.md",
)
PROFILE_JSON = "profile.json"
# Unfilled template tokens such as [YOUR_NAME] or [PROJECT_TITLE].
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]{2,}\]")


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def inspect_background(cwd: Path | None = None) -> dict[str, Any]:
    """What exists, what is still a template, and whether Claude Code will load it."""
    directory = profile_dir(cwd)
    files = {}
    for name in (*PROSE_FILES, PROFILE_JSON):
        path = directory / name
        if not path.is_file():
            files[name] = "missing"
            continue
        text = path.read_text(encoding="utf-8")
        left = len(set(PLACEHOLDER.findall(text)))
        files[name] = f"{left} placeholder(s) left" if left else "filled"
    root = (cwd or Path.cwd()) if (cwd or Path.cwd()).joinpath("CLAUDE.md").is_file() else (project_root() or Path.cwd())
    local = root / "CLAUDE.local.md"
    imports = local.is_file() and "@profile/" in local.read_text(encoding="utf-8")
    json_path = directory / PROFILE_JSON
    return {
        "profile_dir": str(directory),
        "files": files,
        "claude_local_md": "imports profile/" if imports else ("present without profile imports" if local.is_file() else "missing"),
        "profile_json_hash": file_hash(json_path.read_text(encoding="utf-8")) if json_path.is_file() else None,
    }
