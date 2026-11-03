from dataclasses import replace
from datetime import date

import httpx
import pytest

from paperscout import cli, doctor
from paperscout.arxiv.client import ArxivClient, build_search_query, parse_feed
from paperscout.config import load_settings, normalize_provider
from paperscout.http import ApiBadRequest, ApiError
from paperscout.models import ResearchProfile
from paperscout.service import ServiceError

from .conftest import ARXIV_ENTRIES, atom_feed, pid

ERROR_FEED = (
    '<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
    "<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_1234.12345</id><title>Error</title>"
    "<summary>incorrect id format for 1234.12345</summary></entry></feed>"
)


@pytest.fixture
def arxiv_svc(svc):
    svc.provider = "arxiv"
    return svc


# ----------------------------------------------------------------------------- query building & parsing


def test_build_search_query():
    q = build_search_query("text-guided 3D Gaussian scene editing", window=(date(2026, 8, 3), date(2026, 11, 1)),
                           categories=["cs.CV", "cs.GR", "bad category!"])
    assert q == ("(all:text AND all:guided AND all:3d AND all:gaussian AND all:scene AND all:editing) "
                 "AND (cat:cs.CV OR cat:cs.GR) AND submittedDate:[202608030000 TO 202611012359]")
    assert build_search_query("diffusion models", mode="any") == "(all:diffusion OR all:models)"
    with pytest.raises(ValueError):
        build_search_query("the of and")


def test_parse_feed_maps_metadata():
    total, papers = parse_feed(atom_feed([ARXIV_ENTRIES[1]]))
    p = papers[0]
    assert total == 1
    assert p.paper_id == "arxiv:2610.11111" and p.arxiv_id == "2610.11111"  # version suffix stripped
    assert p.doi == "10.1234/synthetic.mvedit" and p.venue == "Synthetic Conference 2026"
    assert p.publication_date == "2026-10-10" and p.year == 2026
    assert p.abstract.startswith("Multi-view") and not p.abstract.endswith(" ")
    assert p.fields_of_study == ["cs.CV", "cs.GR"]
    assert p.link == "https://arxiv.org/abs/2610.11111"
    assert p.citation_count is None  # unknown, not zero
    assert [a.name for a in p.authors] == ["Ada Example", "Bo Sample"]
    _, plain = parse_feed(atom_feed([ARXIV_ENTRIES[2]]))
    assert plain[0].venue == "arXiv preprint (eess.IV)" and plain[0].doi is None


def test_parse_feed_rejects_errors_and_dtds():
    with pytest.raises(ApiBadRequest, match="incorrect id format"):
        parse_feed(ERROR_FEED)
    with pytest.raises(ApiError, match="DTD"):
        parse_feed('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed/>')
    with pytest.raises(ApiError, match="not valid Atom"):
        parse_feed("<feed><entry>")


# ----------------------------------------------------------------------------- client behaviour


def make_client(settings, handler, sleeps, **overrides):
    return ArxivClient(replace(settings, **overrides), transport=httpx.MockTransport(handler), sleep=sleeps.append)


def test_pacing_never_faster_than_arxiv_terms(settings, fake_arxiv, sleeps):
    clock = iter([0.0, 0.5, 0.5])
    client = ArxivClient(replace(settings, arxiv_min_interval=0.0), transport=httpx.MockTransport(fake_arxiv),
                         sleep=sleeps.append, monotonic=lambda: next(clock))
    client.get_papers(["2610.11111"])
    client.get_papers(["2610.22222"])
    assert sleeps == [pytest.approx(2.5)]  # 3 s minimum even though the setting asked for 0


def test_widens_to_any_term_when_all_terms_find_little(settings, fake_arxiv, sleeps):
    client = make_client(settings, fake_arxiv, sleeps)
    hits = client.search_papers("gaussian scene editing quantum", max_results=10)
    assert len(fake_arxiv.queries) == 2 and " OR " in fake_arxiv.queries[1]
    assert {p.arxiv_id for _, p in hits} >= {"2609.00001", "2610.11111"}
    assert [rank for rank, _ in hits] == list(range(1, len(hits) + 1))


