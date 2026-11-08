"""Save the top papers of a ranked list as arXiv PDFs plus a Markdown reading list.

arXiv's terms allow retrieving and storing e-prints "for your own personal use,
or for research purposes", but not serving or redistributing them, so PDFs go
to a local folder only, one request every three seconds, through
export.arxiv.org. Papers without an arXiv ID are listed with their links, never
fetched from other sites. Titles are untrusted text, so file names are built
from a sanitised copy.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from paperscout.http import ApiError, ApiRateLimited
from paperscout.models import CRITERIA, HeuristicAssessment, Priority, Provenance, RankedPaper, utcnow
from paperscout.reports import criterion_line, effort_text, paper_date, sanitize_untrusted
from paperscout.service import Paperscout, ServiceError

LIST_KINDS = ("recent", "discover", "authors")
MAX_TOP = 50
INDEX_NAME = "reading-list.md"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
PERSONAL_USE = ("These PDFs are arXiv e-prints saved for your personal use. arXiv's terms don't allow "
                "redistributing them, so share the abstract links instead.")


class DownloadEntry(BaseModel):
    rank: int
    paper_id: str
    title: str
    score: int | None
    priority: str
    arxiv_id: str | None
    link: str
    file: str | None = None
    status: str  # planned | saved | already saved | not on arXiv | failed | not attempted
    detail: str | None = None


class DownloadResult(BaseModel):
    run_id: str
    heading: str
    destination: str
    dry_run: bool
    judged: bool
    entries: list[DownloadEntry] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for e in self.entries if e.status == status)


def safe_name(text: str, limit: int) -> str:
    """A Windows- and Android-safe file name fragment from untrusted text."""
    text = _UNSAFE.sub(" ", sanitize_untrusted(text))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text.strip(" .") or "untitled"


def _source_run(svc: Paperscout, run_id: str | None, folder: str | None) -> dict:
    if run_id and folder:
        raise ServiceError("Use either --run or --folder, not both.")
    if run_id:
        try:
            return svc.store.get_run(run_id)
        except LookupError as exc:
            raise ServiceError(str(exc)) from None
    if folder:
        found = svc.get_folder(folder)
        run = svc.store.latest_run(("rank-folder",), query=found.name)
        if run is None:
            raise ServiceError(f'Local folder "{found.name}" has not been ranked yet. Run /rank "{found.name}" '
                               "first, then download.")
        return run
    run = svc.store.latest_run(LIST_KINDS)
    if run is None:
        raise ServiceError("No /discover, /recent or /authors list yet. Run one first, or pass --folder or --run.")
    return run


def _ranked_list(svc: Paperscout, run: dict) -> list[RankedPaper]:
    """The run's papers in the order the user saw them: its shortlist first, then the rest by position."""
    params = json.loads(run["params_json"])
    rows = svc.store.run_candidates(run["id"])
    papers = svc.store.get_papers(r["paper_id"] for r in rows)
    items = {
        r["paper_id"]: RankedPaper(
            paper=papers[r["paper_id"]],
            provenance=[Provenance.model_validate(p) for p in json.loads(r["provenance"])],
            assessment=HeuristicAssessment.model_validate_json(r["assessment"]),
        )
        for r in rows if r["paper_id"] in papers
    }
    shortlist = [pid for pid in params.get("shortlist", []) if pid in items]
    rest = [r["paper_id"] for r in rows if r["paper_id"] in items and r["paper_id"] not in shortlist]
    return [items[pid] for pid in shortlist + rest if items[pid].assessment.priority is not Priority.skip]


def file_name(rank: int, item: RankedPaper) -> str:
    score = f"{item.assessment.score:02d}" if item.assessment.score is not None else "--"
    arxiv = item.paper.arxiv_id.replace("/", "_")
    return f"{rank:02d} - {score} - {safe_name(item.paper.title, 70)} ({arxiv}).pdf"


