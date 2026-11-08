"""Field trends: quarterly sampling, counted signals, and the trend analyst's validated directions.

All papers are SYNTHETIC test fixtures.
"""

import json
from datetime import date

import pytest

from paperscout import cli
from paperscout.http import ApiError
from paperscout.models import Paper, ResearchProfile
from paperscout.trends import (Sampled, best_possible_p, compute_signals, fdr_cutoff, method_names, p_value,
                               paper_text, quarters, term_ngrams,
                               two_proportion_z)

from . import conftest

EARLY = ["2025-10-20", "2025-11-12", "2025-12-03", "2026-01-15", "2026-02-10", "2026-03-05", "2026-03-20",
         "2026-02-25"]
RECENT = ["2026-04-08", "2026-05-14", "2026-06-02", "2026-07-21", "2026-08-11", "2026-08-30", "2026-09-17",
          "2026-10-02", "2026-10-15", "2026-10-28"]


def entries() -> list[dict]:
    out = []
    for n, day in enumerate(EARLY):
        out.append({"id": f"25{n:02d}.0{n:04d}", "published": day, "cats": ["cs.CV"],
                    "title": f"Neural Radiance Field Scene Editing Variant {n}",
                    "summary": "We study scene editing of neural radiance fields with InstructPix2Pix. Our method "
                               "achieves state-of-the-art results compared with Instruct-NeRF2NeRF."})
    for n, day in enumerate(RECENT):
        out.append({"id": f"26{n:02d}.1{n:04d}", "published": day, "cats": ["cs.CV"],
                    "title": f"Gaussian Splatting Scene Editing Variant {n}",
                    "summary": "We study scene editing of 3D Gaussian splatting with video diffusion priors and "
                               "InstructPix2Pix. Compared with GaussianEditor, we achieve state-of-the-art quality."})
    return out


@pytest.fixture
def arxiv_field(svc, monkeypatch):
    monkeypatch.setattr(conftest, "ARXIV_ENTRIES", entries())
    svc.provider = "arxiv"
    return svc


def run_trends(svc, *tokens):
    return cli.run("trends", list(tokens), svc=svc)


def run_id_of(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("- Run: ")).split("`")[1]


# ----------------------------------------------------------------------------- pure helpers


def test_quarters_cover_the_window_and_flag_the_partial_one():
    periods = quarters(date(2026, 11, 7), 12)
    assert [p.label for p in periods] == ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
    assert periods[0].start == date(2025, 10, 1) and periods[-1].end == date(2026, 11, 7)
    assert [p.partial for p in periods] == [False, False, False, False, True]


def test_terms_stop_at_punctuation_and_stopwords():
    grams = term_ngrams("Editing of 3D Gaussian splatting scenes, with video-diffusion priors.")
    assert grams[("3d", "gaussian", "splat")] == "3d gaussian splatting"
    assert ("video", "diffusion", "prior") in grams
    assert not any(k[:2] == ("edit", "3d") for k in grams)  # "of" breaks the term
    assert not any("github" in k for k in term_ngrams(paper_text(Paper(
        paper_id="x", title="T", abstract="Code: https://github.com/someone/project-page."))))


def test_method_names_skip_title_case_and_split_lowercase_suffixes():
    text = ("Text-Driven Multi-View editing: we compare InstructPix2Pix, 3DGS-based GaussianEditor, "
            "Instruct-NeRF2NeRF and NeRFs, scored with CLIP (+2 dB PSNR) on a GPU at CVPR.")
    assert method_names(text) == {"InstructPix2Pix", "3DGS", "GaussianEditor", "Instruct-NeRF2NeRF", "NeRF", "CLIP", "PSNR"}


def test_two_proportion_z_sign_and_degenerate_cases():
    assert two_proportion_z(2, 50, 20, 50) > 3 and two_proportion_z(20, 50, 2, 50) < -3
    assert two_proportion_z(0, 0, 3, 10) == 0.0 and two_proportion_z(0, 10, 0, 10) == 0.0


def test_false_discovery_cutoff_needs_more_than_one_lucky_term():
    assert p_value(1.96) == pytest.approx(0.05, abs=1e-3)
    assert fdr_cutoff([0.001, 0.002, 0.09, 0.5]) == 0.002  # 0.09 > 3/4 * 0.10
    assert fdr_cutoff([0.03] + [0.5] * 99) == -1.0  # one p < 0.05 among 100 tests isn't a finding
    assert best_possible_p(2, 84, 147) > 0.05 and best_possible_p(23, 84, 147) < 0.001

