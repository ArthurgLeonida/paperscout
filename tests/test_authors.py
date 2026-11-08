"""/authors: papers by specific researchers through OpenAlex. All people and papers are SYNTHETIC."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from paperscout import cli
from paperscout.arxiv.client import ArxivClient
from paperscout.authors import name_forms, parse_targets
from paperscout.models import ResearchProfile
from paperscout.openalex.client import OpenAlexClient, abstract_from_index, paper_from_work
from paperscout.s2.client import SemanticScholarClient
from paperscout.service import Paperscout
from paperscout.store.db import Store, connect

from .conftest import TODAY

UNI = {"id": "https://openalex.org/I100", "display_name": "Synthetic Institute of Technology"}
OTHER = {"id": "https://openalex.org/I200", "display_name": "Other University"}


def abstract(text: str) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, word in enumerate(text.split()):
        index.setdefault(word, []).append(position)
    return index


def work(wid, title, date_, authorships, *, arxiv=None, doi=None, text="", cited=0, source="arXiv (Cornell University)"):
    locations = [{"landing_page_url": f"https://arxiv.org/abs/{arxiv}"}] if arxiv else []
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None,
            "display_name": f"[SYNTHETIC] {title}", "publication_date": date_, "publication_year": int(date_[:4]),
            "cited_by_count": cited, "authorships": authorships, "locations": locations, "type": "article",
            "primary_location": {"source": {"display_name": source}},
            "abstract_inverted_index": abstract(text) if text else None}


def by(raw, display, *institutions, aid="A1"):
    return {"raw_author_name": raw, "author": {"id": f"https://openalex.org/{aid}", "display_name": display},
            "institutions": list(institutions)}


EDITING = "We propose text-guided editing of 3D Gaussian splatting scenes that preserves the background."
WORKS = {
    "kim": [
        work("W1", "Text-Guided Gaussian Scene Editing", "2026-09-10", [by("Kim, Alex", "Alex Kim", UNI)],
             arxiv="2609.11111", text=EDITING, cited=4),
        # The published version of W1: merged with it.
        work("W2", "Text-Guided Gaussian Scene Editing", "2026-10-01", [by("Alex Kim", "Alex Kim", UNI)],
             doi="10.1234/synthetic.w1", text=EDITING, cited=6, source="Synthetic Conference"),
        # The name is on the paper, but at another institution; a co-author is at UNI: left out.
        work("W3", "Unrelated Protein Folding Study", "2026-05-02",
             [by("Alex Kim", "Alex Kim", OTHER, aid="A9"), by("Bo Park", "Bo Park", UNI, aid="A5")],
             doi="10.1234/synthetic.w3", text="Protein folding with transformers.", source="Synthetic Journal"),
        # No institution listed for the named author; a co-author is at UNI: kept, and counted as "unlisted".
        work("W5", "Gaussian Splatting Editing Without Affiliations", "2026-04-04",
             [by("Kim, Alex", "Alex Kim"), by("Bo Park", "Bo Park", UNI, aid="A5")], arxiv="2604.55555",
             text="Text-guided editing of Gaussian splatting scenes."),
        work("W4", "Background-Preserving Scene Editing Study", "2025-06-20", [by("Alex Kim", "Alex Kim", UNI)],
             doi="10.1234/synthetic.w4", text="Text-guided scene editing of Gaussian splatting that preserves the "
             "background.", source="Synthetic Journal"),
    ],
    "A77": [work("W7", "Multi-View Consistent Editing with Diffusion Guidance", "2026-08-01",
                 [by("Jo Lee", "Jo Lee", OTHER, aid="A77")], arxiv="2608.77777",
                 text="Multi-view consistent editing of Gaussian splatting scenes with diffusion guidance.")],
}
AUTHORS = {"jo lee": [{"id": "https://openalex.org/A77", "display_name": "Jo Lee", "works_count": 120,
                       "last_known_institutions": [OTHER]},
                      {"id": "https://openalex.org/A78", "display_name": "Jo Lee", "works_count": 3},
                      {"id": "https://openalex.org/A79", "display_name": "Joanna Leeds", "works_count": 50}]}


class FakeOpenAlex:
    def __init__(self):
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        params = request.url.params
        path = request.url.path
        if path.endswith("/institutions"):
            found = [{**UNI, "display_name_acronyms": ["SIT"]}] if params["search"] == "SIT" else []
            return httpx.Response(200, json={"meta": {"count": len(found)}, "results": found})
        if path.endswith("/authors"):
            return httpx.Response(200, json={"meta": {}, "results": AUTHORS.get(params["search"].lower(), [])})
        if path.endswith("/works"):
            filters = dict(f.split(":", 1) for f in params["filter"].split(","))
            if "raw_author_name.search" in filters:
                results = WORKS["kim"] if filters["raw_author_name.search"].lower() == "alex kim" else []
            else:
                results = WORKS.get(filters.get("author.id", ""), [])
            results = [w for w in results if filters["from_publication_date"] <= w["publication_date"]
                       <= filters["to_publication_date"]]
            return httpx.Response(200, json={"meta": {"count": len(results), "next_cursor": None},
                                             "results": results},
                                  headers={"X-RateLimit-Remaining-USD": "0.09", "X-RateLimit-Limit-USD": "0.1"})
        return httpx.Response(404, json={"error": "unhandled"})


@pytest.fixture
def fake_oa() -> FakeOpenAlex:
    return FakeOpenAlex()


@pytest.fixture
def asvc(settings, fake_s2, fake_arxiv, fake_oa, sleeps, editing_profile):
    s = replace(settings, provider="arxiv", openalex=True)
    store = Store(connect(s.db_path))
    service = Paperscout(
        s, store,
        SemanticScholarClient(s, cache=store, transport=httpx.MockTransport(fake_s2), sleep=sleeps.append),
        today=TODAY,
        arxiv=ArxivClient(s, cache=store, transport=httpx.MockTransport(fake_arxiv), sleep=sleeps.append),
        openalex=OpenAlexClient(s, cache=store, transport=httpx.MockTransport(fake_oa), sleep=sleeps.append),
    )
    service.save_profile(editing_profile.model_copy(update={"id": ""}), activate=True)
    yield service
    service.close()


def run(svc, *tokens):
    return cli.run("authors", list(tokens), svc=svc)


# ----------------------------------------------------------------------------- pure helpers


def test_name_forms_match_common_spellings():
    assert name_forms("Ye, Jong-Chul") & name_forms("Jong Chul Ye") & name_forms("Jongchul Ye")
    assert name_forms("Kim Seungryong") & name_forms("Seungryong Kim")
    assert not name_forms("Alex Kim") & name_forms("Alexa Kim")


def test_targets_split_on_separators_quotes_and_affiliations():
    targets = parse_targets(["Alex", "Kim;", "Jo", "Lee", "@", "MIT;", "A5085363061"], "SIT")
    assert [(t.name, t.author_id, t.affiliation) for t in targets] == [
        ("Alex Kim", None, "SIT"), ("Jo Lee", None, "MIT"), (None, "A5085363061", "SIT")]
    assert [t.name for t in parse_targets(["Alex Kim", "Jo Lee"], None)] == ["Alex Kim", "Jo Lee"]
    assert [t.name for t in parse_targets(["Alex", "Kim"], None)] == ["Alex Kim"]


def test_works_become_papers_with_arxiv_ids_and_rebuilt_abstracts():
    paper = paper_from_work(WORKS["kim"][0])
    assert paper.paper_id == "arxiv:2609.11111" and paper.arxiv_id == "2609.11111" and paper.venue == "arXiv preprint"
    assert paper.abstract == EDITING and paper.citation_count == 4 and paper.citation_source == "openalex-works"
    w4 = next(w for w in WORKS["kim"] if w["id"].endswith("/W4"))
    assert paper_from_work(w4).paper_id == "doi:10.1234/synthetic.w4"
    assert abstract_from_index({"b": [1], "a": [0]}) == "a b" and abstract_from_index(None) is None


# ----------------------------------------------------------------------------- end to end


def test_papers_by_name_and_affiliation_are_matched_on_each_paper(asvc, fake_oa):
    code, out = run(asvc, "Alex", "Kim", "--affiliation", "SIT")
    assert code == 0, out
    assert "# Papers by Alex Kim (SIT)" in out and "## Researchers" in out
    assert "| Alex Kim | Alex Kim on each paper's author list, with Synthetic Institute of Technology |" in out
    assert "1 search hit(s) left out" in out  # W3: the name is listed with another institution
    assert "1 matched with no institution listed" in out and "Without Affiliations" in out  # W5
    assert "Protein Folding" not in out
    assert out.count("Text-Guided Gaussian Scene Editing") >= 1 and "duplicates_merged=1" in out
    rows = [line for line in out.splitlines() if line.startswith("| 1 | ")]
    assert "Text-Guided Gaussian Scene Editing" in rows[0] and "by Alex Kim" in rows[0]
    assert "Researcher names and affiliations were sent to OpenAlex" in out
    assert "papers by Alex Kim at Synthetic Institute of Technology" in out
    assert "OpenAlex allowance today: $0.090 of $0.10 left (90%; keyless, shared by everyone on your network)" in out
    assert asvc.store.get_paper("arxiv:2609.11111").citation_count == 6  # the published version's count
    assert all(c.method == "GET" and "mailto" not in str(c.url) and "email" not in str(c.url) for c in fake_oa.calls)
    assert asvc.store.get_paper("arxiv:2609.11111").citation_source == "openalex-works"


def test_name_without_affiliation_uses_the_largest_exact_record(asvc):
    code, out = run(asvc, "Jo", "Lee")
    assert code == 0, out
    assert "OpenAlex author A77 (120 works)" in out and "1 other OpenAlex record(s) share this name" in out
    assert "Multi-View Consistent Editing" in out


def test_saved_followed_authors_and_the_window(asvc):
    asvc.save_profile(asvc.resolve_profile(None)[0].model_copy(
        update={"followed_authors": ["Alex Kim @ SIT", "A77"]}), activate=True)
    code, out = run(asvc, "--days", "200")
    assert code == 0, out
    assert "used the 2 followed authors" in out and "Background-Preserving" not in out  # 2025-06 is outside


def test_authors_lists_feed_download(asvc, tmp_path):
    assert run(asvc, "Alex", "Kim", "--affiliation", "SIT")[0] == 0
    code, out = cli.run("download", ["--dry-run", "--out", str(tmp_path)], svc=asvc)
    assert code == 0 and "Papers by Alex Kim" in out


def test_authors_errors(asvc, settings):
    code, out = run(asvc, "Alex", "Kim", "--affiliation", "Nowhere")
    assert code == 0 and "no institution matched 'Nowhere'" in out
    code, out = run(asvc)
    assert code == 1 and "Name at least one researcher" in out
    off = replace(asvc.settings, openalex=False)
    asvc.settings = off
    code, out = run(asvc, "Alex", "Kim")
    assert code == 1 and "PAPERSCOUT_OPENALEX=off" in out


def test_profile_followed_authors_are_cleaned():
    profile = ResearchProfile(name="x", followed_authors=["  Alex   Kim @ SIT ", "alex kim @ sit", ""])
    assert profile.followed_authors == ["Alex Kim @ SIT"]
