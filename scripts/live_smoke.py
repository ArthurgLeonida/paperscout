"""Budgeted live end-to-end smoke test against the active paper provider.

The provider follows PAPERSCOUT_PROVIDER (auto: Semantic Scholar with a key,
otherwise arXiv); override with --provider arxiv|semantic-scholar.

Runs in an ISOLATED data home (default: <repo>/data/smoke, git-ignored) and
refuses to use your normal paperscout data home. Stops at the first
rate-limit/budget failure instead of retrying further.

    uv run -q python scripts/live_smoke.py            # default budget: 15 HTTP attempts
    uv run -q python scripts/live_smoke.py --budget 10
    uv run -q python scripts/live_smoke.py --provider arxiv

Writes only to the isolated database. The "assessment" saved in step 9 is a
clearly labelled mechanical test record, not a real judgment of the paper.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from paperscout.config import default_home, load_settings
from paperscout.dedup import deduplicate
from paperscout.models import Candidate, Provenance, ResearchProfile
from paperscout.planning import build_plan
from paperscout.http import ApiBudgetExceeded, ApiError, ApiRateLimited
from paperscout.service import Paperscout, ServiceError

REPO = Path(__file__).resolve().parents[1]
PROBE = "arXiv:2308.04079"  # 3D Gaussian Splatting (Kerbl et al.), used only as a stable lookup/seed
QUERY = "text guided 3D Gaussian scene editing"
FOLDER = "SMOKE 3D Scene Editing"
PROFILE_NAME = "SMOKE 3D Scene Editing"
# A fixed test profile for the isolated smoke database; it is not anyone's real background.
SMOKE_PROFILE = {
    "name": PROFILE_NAME,
    "field": "computer vision; 3D scene editing",
    "research_question": "How can text-guided editing of 3D Gaussian splatting scenes preserve unedited regions "
                         "and multi-view consistency?",
    "project_description": "Text-guided 3D scene editing on Gaussian splatting with diffusion guidance.",
    "desired_outcomes": ["choose a baseline editing method"],
    "keywords": ["gaussian splatting", "scene editing", "multi-view consistency"],
    "exclusion_criteria": ["character animation"],
    "recency_preference": "strong",
    "knowledge": [{"topic": "gaussian splatting", "level": "working"},
                  {"topic": "score distillation", "level": "new"}],
    "weekly_reading_hours": 5,
}


class Stop(Exception):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=REPO / "data" / "smoke")
    parser.add_argument("--budget", type=int, default=15, help="max HTTP attempts for the whole run")
    parser.add_argument("--provider", choices=("arxiv", "semantic-scholar"), default=None,
                        help="override PAPERSCOUT_PROVIDER for this run")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    home = args.home.resolve()
    if home == default_home().resolve():
        print("Refusing to run against your normal data home. Use an isolated --home.")
        return 2
    base = load_settings(REPO)
    settings = replace(base, home=home, s2_max_requests=args.budget, s2_max_retries=1, s2_min_interval=1.2,
                       arxiv_max_requests=args.budget, arxiv_max_retries=1,
                       provider=args.provider or base.provider)
    provider = settings.effective_provider
    print(f"Isolated home: {home}")
    print(f"Provider: {provider}; S2 API key: "
          f"{'configured (' + base.key_source + ')' if base.s2_api_key else 'not configured'}; "
          f"budget {args.budget} HTTP attempts per provider; 1 retry per request\n")

    results: list[dict] = []
    svc = Paperscout.open(settings)
    state: dict = {}

    def step(name: str, fn):
        try:
            detail = fn()
            status = "SKIP" if isinstance(detail, str) and detail.startswith("skipped:") else "PASS"
            results.append({"step": name, "status": status, "detail": detail})
            print(f"{status:<5} {name}: {detail}")
        except (ApiRateLimited, ApiBudgetExceeded) as exc:
            results.append({"step": name, "status": "FAIL", "detail": str(exc)})
            print(f"FAIL  {name}: {exc}")
            raise Stop from None
        except (ApiError, ServiceError, AssertionError) as exc:
            results.append({"step": name, "status": "FAIL", "detail": str(exc)})
            print(f"FAIL  {name}: {exc}")

    def lookup():
        res = svc.resolve(PROBE, prefer_local=False)
        assert res.status == "resolved" and res.paper, res.message
        state["seed"] = res.paper
        p = res.paper
        return f"{p.title} ({p.publication_date or p.year}); id {p.paper_id}; evidence {p.evidence_level.value}"

    def search():
        hits = svc.search_sample(QUERY, max_results=20)
        assert hits, "search returned no papers"
        source = "arxiv.search" if svc.uses_arxiv else "s2.search"
        state["search"] = [Candidate(paper=p, provenance=[Provenance(source=source, query=QUERY, rank=r)])
                           for r, p in hits]
        with_abs = sum(1 for c in state["search"] if c.paper.abstract)
        return f"{len(hits)} papers ({with_abs} with abstracts); top: {hits[0][1].title}"

    def recommend():
        if svc.uses_arxiv:
            return "skipped: arXiv has no recommendations (Semantic Scholar only)"
        assert "seed" in state, "needs the lookup step"
        recs = svc.client.recommend([state["seed"].paper_id], limit=10)
        state["recs"] = [Candidate(paper=p, provenance=[Provenance(source="s2.recommendations",
                                                                   seed=state["seed"].paper_id[:8], rank=i)])
                         for i, p in enumerate(recs, 1)]
        return f"{len(recs)} recommendations for the seed"

    def dedup():
        merged, dupes = deduplicate(state.get("search", []) + state.get("recs", []))
        state["merged"] = merged
        multi = sum(1 for c in merged if len({p.source for p in c.provenance}) > 1)
        return f"{len(merged)} unique of {len(state.get('search', [])) + len(state.get('recs', []))}; " \
               f"{dupes} duplicates merged; {multi} papers found by both search and recommendations"

    def save():
        profile = ResearchProfile(**SMOKE_PROFILE)
        svc.save_profile(profile, activate=True)
        if svc.store.find_folder(FOLDER) is None:
            svc.create_folder(FOLDER, "Smoke-test folder (isolated database)", ["3D scene editing"])
        folder = svc.store.get_folder(FOLDER)
        added = 0
        for cand in state["merged"][:6]:
            svc.store.upsert_paper(cand.paper, cand.provenance[0].source)
            added += svc.store.add_to_folder(folder.id, cand.paper.paper_id, "user", "local")
        return f"{added} papers saved to local folder '{FOLDER}' ({folder.id})"

    def rank():
        report = svc.rank_folder(FOLDER)
        assert report.items, "ranking returned nothing"
        state["top"] = report.items[0]
        a = report.items[0].assessment
        return f"{len(report.items)} ranked; top: {report.items[0].paper.title} " \
               f"({a.priority.value}, {a.reading_role.value}, evidence {a.evidence_level.value})"

    def feedback():
        event, paper, history = svc.record_feedback(state["top"].paper.paper_id, "read_later", "smoke test")
        return f"recorded {event.label.value} on '{paper.title}' (history length {len(history)})"

    def plan():
        p = build_plan(svc, hours=2)
        return f"{len(p.entries)} papers planned in {p.reading_minutes} min (+{p.notes_buffer_minutes} notes)"

    def assessment_roundtrip():
        pid = state["top"].paper.paper_id
        level = state["top"].paper.evidence_level.value
        svc.record_claude_assessment({
            "paper_id": pid, "model": "smoke-test (not a real assessment)", "prompt_version": "smoke-v1",
            "evidence_level": level, "topic_relevance": "unknown", "project_usefulness": "unknown",
            "reading_role": "unknown", "priority": "unknown", "reading_depth": "overview",
            "rationale": "Mechanical storage test written by scripts/live_smoke.py.",
            "uncertainties": ["Not a judgment of the paper."], "next_action": "none",
        })
        svc.close()
        reopened = Paperscout.open(settings)  # simulate an application restart
        try:
            stored = reopened.store.assessments_for(pid)
            members = reopened.store.memberships(reopened.store.get_folder(FOLDER).id)
        finally:
            reopened.close()
        assert stored and stored[-1]["prompt_version"] == "smoke-v1"
        return f"saved, restarted, reopened: {len(stored)} assessment(s); folder still has {len(members)} papers"

    try:
        for name, fn in [("1 paper lookup", lookup), ("2 topic search", search),
                         ("3 seed recommendations", recommend), ("4 deduplication", dedup),
                         ("5 save candidates", save), ("6 rank saved candidates", rank),
                         ("7 record feedback", feedback), ("8 reading plan", plan),
                         ("9 save + reopen assessment", assessment_roundtrip)]:
            step(name, fn)
    except Stop:
        print("\nStopped early to respect rate limits; remaining steps were not run.")
    finally:
        try:
            stats = svc.api_stats()
            calls, hits = stats["api_calls"], stats["cache_hits"]
            diagnostics = svc.client.diagnostics + svc.arxiv.diagnostics
            svc.close()
        except Exception:
            calls, hits, diagnostics = "n/a", "n/a", []
    statuses = [d.get("status") for d in diagnostics]
    print(f"\nHTTP attempts: {calls}; cache hits: {hits}; statuses: {statuses}")
    (home / "smoke-summary.json").write_text(json.dumps({"results": results, "diagnostics": diagnostics},
                                                         indent=2, default=str), encoding="utf-8")
    return 0 if len(results) == 9 and all(r["status"] in ("PASS", "SKIP") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
