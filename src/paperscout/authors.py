"""Papers by specific researchers, ranked against the research profile.

Names and an optional affiliation ("Seungryong Kim @ KAIST") are resolved through
OpenAlex, which has author and institution records; arXiv has neither, and
Semantic Scholar's affiliations are sparse. OpenAlex splits one person into
several author records and sometimes merges namesakes, so with an affiliation a
paper is kept only when one of its authorships has **both** the name and the
institution. That is robust to split records, but a namesake at the same
institution would also match. Without an affiliation, the author record with
the most works for that exact name is used, and the alternatives are reported.

The papers then go through the same scoring, judges, report layout, /download
and /reading-plan as /discover. Researcher names and the affiliation text are
sent to OpenAlex; nothing else about the researcher's background is.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from paperscout.dedup import deduplicate
from paperscout.dedup import identity_keys as dedup_keys
from paperscout.http import ApiError
from paperscout.models import Candidate, Priority, Provenance
from paperscout.openalex.client import openalex_id, paper_from_work
from paperscout.reports import RankingReport
from paperscout.service import Paperscout, ServiceError

KIND = "authors"
DEFAULT_DAYS = 730
MAX_TARGETS = 30
MAX_PAGES = 3  # 600 works per researcher and window
MAX_REQUESTS = 120
SOURCE = "OpenAlex works by author (keyless; each paper matched on its author list)"
DATE_BASIS = ("OpenAlex publication_date: the first publication, usually the arXiv v1 date for preprints or the "
              "venue date otherwise")
_AUTHOR_ID = re.compile(r"^(?:https?://openalex\.org/)?(A\d+)$", re.IGNORECASE)
_FILTER_UNSAFE = re.compile(r"[,|:;]")


@dataclass
class Target:
    """One researcher as the user wrote it."""

    label: str
    name: str | None = None
    author_id: str | None = None
    affiliation: str | None = None
    # filled in while resolving
    institution_id: str | None = None
    institution: str | None = None
    matched_as: str | None = None
    papers: int = 0
    dropped: int = 0
    note: str = ""
    works: list[dict[str, Any]] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        return {"researcher": self.label, "matched_as": self.matched_as, "institution": self.institution,
                "papers": self.papers, "note": self.note}


def name_forms(name: str | None) -> set[str]:
    """Spellings of one name to compare: "Ye, Jong-Chul", "Jong Chul Ye" and "Jongchul Ye" all match."""
    if not name:
        return set()
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    if "," in text:
        family, _, given = text.partition(",")
        text = f"{given} {family}"
    tokens = re.findall(r"[a-z0-9]+", text)
    if not tokens:
        return set()
    forms = {"".join(tokens)}
    if len(tokens) > 1:
        forms.add("".join(tokens[-1:] + tokens[:-1]))  # family name first ("Kim Seungryong")
        forms.add("".join(tokens[1:] + tokens[:1]))
    return forms


def parse_targets(positionals: list[str], default_affiliation: str | None) -> list[Target]:
    """Names separated by ';' or ',', or given as separate quoted names. "Name @ Affiliation" sets one's own."""
    raw = " ".join(positionals).strip()
    if not raw:
        return []
    if ";" in raw or "," in raw:
        parts = re.split(r"[;,]", raw)
    elif len(positionals) > 1 and any(" " in token.strip() for token in positionals):
        parts = positionals
    else:
        parts = [raw]
    targets: list[Target] = []
    for part in parts:
        label = " ".join(part.split())
        if not label:
            continue
        who, _, where = label.partition("@")
        who, where = who.strip(), where.strip() or default_affiliation
        match = _AUTHOR_ID.match(who)
        if match:
            targets.append(Target(label=label, author_id=match.group(1).upper(), affiliation=where or None))
        elif who:
            targets.append(Target(label=label, name=who, affiliation=where or None))
    return targets


def authorship_match(work: dict[str, Any], forms: set[str], institution_id: str) -> str | None:
    """How the named author is tied to the institution on this paper: "institution", "unlisted" or None.

    "unlisted": OpenAlex lists no institution for the named author on this paper (common for preprints),
    while the search already required another author there. An authorship listing other institutions
    only doesn't match.
    """
    found = None
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        names = name_forms(authorship.get("raw_author_name")) | name_forms(
            (authorship.get("author") or {}).get("display_name"))
        if not names & forms:
            continue
        institutions = {openalex_id(i.get("id")) for i in authorship.get("institutions") or [] if isinstance(i, dict)}
        if institution_id in institutions:
            return "institution"
        if not institutions:
            found = "unlisted"
    return found


