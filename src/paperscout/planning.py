"""Reading plans and reading-note scaffolds.

Time estimates are planning heuristics from the ranking config, not
predictions. Note scaffolds contain prompts and metadata only; they never
pre-fill an "understanding" of the paper.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from paperscout.models import Candidate, FeedbackLabel, Paper, Priority, Provenance, RankedPaper, ReadingDepth, ReadingRole
from paperscout.reports import effort_text, paper_date, sanitize_untrusted
from paperscout.service import PRIORITY_ORDER, Paperscout, ServiceError

DEPTH_LADDER = [ReadingDepth.deep, ReadingDepth.substantive, ReadingDepth.overview]
ROLE_ORDER = {
    ReadingRole.prerequisite: 0,
    ReadingRole.foundational: 1,
    ReadingRole.survey: 2,
    ReadingRole.method: 3,
    ReadingRole.recent_method: 3,
    ReadingRole.benchmark_dataset: 4,
    ReadingRole.unknown: 5,
}
SESSION_MAX_MINUTES = 120


class PlanEntry(BaseModel):
    order: int
    session: int
    paper: Paper
    role: ReadingRole
    priority: Priority
    depth: ReadingDepth
    minutes: tuple[int, int]
    budgeted_minutes: int
    why: str
    adjustment: str | None = None


class ReadingPlan(BaseModel):
    hours: float
    budget_minutes: int
    reading_minutes: int
    notes_buffer_minutes: int
    questions_buffer_minutes: int
    source: str
    profile_name: str | None
    entries: list[PlanEntry] = Field(default_factory=list)
    deferred: list[dict] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


def _effort(svc: Paperscout, depth: ReadingDepth, role: ReadingRole) -> tuple[int, int]:
    return svc.scorer._effort(depth, role)


def _pool(svc: Paperscout, folder: str | None, profile_id: str | None, notices: list[str]) -> tuple[list[Candidate], str]:
    if folder:
        f = svc.get_folder(folder)
        cands, _ = svc._folder_candidates(f, profile_id, unread=True, new=False, imported=False)
        return cands, f'local folder "{f.name}" (unread papers)'
    seen: dict[str, Candidate] = {}
    for f in svc.store.list_folders():
        cands, _ = svc._folder_candidates(f, profile_id, unread=True, new=False, imported=False)
        for c in cands:
            seen.setdefault(c.paper.paper_id, c)
    for pid in svc.store.feedback_paper_ids(profile_id, {FeedbackLabel.read_later}):
        paper = svc.store.get_paper(pid)
        if paper and pid not in seen:
            seen[pid] = Candidate(paper=paper, provenance=[Provenance(source="feedback.read_later")])
    if seen:
        return list(seen.values()), "unread papers in all local folders + papers marked read_later"
    run = svc.store.latest_run(("recent", "discover", "authors"))
    if run:
        rows = svc.store.run_candidates(run["id"])[:25]
        papers = svc.store.get_papers(r["paper_id"] for r in rows)
        notices.append(f"No unread folder papers; using the latest discovery run `{run['id']}` ({run['query']}).")
        cands = []
        for r in rows:
            if r["paper_id"] in papers:
                prov = [Provenance.model_validate(p) for p in json.loads(r["provenance"])]
                cands.append(Candidate(paper=papers[r["paper_id"]], provenance=prov))
        return cands, f"latest discovery run {run['id']}"
    return [], "nothing available"


def build_plan(
    svc: Paperscout,
    *,
    hours: float | None = None,
    week: bool = False,
    folder: str | None = None,
    profile: str | None = None,
) -> ReadingPlan:
    notices: list[str] = []
    prof, _ = svc.resolve_profile(profile)
    if week and hours is not None:
        raise ServiceError("Use either --hours or --week, not both.")
    if hours is None:
        if prof and prof.weekly_reading_hours:
            hours = prof.weekly_reading_hours
            notices.append(f"Using the weekly reading time from profile '{prof.name}' ({hours:g} h).")
        else:
            raise ServiceError("No time budget: pass --hours N, or set weekly_reading_hours in your profile.")
    if not (0.25 <= hours <= 80):
        raise ServiceError("--hours must be between 0.25 and 80.")

    budget = int(round(hours * 60))
    notes_fraction = svc.config["effort_minutes"]["note_buffer_fraction"]
    notes_buffer = int(round(budget * notes_fraction))
    questions_buffer = 15 if budget >= 120 else 5
    available = budget - notes_buffer - questions_buffer

    candidates, source = _pool(svc, folder, prof.id if prof else None, notices)
    if not candidates:
        raise ServiceError("Nothing to plan: add papers to a local folder, mark papers read_later, or run /recent first.")
    criteria = "\n".join([prof.topic_text(), prof.research_question]).strip() if prof else ""
    if folder:
        criteria = svc.get_folder(folder).criteria_text() + "\n" + criteria
    ctx = svc._build_context(criteria, prof, prof is not None, None, [], notices)
    ranked: list[RankedPaper] = svc._score(candidates, ctx, prof.id if prof else None)

    chosen: list[tuple[RankedPaper, ReadingDepth, tuple[int, int], int, str | None]] = []
    deferred = []
    remaining = available
    for item in ranked:
        a = item.assessment
        if a.priority is Priority.skip or (item.latest_feedback and item.latest_feedback.negative):
            continue
        if a.reading_depth is ReadingDepth.skip:
            continue
        start = DEPTH_LADDER.index(a.reading_depth) if a.reading_depth in DEPTH_LADDER else 2
        placed = False
        for depth in DEPTH_LADDER[start:]:
            lo, hi = _effort(svc, depth, a.reading_role)
            need = (lo + hi) // 2
            if need <= remaining:
                note = None if depth is a.reading_depth else f"reduced from {a.reading_depth.value} to fit the budget"
                chosen.append((item, depth, (lo, hi), need, note))
                remaining -= need
                placed = True
                break
        if not placed:
            deferred.append({"title": item.paper.title, "paper_id": item.paper.paper_id,
                             "reason": "does not fit the remaining budget"})
        if remaining < _effort(svc, ReadingDepth.overview, ReadingRole.method)[0]:
            break

    chosen.sort(key=lambda c: (ROLE_ORDER.get(c[0].assessment.reading_role, 5),
                               PRIORITY_ORDER[c[0].assessment.priority]))
    entries, session, session_minutes = [], 1, 0
    for order, (item, depth, minutes, need, note) in enumerate(chosen, 1):
        if session_minutes and session_minutes + need > SESSION_MAX_MINUTES:
            session += 1
            session_minutes = 0
        session_minutes += need
        a = item.assessment
        why = f"{a.priority.value}; topic {a.topic_relevance.band.value}, project {a.project_usefulness.band.value}"
        if a.reading_role in (ReadingRole.prerequisite, ReadingRole.foundational):
            why += "; read early because later papers likely build on it"
        entries.append(PlanEntry(order=order, session=session, paper=item.paper, role=a.reading_role,
                                 priority=a.priority, depth=depth, minutes=minutes, budgeted_minutes=need,
                                 why=why, adjustment=note))
    if not entries:
        notices.append("The budget is too small for even one overview pass (about 20–40 min).")
    return ReadingPlan(
        hours=hours, budget_minutes=budget, reading_minutes=available - remaining, notes_buffer_minutes=notes_buffer,
        questions_buffer_minutes=questions_buffer, source=source, profile_name=prof.name if prof else None,
        entries=entries, deferred=deferred[:10], notices=notices,
    )


def render_plan(plan: ReadingPlan) -> str:
    lines = [
        f"# Reading plan: {plan.hours:g} h",
        "",
        f"- Source: {plan.source}",
        f"- Profile: {plan.profile_name or 'none'}",
        f"- Budget: {plan.budget_minutes} min = reading {plan.reading_minutes} min + notes {plan.notes_buffer_minutes} min "
        f"+ open questions/next steps {plan.questions_buffer_minutes} min",
        "- All durations are planning heuristics, not predictions. Adjust them after your first sessions.",
        "",
    ]
    for n in plan.notices:
        lines.append(f"- Note: {n}")
    if plan.notices:
        lines.append("")
    current = None
    for e in plan.entries:
        if e.session != current:
            current = e.session
            lines.append(f"## Session {current}")
        adj = f" ({e.adjustment})" if e.adjustment else ""
        lines.append(
            f"{e.order}. **{sanitize_untrusted(e.paper.title)}** ({paper_date(e.paper)}) — {e.depth.value}{adj}, "
            f"{effort_text(e.minutes)} (budgeted {e.budgeted_minutes} min)"
        )
        lines.append(f"   - Role: {e.role.value}. Why: {e.why}")
        lines.append(f"   - {e.paper.link}")
    lines += [
        "",
        "## Wrap-up",
        f"- {plan.notes_buffer_minutes} min: write notes in your own words (scaffold: `/note <paper> --pass first`).",
        f"- {plan.questions_buffer_minutes} min: list unresolved questions; record feedback with `/paper-feedback`.",
    ]
    if plan.deferred:
        lines += ["", "## Deferred (did not fit)"] + [f"- {d['title']} (`{d['paper_id']}`)" for d in plan.deferred]
    return "\n".join(lines)


# --------------------------------------------------------------------------- notes

PASS_SECTIONS = {
    "first": [
        ("Purpose of reading", "Why am I reading this now? What decision or question does it inform?"),
        ("Problem", "In one or two sentences, in my own words."),
        ("Main contribution (as claimed by the authors)", "What do they say is new?"),
        ("Connection to my project", "Where could this matter for my work, if at all?"),
        ("Questions", "What is unclear or what would I need to check?"),
        ("Next action", "Skip / second pass / deep study / try the code. Why?"),
    ],
    "second": [
        ("Purpose of reading", "What do I want to understand after this pass?"),
        ("Problem", "Setting, assumptions, inputs/outputs."),
        ("Main contribution", "Separate the authors' claims from what the evidence shows."),
        ("Method", "Key components, sketched in my own notation. Section/page refs."),
        ("Evidence", "Which experiments support which claims? Baselines, datasets, metrics. Page/table refs."),
        ("Limitations", "Stated by the authors / noticed by me (mark which)."),
        ("Connection to my project", "What could I reuse, compare against, or avoid?"),
        ("Questions", "Open issues to resolve; contradictions to clarify (not proof of error)."),
        ("Next action", "Concrete next step and when."),
    ],
    "deep": [
        ("Purpose of reading", "What should I be able to reproduce, derive, or extend afterwards?"),
        ("Problem", "Formal statement and assumptions."),
        ("Main contribution", "Claims vs. reported evidence vs. my interpretation."),
        ("Method", "Derivations to re-check, with page/equation references."),
        ("Evidence", "Experimental setup details; fairness of comparisons (only with the full text in hand)."),
        ("Limitations", "Failure cases, scope, compute requirements."),
        ("Reproduction / implementation notes", "Code availability (verify independently), environment, data."),
        ("Connection to my project", "Exact integration points or experiments to run."),
        ("Questions", "What would I ask the authors?"),
        ("Next action", "Implementation or experiment plan."),
    ],
}


def note_template(svc: Paperscout, paper_text: str, *, pass_: str = "first", profile: str | None = None) -> tuple[str, Paper]:
    if pass_ not in PASS_SECTIONS:
        raise ServiceError(f"--pass must be one of: {', '.join(PASS_SECTIONS)}")
    paper = svc.require_paper(paper_text)
    prof, _ = svc.resolve_profile(profile)
    ids = [f"S2: {paper.paper_id}"]
    if paper.doi:
        ids.append(f"DOI: {paper.doi}")
    if paper.arxiv_id:
        ids.append(f"arXiv: {paper.arxiv_id}")
    lines = [
        "---",
        f"title: \"{sanitize_untrusted(paper.title).replace(chr(34), chr(39))}\"",
        f"authors: \"{sanitize_untrusted(paper.short_authors(6))}\"",
        f"date: {paper_date(paper)}",
        f"link: {paper.link}",
        f"ids: \"{'; '.join(ids)}\"",
        f"pass: {pass_}",
        f"created: {date.today().isoformat()}",
        "---",
        "",
        f"# {sanitize_untrusted(paper.title)}",
        "",
    ]
    if prof and prof.research_question:
        lines += [f"> Profile goal ({prof.name}): {prof.research_question}", ""]
    for heading, prompt in PASS_SECTIONS[pass_]:
        lines += [f"## {heading}", f"<!-- {prompt} -->", "", ""]
    return "\n".join(lines), paper


def save_note(svc: Paperscout, text: str, paper: Paper, pass_: str) -> Path:
    svc.settings.notes_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", paper.title.lower()).strip("-")[:60] or paper.paper_id
    path = svc.settings.notes_dir / f"{slug}-{pass_}.md"
    if path.exists():
        raise ServiceError(f"{path} already exists; not overwriting your notes.")
    path.write_text(text, encoding="utf-8")
    return path