def test_get_papers_is_aligned_with_input(settings, fake_arxiv, sleeps):
    client = make_client(settings, fake_arxiv, sleeps)
    found = client.get_papers(["2610.22222v3", "2001.99999", "2610.11111"])
    assert [p.arxiv_id if p else None for p in found] == ["2610.22222", None, "2610.11111"]


def test_arxiv_error_feed_over_http_400(settings, sleeps):
    client = make_client(settings, lambda r: httpx.Response(400, text=ERROR_FEED), sleeps)
    with pytest.raises(ApiBadRequest, match="incorrect id format"):
        client.query(id_list=["1234.12345"])


def test_arxiv_budget_and_rate_limit_advice(settings, sleeps):
    client = make_client(settings, lambda r: httpx.Response(503, text="busy"), sleeps,
                         arxiv_max_retries=5, arxiv_max_requests=2)
    with pytest.raises(ApiError, match="budget of 2 HTTP attempts to arXiv"):
        client.get_papers(["2610.11111"])
    limited = make_client(settings, lambda r: httpx.Response(429, text="slow down"), sleeps, arxiv_max_retries=0)
    with pytest.raises(ApiError, match="one request every three seconds"):
        limited.get_papers(["2610.11111"])


# ----------------------------------------------------------------------------- provider selection


def test_effective_provider(settings, monkeypatch):
    assert replace(settings, provider="auto", s2_api_key=None).effective_provider == "arxiv"
    assert replace(settings, provider="auto", s2_api_key="k").effective_provider == "semantic-scholar"
    assert replace(settings, provider="arxiv", s2_api_key="k").effective_provider == "arxiv"
    assert normalize_provider("S2") == "semantic-scholar"
    with pytest.raises(ValueError, match="Unknown provider"):
        normalize_provider("scholar")


def test_load_settings_reads_provider_and_clamps_interval(tmp_path, monkeypatch):
    monkeypatch.setattr("paperscout.config.project_root", lambda: None)
    for name in ("S2_API_KEY", "PAPERSCOUT_PROVIDER", "PAPERSCOUT_ARXIV_MIN_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPERSCOUT_PROVIDER", "semantic-scholar")
    monkeypatch.setenv("PAPERSCOUT_ARXIV_MIN_INTERVAL", "0.5")
    settings = load_settings(tmp_path)
    assert settings.effective_provider == "semantic-scholar" and settings.arxiv_min_interval == 3.0


# ----------------------------------------------------------------------------- service on arXiv


def test_recent_on_arxiv_uses_v1_dates_and_never_calls_s2(arxiv_svc, editing_profile, fake_s2):
    report = arxiv_svc.recent("gaussian scene editing", days=120)
    assert "arXiv API" in report.source
    assert report.date_window["basis"].startswith("arXiv submission date of the first version")
    ids = [i.paper.paper_id for i in report.items]
    assert "arxiv:2610.11111" in ids and "arxiv:2106.33333" not in ids  # outside the window
    item = next(i for i in report.items if i.paper.paper_id == "arxiv:2610.11111")
    assert item.provenance[0].source == "arxiv.search"
    assert any("arXiv provides no citation counts" in n for n in report.notices)
    assert fake_s2.calls == []


def test_profile_categories_restrict_arxiv_search(arxiv_svc, editing_profile, fake_arxiv):
    arxiv_svc.save_profile(editing_profile.model_copy(update={"arxiv_categories": ["cs.CV"]}))
    report = arxiv_svc.recent("gaussian scene editing", days=120)
    assert "cat:cs.CV" in fake_arxiv.queries[0]
    assert any("categories: cs.CV" in n for n in report.notices)


def test_discover_on_arxiv_states_missing_graph_features(arxiv_svc, editing_profile, fake_s2):
    report = arxiv_svc.discover("gaussian scene editing", limit=5)
    assert report.items and fake_s2.calls == []
    old = next((i for i in report.items if i.paper.paper_id == "arxiv:2106.33333"), None)
    if old is not None:
        assert any("Citation count unknown" in u for u in old.assessment.uncertainties)


