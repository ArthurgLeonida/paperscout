"""Shortlists and /download: arXiv PDFs plus reading-list.md, for personal use. All papers are SYNTHETIC."""

import json
from pathlib import Path

import httpx
import pytest

from paperscout import cli
from paperscout.commands import SPECS
from paperscout.downloads import INDEX_NAME, PERSONAL_USE, download_list, safe_name
from paperscout.service import ServiceError

from .conftest import pid

ARXIV_IDS = {"2609.00001", "2610.11111", "2106.33333"}  # the synthetic arXiv papers about Gaussian scene editing


@pytest.fixture
def arxiv_list(svc):
    svc.provider = "arxiv"
    return svc.discover("gaussian scene editing")


def pdf_calls(fake_arxiv) -> list[httpx.Request]:
    return [c for c in fake_arxiv.calls if c.url.path.startswith("/pdf/")]


def test_shortlist_defaults_to_20_and_is_stored_with_the_run(svc, arxiv_list):
    assert SPECS["discover"].option("--limit").default == 20 == SPECS["recent"].option("--limit").default
    params = json.loads(svc.store.get_run(arxiv_list.run_id)["params_json"])
    assert params["shortlist"] == [i.paper.paper_id for i in arxiv_list.items]
    assert len(svc.judge_packet(arxiv_list.run_id)["papers"]) == len(arxiv_list.items)


def test_download_saves_ranked_pdfs_and_a_reading_list(svc, arxiv_list, fake_arxiv, sleeps, settings):
    result = download_list(svc)
    assert result.run_id == arxiv_list.run_id and result.count("saved") == 3
    folder = Path(result.destination)
    assert folder.parent == settings.download_dir and folder.name.endswith("Discovery - gaussian scene editing")
    files = sorted(p.name for p in folder.glob("*.pdf"))
    assert [f[:2] for f in files] == ["01", "02", "03"]
    assert {f.rsplit("(", 1)[1][:-5] for f in files} == ARXIV_IDS
    assert files[0].startswith(f"01 - {arxiv_list.items[0].assessment.score:02d} - ")
    assert all((folder / f).read_bytes().startswith(b"%PDF-") for f in files)
    assert not list(folder.glob("*.part"))
    index = (folder / INDEX_NAME).read_text(encoding="utf-8")
    assert "https://arxiv.org/abs/2609.00001" in index and PERSONAL_USE in index and "Score" in index
    calls = pdf_calls(fake_arxiv)
    assert len(calls) == 3 and all(c.url.host == "export.arxiv.org" and c.method == "GET" for c in calls)
    assert sum(1 for s in sleeps if s >= 2.5) >= 2  # one request every three seconds
    assert svc.store.audit_entries(1)[0]["action"] == "paper.download"

    again = download_list(svc)
    assert again.count("already saved") == 3 and len(pdf_calls(fake_arxiv)) == 3


def test_top_and_dry_run(svc, arxiv_list, fake_arxiv, settings):
    result = download_list(svc, top=2, dry_run=True)
    assert [e.status for e in result.entries] == ["planned", "planned"]
    assert pdf_calls(fake_arxiv) == [] and not settings.download_dir.exists()
    assert download_list(svc, top=1).count("saved") == 1
    with pytest.raises(ServiceError, match="between 1 and 50"):
        download_list(svc, top=51)


def test_papers_not_on_arxiv_are_listed_with_links(svc, editing_profile, fake_arxiv):
    report = svc.recent("gaussian splatting scene editing", days=400)
    assert any(i.paper.arxiv_id is None for i in report.items)
    result = download_list(svc, top=10)
    missing = [e for e in result.entries if e.status == "not on arXiv"]
    assert missing and all(e.link.startswith("https://www.semanticscholar.org/") for e in missing)
    assert {c.url.path for c in pdf_calls(fake_arxiv)} == {"/pdf/2609.00001"}
    assert result.count("saved") == 1