def _resolve(svc: Paperscout, target: Target, window: tuple[date, date], institutions: dict[str, Any],
             notices: list[str]) -> None:
    """Fill in the target's works; problems go into its note rather than stopping the run."""
    dates = f"from_publication_date:{window[0].isoformat()},to_publication_date:{window[1].isoformat()}"
    client = svc.openalex
    if target.affiliation:
        key = target.affiliation.lower()
        if key not in institutions:
            institutions[key] = client.institution(target.affiliation)
        found = institutions[key]
        if found is None:
            target.note = f"no institution matched '{target.affiliation}'"
            return
        target.institution_id = openalex_id(found["id"])
        target.institution = found.get("display_name")
    if target.author_id:
        _, works = client.works(f"author.id:{target.author_id},{dates}", MAX_PAGES)
        target.matched_as = f"OpenAlex author {target.author_id}"
        target.works = works
    elif target.institution_id:
        name = _FILTER_UNSAFE.sub(" ", target.name or "").strip()
        _, works = client.works(f"raw_author_name.search:{name},authorships.institutions.id:{target.institution_id},"
                                f"{dates}", MAX_PAGES)
        forms = name_forms(target.name)
        how = [(w, authorship_match(w, forms, target.institution_id)) for w in works]
        target.works = [w for w, match in how if match]
        target.dropped = len(works) - len(target.works)
        unlisted = sum(1 for _, match in how if match == "unlisted")
        target.matched_as = f"{target.name} on each paper's author list, with {target.institution}"
        notes = []
        if unlisted:
            notes.append(f"{unlisted} matched with no institution listed for this author on the paper (a co-author "
                         f"is at {target.institution})")
        if target.dropped:
            notes.append(f"{target.dropped} search hit(s) left out: a different name, or this name with another "
                         "institution")
        target.note = "; ".join(notes)
    else:
        forms = name_forms(target.name)
        candidates = [c for c in client.author_candidates(target.name or "") if name_forms(c.get("display_name"))
                      & forms]
        if not candidates:
            target.note = "no OpenAlex author with this name"
            return
        best = max(candidates, key=lambda c: c.get("works_count") or 0)
        author_id = openalex_id(best["id"])
        places = ", ".join(i.get("display_name", "") for i in best.get("last_known_institutions") or [])
        _, target.works = client.works(f"author.id:{author_id},{dates}", MAX_PAGES)
        target.matched_as = f"OpenAlex author {author_id} ({best.get('works_count', '?')} works)"
        target.institution = places or None
        if len(candidates) > 1:
            target.note = (f"{len(candidates) - 1} other OpenAlex record(s) share this name; add '@ <affiliation>' "
                           "to match on each paper instead")
    target.papers = len(target.works)


def _keep_highest_citations(merged: list[Candidate], raw: list[Candidate]) -> None:
    """A preprint and its published version are merged; the published one usually has more citations."""
    best: dict[str, int] = {}
    for cand in raw:
        for key in dedup_keys(cand.paper):
            if cand.paper.citation_count is not None:
                best[key] = max(best.get(key, 0), cand.paper.citation_count)
    for cand in merged:
        counts = [best[k] for k in dedup_keys(cand.paper) if k in best]
        if counts and max(counts) > (cand.paper.citation_count or 0):
            cand.paper = cand.paper.model_copy(update={"citation_count": max(counts)})


def _heading(targets: list[Target]) -> str:
    places = {t.affiliation for t in targets if t.affiliation}
    where = f" ({places.pop()})" if len(places) == 1 else ""
    if len(targets) <= 3:
        return "Papers by " + ", ".join((t.name or t.author_id or t.label) for t in targets) + where
    return f"Papers by {len(targets)} researchers{where}"


