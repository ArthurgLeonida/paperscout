"""Budgeted live end-to-end smoke test against the real Semantic Scholar API.

Runs in an ISOLATED data home (default: <repo>/data/smoke, git-ignored) and
refuses to use your normal paperscout data home. Stops at the first
rate-limit/budget failure instead of retrying further.

    uv run -q python scripts/live_smoke.py            # default budget: 15 HTTP attempts
    uv run -q python scripts/live_smoke.py --budget 10

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
from paperscout.s2.client import S2BudgetExceeded, S2Error, S2RateLimited
from paperscout.service import Paperscout, ServiceError

REPO = Path(__file__).resolve().parents[1]
PROBE = "arXiv:2308.04079"  # 3D Gaussian Splatting (Kerbl et al.), used only as a stable lookup/seed
QUERY = "text guided 3D Gaussian scene editing"
FOLDER = "SMOKE 3D Scene Editing"
PROFILE_NAME = "SMOKE 3D Scene Editing"


class Stop(Exception):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=REPO / "data" / "smoke")
    parser.add_argument("--budget", type=int, default=15, help="max HTTP attempts for the whole run")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    home = args.home.resolve()
    if home == default_home().resolve():
        print("Refusing to run against your normal data home. Use an isolated --home.")
        return 2
    base = load_settings(REPO)
    settings = replace(base, home=home, s2_max_requests=args.budget, s2_max_retries=1, s2_min_interval=1.2)
    print(f"Isolated home: {home}")
    print(f"API key: {'configured (' + base.key_source + ')' if base.s2_api_key else 'not configured'}; "
          f"budget {args.budget} HTTP attempts; 1 retry per request\n")

    results: list[dict] = []
    svc = Paperscout.open(settings)
    state: dict = {}

    def step(name: str, fn):
        try:
            detail = fn()
            results.append({"step": name, "status": "PASS", "detail": detail})
            print(f"PASS  {name}: {detail}")
        except (S2RateLimited, S2BudgetExceeded) as exc:
            results.append({"step": name, "status": "FAIL", "detail": str(exc)})
            print(f"FAIL  {name}: {exc}")
            raise Stop from None
        except (S2Error, ServiceError, AssertionError) as exc:
            results.append({"step": name, "status": "FAIL", "detail": str(exc)})
            print(f"FAIL  {name}: {exc}")

    def lookup():
        res = svc.resolve(PROBE, prefer_local=False)
        assert res.status == "resolved" and res.paper, res.message
        state["seed"] = res.paper
        p = res.paper
        return f"{p.title} ({p.publication_date or p.year}); S2 {p.paper_id}; evidence {p.evidence_level.value}"

    def search():
        hits = svc.client.search_papers(QUERY, max_results=20)
        assert hits, "search returned no papers"
        state["search"] = [Candidate(paper=p, provenance=[Provenance(source="s2.search", query=QUERY, rank=r)])
                           for r, p in hits]
        with_abs = sum(1 for c in state["search"] if c.paper.abstract)
        return f"{len(hits)} papers ({with_abs} with abstracts); top: {hits[0][1].title}"

    def recommend():
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
        profile = ResearchProfile.model_validate(
            {**json.loads((REPO / "examples/profiles/3d-scene-editing.json").read_text(encoding="utf-8")),
             "name": PROFILE_NAME})
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
            calls, hits = svc.client.network_calls, svc.client.cache_hits
            diagnostics = svc.client.diagnostics
            svc.close()
        except Exception:
            calls, hits, diagnostics = "n/a", "n/a", []
    statuses = [d.get("status") for d in diagnostics]
    print(f"\nHTTP attempts: {calls}; cache hits: {hits}; statuses: {statuses}")
    (home / "smoke-summary.json").write_text(json.dumps({"results": results, "diagnostics": diagnostics},
                                                         indent=2, default=str), encoding="utf-8")
    return 0 if results and all(r["status"] == "PASS" for r in results) and len(results) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