def download_list(
    svc: Paperscout,
    *,
    top: int = 10,
    run_id: str | None = None,
    folder: str | None = None,
    out: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> DownloadResult:
    if not 1 <= top <= MAX_TOP:
        raise ServiceError(f"--top must be between 1 and {MAX_TOP}.")
    run = _source_run(svc, run_id, folder)
    params = json.loads(run["params_json"])
    heading = (params.get("report") or {}).get("heading") or f"{run['kind']}: {run['query'] or 'list'}"
    items = _ranked_list(svc, run)[:top]
    if not items:
        raise ServiceError(f"Run {run['id']} has no papers to download.")
    started = datetime.fromisoformat(run["started_at"]).date().isoformat()
    title = heading.replace(", with judge ratings", "").replace(": ", " - ")
    folder_name = safe_name(f"{started} {title}", 90)
    destination = (out or svc.settings.downloads_dir) / folder_name
    judged = any(c.judged for item in items for c in item.assessment.criteria.values())
    result = DownloadResult(run_id=run["id"], heading=heading, destination=str(destination), dry_run=dry_run,
                            judged=judged)
    for rank, item in enumerate(items, 1):
        paper = item.paper
        entry = DownloadEntry(rank=rank, paper_id=paper.paper_id, title=sanitize_untrusted(paper.title),
                              score=item.assessment.score, priority=item.assessment.priority.value,
                              arxiv_id=paper.arxiv_id, link=paper.link, status="planned")
        if paper.arxiv_id:
            entry.file = file_name(rank, item)
            if (destination / entry.file).is_file():
                entry.status = "already saved"
        else:
            entry.status = "not on arXiv"
            entry.detail = "only arXiv e-prints are downloaded; open the link"
        result.entries.append(entry)
    if not judged:
        result.notices.append("This list has computed criteria only (no judge ratings), so its order may differ "
                              "from a judged ranking.")
    if dry_run:
        return result

    destination.mkdir(parents=True, exist_ok=True)
    max_bytes = svc.settings.download_max_mb * 2**20
    stopped = None
    for entry in result.entries:
        if entry.status != "planned":
            continue
        if stopped:
            entry.status, entry.detail = "not attempted", stopped
            continue
        if progress:
            progress(f"[{entry.rank}/{len(result.entries)}] arXiv {entry.arxiv_id}: {entry.title[:70]}")
        try:
            size = svc.arxiv.download_pdf(entry.arxiv_id, destination / entry.file, max_bytes)
            entry.status, entry.detail = "saved", f"{size / 2**20:.1f} MB"
        except ApiRateLimited as exc:
            entry.status, entry.detail = "failed", str(exc)
            stopped = "stopped after arXiv asked to slow down; run the same command later to fetch the rest"
        except ApiError as exc:
            entry.status, entry.detail = "failed", str(exc)
    (destination / INDEX_NAME).write_text(render_index(result, items, svc.config.version), encoding="utf-8")
    svc.store.audit("paper.download", f"local folder {destination}", run["id"], False,
                    f"{result.count('saved')} saved",
                    {"top": top, **{s: result.count(s) for s in ("saved", "already saved", "not on arXiv",
                                                                 "failed", "not attempted")}})
    return result


def render_index(result: DownloadResult, items: list[RankedPaper], version: str) -> str:
    """reading-list.md: the list with scores, reasons and links, saved next to the PDFs."""
    lines = [
        f"# {sanitize_untrusted(result.heading)}",
        "",
        f"Saved {utcnow():%Y-%m-%d} from paperscout run `{result.run_id}` ({version}). Scores order a reading list: "
        "they aren't probabilities or quality measures, and differences under about 10 points mean little. "
        + ("Criteria marked (judge) were rated by Claude subagents from the abstracts."
           if result.judged else "Every criterion was computed (no judge ratings)."),
        "",
        f"_{PERSONAL_USE}_",
        "",
        "| # | Score | Priority | Read | Paper | Link | File |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry, item in zip(result.entries, items):
        a = item.assessment
        read = f"{a.reading_depth.value}, {effort_text(a.effort_minutes)}"
        title = entry.title.replace("|", "/")
        file = f"`{entry.file}`" if entry.status in ("saved", "already saved") else entry.status
        lines.append(f"| {entry.rank} | {entry.score if entry.score is not None else '?'} | {entry.priority} | {read} "
                     f"| {title} ({item.paper.year or 'n.d.'}) | {entry.link} | {file} |")
    lines.append("")
    for entry, item in zip(result.entries, items):
        a, p = item.assessment, item.paper
        lines += [
            f"## {entry.rank}. {entry.title}",
            f"{sanitize_untrusted(p.short_authors())} · {sanitize_untrusted(p.venue) or 'venue unknown'} · "
            f"{paper_date(p)} · {entry.link}",
            "",
            f"- Score {entry.score if entry.score is not None else 'unknown'}/100 · {a.priority.value} · "
            f"role: {a.reading_role.value} · depth: {a.reading_depth.value} ({effort_text(a.effort_minutes)})",
        ]
        lines += [criterion_line(name, a.criteria[name]) for name in CRITERIA
                  if name in a.criteria and a.criteria[name].value is not None]
        if a.background_note:
            lines.append(f"- Your background: {a.background_note}")
        lines.append(f"- Next action: {a.next_action}")
        lines.append(f"- File: {entry.file if entry.status in ('saved', 'already saved') else entry.status}"
                     + (f" ({entry.detail})" if entry.detail and entry.status != "saved" else ""))
        lines.append("")
    return "\n".join(lines)


def render_download(result: DownloadResult) -> str:
    if result.dry_run:
        lines = [f"Dry run: would save {result.count('planned')} PDF(s) to {result.destination} "
                 f"({result.count('already saved')} already there, {result.count('not on arXiv')} not on arXiv). "
                 "Nothing was downloaded."]
    else:
        lines = [f"Saved {result.count('saved')} PDF(s) to {result.destination}",
                 f"({result.count('already saved')} already there, {result.count('not on arXiv')} not on arXiv, "
                 f"{result.count('failed')} failed, {result.count('not attempted')} not attempted).",
                 f"Reading list with scores, reasons and links: {Path(result.destination) / INDEX_NAME}"]
    lines += [f"List: {sanitize_untrusted(result.heading)} (run `{result.run_id}`)", ""]
    lines += [f"- {n}" for n in result.notices] + ([""] if result.notices else [])
    lines += ["| # | Score | Priority | Paper | arXiv | Status |", "|---|---|---|---|---|---|"]
    for e in result.entries:
        status = e.status + (f": {e.detail}" if e.detail and e.status in ("failed", "not attempted") else "")
        lines.append(f"| {e.rank} | {e.score if e.score is not None else '?'} | {e.priority} | "
                     f"{e.title.replace('|', '/')[:90]} | {e.arxiv_id or e.link} | {status} |")
    lines += ["", PERSONAL_USE]
    return "\n".join(lines)