def test_same_paper_from_both_providers_stays_one_record(svc, editing_profile):
    svc.require_paper(pid(1))  # stored from Semantic Scholar; its arXiv ID is 2609.00001
    svc.record_feedback(pid(1), "understood")
    svc.provider = "arxiv"
    report = svc.recent("text guided gaussian scene editing", days=120)
    assert "arxiv:2609.00001" not in {i.paper.paper_id for i in report.items}
    assert any(e.paper.paper_id == pid(1) and "understood" in e.reason for e in report.excluded)
    merged = next(e.paper for e in report.excluded if e.paper.paper_id == pid(1))
    assert merged.citation_count == 3 and merged.external_ids["ArXiv"] == "2609.00001"


def test_resolution_on_arxiv(arxiv_svc, fake_s2):
    paper = arxiv_svc.require_paper("https://arxiv.org/abs/2610.22222v1")
    assert paper.paper_id == "arxiv:2610.22222" and fake_s2.calls == []
    by_title = arxiv_svc.resolve("[SYNTHETIC] Multi-View Consistent Gaussian Scene Editing")
    assert by_title.status == "resolved" and by_title.paper.arxiv_id == "2610.11111"
    fake_s2.fail_queue = [httpx.Response(429, json={"message": "Too Many Requests"})] * 4
    res = arxiv_svc.resolve("10.1145/3592433")
    assert res.status == "not_found" and "use an arXiv ID or the paper title" in res.message


def test_seeds_on_arxiv(arxiv_svc, fake_s2):
    profile = ResearchProfile(name="Seeds", field="3D scene editing", keywords=["gaussian splatting"],
                              positive_seeds=["arXiv:2610.11111", "10.1145/3592433"])
    arxiv_svc.save_profile(profile, activate=True)
    report = arxiv_svc.recent("gaussian scene editing", days=120)
    assert any("give seeds as arXiv IDs" in n for n in report.notices)
    assert any("text similarity only" in n for n in report.notices)
    assert fake_s2.calls == []


def test_import_on_arxiv_falls_back_to_titles(arxiv_svc, tmp_path, fake_s2):
    arxiv_svc.create_folder("Imported", "imported papers")
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{a, title={Whatever}, eprint={2610.22222}, archivePrefix={arXiv}}\n"
        "@inproceedings{b, title={[SYNTHETIC] Multi-View Consistent Gaussian Scene Editing}, doi={10.9999/x}}\n"
        "@misc{c, note={nothing usable}}\n",
        encoding="utf-8",
    )
    result = arxiv_svc.import_file(bib, "Imported")
    assert len(result["added"]) == 2 and fake_s2.calls == []
    assert result["problems"]


def test_cli_provider_override_and_unknown_value(svc, editing_profile, fake_s2):
    code, out = cli.run("recent", ["gaussian", "scene", "editing", "--days", "120", "--provider", "arxiv"], svc=svc)
    assert code == 0 and "arXiv API" in out and fake_s2.calls == []
    code, out = cli.run("recent", ["x", "--provider", "scholar"], svc=svc)
    assert code == 2 and "must be one of" in out


def test_folder_proposal_sample_uses_active_provider(arxiv_svc, editing_profile, fake_s2):
    from paperscout.taxonomy import proposal_context

    ctx = proposal_context(arxiv_svc, "gaussian scene editing")
    assert ctx["sample_papers"] and "arXiv" in ctx["sample_note"] and fake_s2.calls == []


def test_doctor_probes_arxiv_with_one_request(settings, fake_arxiv):
    report = doctor.run_doctor(replace(settings, provider="arxiv"), live=True,
                               transport=httpx.MockTransport(fake_arxiv))
    assert report["live_probe"]["provider"] == "arXiv" and len(fake_arxiv.calls) == 1
    assert report["live_probe"]["ok"] is False  # the probe paper is not in the synthetic fixtures
    assert "Paper provider: arxiv" in doctor.render(report)


def test_service_errors_are_clear_when_arxiv_fails(arxiv_svc, fake_arxiv, monkeypatch):
    monkeypatch.setattr(fake_arxiv, "__call__", None, raising=False)

    def broken(request):
        return httpx.Response(503, text="maintenance")

    arxiv_svc.arxiv._http = httpx.Client(transport=httpx.MockTransport(broken))
    with pytest.raises(ServiceError, match="arXiv search failed"):
        arxiv_svc.recent("gaussian scene editing", days=30)


def test_close_is_idempotent(svc):
    svc.close()
    svc.close()