def discover_authors(
    svc: Paperscout,
    positionals: list[str],
    *,
    affiliation: str | None = None,
    days: int | None = None,
    since: date | None = None,
    until: date | None = None,
    topic: str | None = None,
    profile_name: str | None = None,
    limit: int = 20,
    include_read: bool = False,
    quick: bool = False,
) -> RankingReport:
    if not svc.settings.openalex:
        raise ServiceError("/authors needs OpenAlex, which is turned off (PAPERSCOUT_OPENALEX=off).")
    notices: list[str] = []
    profile, explicit = svc.resolve_profile(profile_name)
    targets = parse_targets(positionals, affiliation)
    if not targets and profile and profile.followed_authors:
        targets = parse_targets(["; ".join(profile.followed_authors)], affiliation)
        notices.append(f"No names given: used the {len(targets)} followed authors of profile '{profile.name}'.")
    if not targets:
        raise ServiceError('Name at least one researcher, e.g. `authors Seungryong Kim --affiliation KAIST`, or '
                           'add "followed_authors" to profile/profile.json and run `paperscout profile sync`.')
    if len(targets) > MAX_TARGETS:
        raise ServiceError(f"{len(targets)} researchers given; one run takes at most {MAX_TARGETS}. Split the list.")
    window = svc._date_window(days if (days is not None or since is not None) else DEFAULT_DAYS, since, until)

    affiliations = {t.affiliation.lower() for t in targets if t.affiliation}
    budget = min(MAX_REQUESTS, len(affiliations) + len(targets) * (MAX_PAGES + 1) + 5)
    if svc.openalex.policy.max_requests < budget:
        # The per-command cap guards against runaway commands; this run's size is known in advance.
        svc.openalex.policy = dataclasses.replace(svc.openalex.policy, max_requests=budget)
    institutions: dict[str, Any] = {}
    for target in targets:
        try:
            _resolve(svc, target, window, institutions, notices)
        except ApiError as exc:
            target.note = f"OpenAlex request failed: {exc}"
    failed = [t for t in targets if t.note.startswith("OpenAlex request failed")]
    if failed and not any(t.works for t in targets):
        raise ServiceError(f"No papers retrieved; OpenAlex requests failed ({failed[0].note}).")

    raw: list[Candidate] = []
    for target in targets:
        for rank, work in enumerate(target.works, 1):
            paper = paper_from_work(work)
            if paper is None:
                continue
            raw.append(Candidate(paper=paper, provenance=[Provenance(
                source="openalex.author", query=target.label, seed=target.name or target.author_id, rank=rank,
                detail=target.institution)]))
    candidates, duplicates = deduplicate(raw)
    _keep_highest_citations(candidates, raw)
    svc._canonicalize(candidates)
    candidates, excluded = svc._filter_reviewed(candidates, profile, include_read)

    if topic:
        criteria = topic
        use_profile, mode = svc._profile_mode(criteria, profile, explicit, notices, "topic")
    elif profile:
        criteria = "\n".join([*profile.search_queries, *profile.keywords, profile.research_question]).strip()
        use_profile, mode = True, "profile"
    else:
        criteria, use_profile, mode = "", False, "authors only (no topic or profile)"
        notices.append("No topic and no research profile, so relevance is unknown: the papers are ordered by "
                       "recency and citations only. Add --topic or run /setup.")
    ctx = svc._build_context(criteria, profile, use_profile, None, [], notices)
    ranked = svc._score(candidates, ctx, profile.id if profile else None, notices, quick)
    shown = [r for r in ranked if r.assessment.priority is not Priority.skip]
    selected = shown[:limit]

    notices.append("Researcher names and affiliations were sent to OpenAlex (keyless; requests share a small free "
                   "daily allowance per network). OpenAlex can split one person into several records or merge "
                   "namesakes; with an affiliation, papers are matched on each paper's author list, so a namesake "
                   "at the same institution would also match.")
    heading = _heading(targets)
    query = "; ".join(t.label for t in targets)
    date_window = {"since": window[0].isoformat(), "until": window[1].isoformat(), "basis": DATE_BASIS}
    run_params = {"limit": limit, "include_read": include_read, "authors": [t.row() for t in targets],
                  "window": date_window, "shortlist": [r.paper.paper_id for r in selected],
                  "context": svc._run_context(ctx, None, [], mode),
                  "report": {"heading": heading, "source": SOURCE, "date_window": date_window, "limit": limit,
                             "balanced": False, "hide_skip": True}}
    run_id = svc._persist_run(KIND, query, run_params, profile, ranked, notices)
    return RankingReport(
        kind=KIND, heading=heading, run_id=run_id, ranking_version=svc.config.version,
        profile_name=profile.name if profile else None, profile_mode=mode, source=SOURCE, query=query,
        date_window=date_window, notices=notices, items=selected, excluded=excluded,
        researchers=[t.row() for t in targets],
        stats={"retrieved": len(raw), "duplicates_merged": duplicates, "ranked": len(ranked),
               "below_threshold": len(ranked) - len(shown), "shown": len(selected),
               "judged": sum(1 for r in ranked if any(c.judged for c in r.assessment.criteria.values())),
               **svc.api_stats()},
    )