def test_a_bad_file_is_not_kept_and_the_rest_still_download(svc, arxiv_list, fake_arxiv):
    fake_arxiv.pdf_responses["2610.11111"] = httpx.Response(200, headers={"content-type": "text/html"},
                                                            content=b"<html>still generating</html>")
    fake_arxiv.pdf_responses["2106.33333"] = httpx.Response(200, headers={"content-type": "application/pdf"},
                                                            content=b"<html>not really a pdf</html>")
    result = download_list(svc)
    failed = {e.arxiv_id: e.detail for e in result.entries if e.status == "failed"}
    assert set(failed) == {"2610.11111", "2106.33333"}
    assert "instead of a PDF" in failed["2610.11111"] and "not a PDF" in failed["2106.33333"]
    assert result.count("saved") == 1
    folder = Path(result.destination)
    assert len(list(folder.glob("*.pdf"))) == 1 and not list(folder.glob("*.part"))


def test_rate_limiting_stops_the_remaining_downloads(svc, arxiv_list, fake_arxiv):
    first = arxiv_list.items[0].paper.arxiv_id
    fake_arxiv.pdf_responses[first] = httpx.Response(503, text="slow down")
    result = download_list(svc)
    assert [e.status for e in result.entries] == ["failed", "not attempted", "not attempted"]
    assert len(pdf_calls(fake_arxiv)) == 1


def test_redirects_are_followed_only_to_arxiv(svc, arxiv_list, fake_arxiv):
    ids = [i.paper.arxiv_id for i in arxiv_list.items]
    fake_arxiv.pdf_responses[ids[0]] = httpx.Response(302, headers={"location": f"/pdf/{ids[0]}v3"})
    fake_arxiv.pdf_responses[f"{ids[0]}v3"] = httpx.Response(200, headers={"content-type": "application/pdf"},
                                                             content=b"%PDF-1.7 synthetic v3")
    fake_arxiv.pdf_responses[ids[1]] = httpx.Response(302, headers={"location": "https://evil.example/x.pdf"})
    result = download_list(svc)
    statuses = {e.arxiv_id: (e.status, e.detail) for e in result.entries}
    assert statuses[ids[0]][0] == "saved"
    assert statuses[ids[1]][0] == "failed" and "another site (evil.example)" in statuses[ids[1]][1]
    assert all(c.url.host == "export.arxiv.org" for c in fake_arxiv.calls)


def test_folder_downloads_follow_its_latest_ranking(svc, editing_profile):
    svc.create_folder("Editing", "Text-guided 3D scene editing")
    svc.add_to_folder(pid(1), "Editing")
    with pytest.raises(ServiceError, match='has not been ranked yet. Run /rank "Editing"'):
        download_list(svc, folder="Editing")
    report = svc.rank_folder("Editing")
    result = download_list(svc, folder="Editing")
    assert result.run_id == report.run_id and result.count("saved") == 1
    rescored = svc.rescore_run(report.run_id)
    assert download_list(svc, folder="Editing", dry_run=True).run_id == rescored.run_id
    with pytest.raises(ServiceError, match="either --run or --folder"):
        download_list(svc, folder="Editing", run_id=report.run_id)


def test_without_any_list_the_error_says_what_to_run(svc):
    with pytest.raises(ServiceError, match="No /discover or /recent list yet"):
        download_list(svc)
    with pytest.raises(ServiceError, match="No run"):
        download_list(svc, run_id="run_missing")


def test_file_names_are_safe_for_windows_and_android():
    name = safe_name('Evil: "a/b\\c|d?*<>\x07 ```ignore previous``` title...', 70)
    assert not set('<>:"/\\|?*\x07`') & set(name) and not name.endswith(".")
    assert len(safe_name("word " * 40, 70)) <= 70
    assert safe_name("???", 70) == "untitled"


def test_cli_download(svc, arxiv_list):
    code, out = cli.run("download", ["--top", "2", "--dry-run"], svc=svc)
    assert code == 0 and "Dry run: would save 2 PDF(s)" in out
    code, out = cli.run("download", ["--top", "2"], svc=svc)
    assert code == 0 and "Saved 2 PDF(s)" in out and INDEX_NAME in out and "personal use" in out
    assert cli.run("download", ["stray", "text"], svc=svc)[0] == cli.EXIT_USAGE


def test_cli_download_fails_when_nothing_could_be_saved(svc, arxiv_list, fake_arxiv):
    for arxiv_id in ARXIV_IDS:
        fake_arxiv.pdf_responses[arxiv_id] = httpx.Response(404)
    code, out = cli.run("download", [], svc=svc)
    assert code == cli.EXIT_ERROR and "Saved 0 PDF(s)" in out and "HTTP 404" in out