def test_signals_count_rising_fading_methods_claims_and_skip_forced_terms():
    periods = quarters(date(2026, 11, 1), 12)
    sampled = []
    for e in entries():
        published = date.fromisoformat(e["published"])
        period = next(p.label for p in periods if p.start <= published <= p.end)
        paper = Paper(paper_id=f"arxiv:{e['id']}", title=e["title"], abstract=e["summary"],
                      publication_date=e["published"])
        sampled.append(Sampled(paper=paper, period=period, found_by={"scene editing": 1}))
    signals = compute_signals(sampled, periods, ["scene editing"], date(2026, 11, 1))
    rising = {t["label"]: t for t in signals["terms"] if t["kind"] in ("rising", "emerging")}
    fading = {t["label"] for t in signals["terms"] if t["kind"] == "fading"}
    assert any("gaussian splatting" in label for label in rising)
    assert any("neural radiance field" in label for label in fading)
    assert not any(label == "scene editing" for label in rising)  # the search itself forces it
    gaussian = next(t for label, t in rising.items() if "gaussian splatting" in label)
    assert gaussian["kind"] == "emerging" and gaussian["support"] == "strong" and gaussian["papers"]
    assert signals["terms_tested"] >= len(signals["terms"]) and signals["terms_strong"] >= 1
    methods = {m["label"]: m for m in signals["methods"]}
    assert methods["InstructPix2Pix"]["papers_naming"] == 18
    assert methods["GaussianEditor"]["early"][0] == 0 and methods["GaussianEditor"]["recent"][0] == 10
    assert signals["claims"]["count"] == 18 and ("GaussianEditor", 10) in signals["claims"]["methods_named"]


# ----------------------------------------------------------------------------- end to end


def test_trends_report_packet_and_recorded_directions(arxiv_field, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run_trends(arxiv_field, "scene", "editing", "--months", "12")
    assert code == 0, out
    assert "**18 papers sampled from 1 search over 5 quarters (2025-10-01 → 2026-11-01).**" in out
    assert "## Rising terms" in out and "(new)" in out and "## Fading terms" in out
    assert "## State-of-the-art claims (the authors' own, not verified)" in out and "InstructPix2Pix" in out
    assert "2026-Q4*" in out and "Saved full report" in out
    run_id = run_id_of(out)

    code, out = run_trends(arxiv_field, "packet", "--run", run_id)
    assert code == 0 and "trend packet" in out
    packet = json.loads((tmp_path / "proposals" / f"trends-{run_id}" / "packet.json").read_text(encoding="utf-8"))
    ids = {s["id"]: s for s in packet["signals"]}
    term = next(i for i, s in ids.items() if s["kind"] == "emerging")
    paper = ids[term]["papers"][0]
    assert "claims" in ids and "untrusted" in packet and packet["papers"][0]["abstract"]

    def analysis(**changes):
        direction = {"title": "Editing moves to Gaussian splatting", "kind": "emerging",
                     "statement": "Recent papers in the sample edit Gaussian splats instead of radiance fields.",
                     "evidence": [term, "method:GaussianEditor"], "papers": [paper], "confidence": "high",
                     "for_your_project": "Reviewers may ask why the manuscript uses NeRF."}
        direction.update(changes)
        path = tmp_path / "analysis.json"
        path.write_text(json.dumps({"run_id": run_id, "model": "claude-test", "prompt_version": "trend-analyst-v1",
                                    "directions": [direction], "caveats": ["Synthetic sample."]}), encoding="utf-8")
        return run_trends(arxiv_field, "record", "--run", run_id, "--file", str(path))

    code, out = analysis(evidence=["term:not a signal"])
    assert code == 1 and "aren't in the packet" in out
    code, out = analysis(papers=["arxiv:9999.99999"])
    assert code == 1 and "aren't in run" in out
    code, out = analysis(evidence=[term])
    assert code == 1 and "only one signal" in out
    code, out = analysis(statement="GaussianEditor is the new state of the art for 3D scene editing.")
    assert code == 1 and "attribute it" in out

    code, out = analysis(statement="Ten recent papers claim state-of-the-art results against GaussianEditor.")
    assert code == 0, out
    assert "Stored the trend analysis" in out and "## Directions" in out
    assert "**1. Editing moves to Gaussian splatting** · emerging · high confidence" in out
    assert "- **For your project:** Reviewers may ask why the manuscript uses NeRF." in out
    analysis_run = out.split("as run ")[1].split(".")[0]
    code, out = run_trends(arxiv_field, "show", "--run", analysis_run)
    assert code == 0 and "## Directions" in out and f"signals run `{run_id}`" in out
    code, out = run_trends(arxiv_field, "packet", "--run", analysis_run)
    assert code == 1 and "already holds an analysis" in out


def test_saved_searches_and_category_volume(arxiv_field):
    arxiv_field.save_profile(ResearchProfile(name="Editing", research_question="How to edit scenes?",
                                             search_queries=["scene editing", "radiance field editing"],
                                             arxiv_categories=["cs.CV"]), activate=True)
    code, out = run_trends(arxiv_field)
    assert code == 0, out
    assert "counted the 2 saved searches" in out and "## Search volume per quarter" in out
    assert "| all papers in cs.CV |" in out and "limited to your profile's arXiv categories: cs.CV" in out
    # 2 searches x 5 quarters + 5 category totals is more than the default per-command budget of 10.
    assert "failed and was skipped" not in out and "| ? |" not in out


def test_a_search_missing_a_quarter_is_left_out_of_the_counts(arxiv_field, monkeypatch):
    arxiv_field.save_profile(ResearchProfile(name="Editing", research_question="How to edit scenes?",
                                             search_queries=["scene editing", "radiance field editing"]), activate=True)
    real = arxiv_field.arxiv.query

    def flaky(**kwargs):
        if "radiance" in kwargs.get("search_query", "") and "20260701" in kwargs["search_query"]:
            raise ApiError("arXiv HTTP 503: synthetic outage", 503)
        return real(**kwargs)

    monkeypatch.setattr(arxiv_field.arxiv, "query", flaky)
    code, out = run_trends(arxiv_field)
    assert code == 0, out
    assert "Left out of the counts because a quarter is missing: radiance field editing." in out


def test_runs_that_need_too_many_requests_are_refused_up_front(arxiv_field):
    arxiv_field.save_profile(ResearchProfile(name="Wide", research_question="q?",
                                             search_queries=[f"scene editing {n}" for n in range(7)]), activate=True)
    code, out = run_trends(arxiv_field, "--months", "36")
    assert code == 1 and "one trends run may send 80" in out


def test_trend_samples_do_not_mark_papers_as_seen(arxiv_field):
    assert run_trends(arxiv_field, "scene", "editing")[0] == 0
    report = arxiv_field.discover("scene editing")
    assert report.items and not any(i.seen_before for i in report.items)


def test_trends_usage_errors(arxiv_field):
    assert run_trends(arxiv_field, "show")[0] == cli.EXIT_USAGE  # an action without --run
    assert run_trends(arxiv_field, "scene", "editing", "--run", "x")[0] == cli.EXIT_USAGE
    assert run_trends(arxiv_field)[0] == cli.EXIT_USAGE  # no topic and no profile
    code, out = run_trends(arxiv_field, "show", "--run", "run_missing")
    assert code == 1 and "No run" in out


def test_trends_with_semantic_scholar(svc, editing_profile):
    code, out = run_trends(svc, "text-guided", "gaussian", "scene", "editing", "--months", "12")
    assert code in (0, 1), out
    if code == 0:
        assert "papers sampled" in out and "arXiv has no reference lists" not in out
    else:
        assert "No papers found" in out


def test_a_short_arxiv_page_is_fetched_again_without_the_cache(arxiv_field, monkeypatch):
    real = arxiv_field.arxiv.query
    refreshed = []

    def flaky(**kwargs):
        total, papers = real(**{k: v for k, v in kwargs.items() if k != "refresh"})
        if kwargs.get("refresh"):
            refreshed.append(kwargs["search_query"])
            return total, papers
        return total, papers[:1]  # arXiv reports `total` matches but sends one entry

    monkeypatch.setattr(arxiv_field.arxiv, "query", flaky)
    code, out = run_trends(arxiv_field, "scene", "editing")
    assert code == 0 and refreshed and "**18 papers sampled" in out
