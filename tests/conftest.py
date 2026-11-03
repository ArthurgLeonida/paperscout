"""Test fixtures: a fake Semantic Scholar API built on httpx.MockTransport.

All paper records here are SYNTHETIC test fixtures (titles are prefixed with
"[SYNTHETIC]"); they do not describe real publications.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from paperscout.config import Settings
from paperscout.models import ResearchProfile
from paperscout.arxiv.client import ArxivClient
from paperscout.s2.client import SemanticScholarClient
from paperscout.service import Paperscout
from paperscout.store.db import Store, connect

TODAY = date(2026, 11, 1)


def pid(n: int) -> str:
    return f"{n:040x}"


def rec(n, title, *, abstract=None, date_=None, year=None, citations=0, types=None, arxiv=None, doi=None, tldr=None):
    year = year if year is not None else (int(date_[:4]) if date_ else None)
    return {
        "paperId": pid(n),
        "corpusId": 1000 + n,
        "externalIds": {k: v for k, v in {"ArXiv": arxiv, "DOI": doi, "CorpusId": 1000 + n}.items() if v},
        "url": f"https://www.semanticscholar.org/paper/{pid(n)}",
        "title": f"[SYNTHETIC] {title}",
        "abstract": abstract,
        "venue": "Synthetic Venue",
        "year": year,
        "publicationDate": date_,
        "publicationTypes": types,
        "s2FieldsOfStudy": [{"category": "Computer Science", "source": "s2-fos-model"}],
        "citationCount": citations,
        "influentialCitationCount": 0,
        "referenceCount": 10,
        "isOpenAccess": True,
        "openAccessPdf": {"url": f"https://example.org/{n}.pdf", "status": "GREEN"},
        "authors": [{"authorId": "1", "name": "Ada Example"}, {"authorId": "2", "name": "Bo Sample"}],
        "_tldr": tldr,
    }


PAPERS = {
    1: rec(1, "Text-Guided Editing of 3D Gaussian Splatting Scenes",
           abstract="We propose a text-guided method for editing 3D Gaussian splatting scenes using diffusion "
                    "model guidance while preserving the background of the scene.",
           date_="2026-09-15", citations=3, arxiv="2609.00001"),
    2: rec(2, "Background-Preserving Scene Editing with Gaussian Splatting",
           abstract="Scene editing with 3D Gaussian splatting that keeps the background intact under text "
                    "instructions using diffusion priors.",
           date_="2026-08-20", citations=1),
    3: rec(3, "Gaussian Splatting for Real-Time Radiance Field Rendering",
           abstract="We introduce 3D Gaussian splatting, a scene representation for real-time radiance field "
                    "rendering of captured scenes.",
           date_="2023-08-08", citations=2500, doi="10.1234/synthetic.3dgs"),
    4: rec(4, "A Survey of 3D Gaussian Splatting",
           abstract="This survey reviews 3D Gaussian splatting methods for rendering, reconstruction and editing.",
           date_="2025-01-10", citations=150, types=["Review"]),
    5: rec(5, "Diffusion Models for Character Animation of 3D Scenes",
           abstract="Character animation with diffusion models in Gaussian splatting scenes; we focus on animation.",
           date_="2026-10-01"),
    6: rec(6, "Instruction-Driven 3D Scene Editing",
           abstract=None, date_="2026-07-30", tldr="A method for editing 3D scenes from text instructions."),
    7: rec(7, "Language-Guided Gaussian Scene Manipulation",
           abstract="Language guided manipulation and editing of Gaussian splatting scenes.", year=2026),
    8: rec(8, "Medical Image Segmentation with Foundation Models",
           abstract="Segmentation of medical images such as CT and MRI scans with foundation models.",
           date_="2026-09-01"),
    9: rec(9, "Neural Radiance Fields for View Synthesis",
           abstract="Representing scenes as neural radiance fields for novel view synthesis.",
           date_="2020-03-19", citations=8000),
    10: rec(10, "Text-Guided Editing of 3D Gaussian Splatting Scenes",  # same title as 1, different year
            abstract="An earlier workshop version about text-guided Gaussian scene editing.", date_="2025-06-01"),
    11: rec(11, "Gaussian Scene Editing Without a Date",
            abstract="Editing Gaussian splatting scenes; this record has no date at all."),
}

REFERENCES = {pid(1): [3, 9], pid(2): [3, 9, 4], pid(7): [3]}
RECOMMENDATIONS = [2, 7, 1]


def public(record: dict) -> dict:
    data = {k: v for k, v in record.items() if not k.startswith("_")}
    return data


class FakeS2:
    """Routes requests like the documented Graph/Recommendations APIs."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.fail_queue: list[httpx.Response] = []

    def _lookup(self, ident: str) -> dict | None:
        ident = unquote(ident)
        for record in PAPERS.values():
            ext = record["externalIds"]
            if ident == record["paperId"]:
                return record
            if ident.upper().startswith("DOI:") and ext.get("DOI", "").lower() == ident[4:].lower():
                return record
            if ident.upper().startswith("ARXIV:") and ext.get("ArXiv") == ident[6:]:
                return record
            if ident.lower().startswith("corpusid:") and str(record["corpusId"]) == ident.split(":", 1)[1]:
                return record
        return None

    def _detail(self, record: dict) -> dict:
        data = public(record)
        data["tldr"] = {"model": "tldr@v2", "text": record["_tldr"]} if record.get("_tldr") else None
        return data

    @staticmethod
    def _in_window(record: dict, window: str | None) -> bool:
        if not window:
            return True
        start, _, end = window.partition(":")
        if record["publicationDate"]:
            return (not start or record["publicationDate"] >= start) and (not end or record["publicationDate"] <= end)
        if record["year"]:
            return int(start[:4] or 0) <= record["year"] <= int(end[:4] or 9999)
        return False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_queue:
            return self.fail_queue.pop(0)
        path = request.url.path
        params = request.url.params
        if path.endswith("/paper/search"):
            query = params.get("query", "")
            if "-" in query:  # documented: hyphenated query terms yield no matches
                return httpx.Response(200, json={"total": 0, "offset": 0, "data": []})
            terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2]
            scored = []
            for record in PAPERS.values():
                text = f"{record['title']} {record['abstract'] or ''}".lower()
                score = sum(1 for t in terms if t in text)
                if score and self._in_window(record, params.get("publicationDateOrYear")):
                    scored.append((score, record))
            scored.sort(key=lambda s: -s[0])
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100))
            page = [public(r) for _, r in scored[offset:offset + limit]]
            body = {"total": len(scored), "offset": offset, "data": page}
            if offset + limit < len(scored):
                body["next"] = offset + limit
            return httpx.Response(200, json=body)
        if path.endswith("/paper/search/match"):
            query = params.get("query", "").lower()
            for record in PAPERS.values():
                if record["title"].lower().replace("[synthetic] ", "") == query.replace("[synthetic] ", ""):
                    return httpx.Response(200, json={"data": [{**public(record), "matchScore": 200.0}]})
            return httpx.Response(404, json={"error": "Title match not found"})
        if path.endswith("/paper/batch"):
            ids = json.loads(request.content)["ids"]
            return httpx.Response(200, json=[self._detail(r) if (r := self._lookup(i)) else None for i in ids])
        match = re.search(r"/paper/(.+)/references$", path)
        if match:
            refs = REFERENCES.get(match.group(1), [])
            return httpx.Response(200, json={"offset": 0, "data": [{"citedPaper": public(PAPERS[n])} for n in refs]})
        match = re.search(r"/graph/v1/paper/(.+)$", path)
        if match:
            record = self._lookup(match.group(1))
            if record is None:
                return httpx.Response(404, json={"error": "Paper not found"})
            return httpx.Response(200, json=self._detail(record))
        if path.endswith("/recommendations/v1/papers/") and request.method == "POST":
            return httpx.Response(200, json={"recommendedPapers": [public(PAPERS[n]) for n in RECOMMENDATIONS]})
        return httpx.Response(404, json={"error": f"unhandled fake route {path}"})

    def count(self, fragment: str) -> int:
        return sum(1 for c in self.calls if fragment in c.url.path)


# ----------------------------------------------------------------------------- fake arXiv API (SYNTHETIC entries)

ARXIV_ENTRIES = [
    # Same arXiv ID as PAPERS[1], to test that one paper seen through two providers stays one record.
    {"id": "2609.00001", "title": "Text-Guided Editing of 3D Gaussian Splatting Scenes",
     "summary": "We propose a text-guided method for editing 3D Gaussian splatting scenes while preserving the "
                "background of the scene.", "published": "2026-09-15", "cats": ["cs.CV"]},
    {"id": "2610.11111", "title": "Multi-View Consistent Gaussian Scene Editing",
     "summary": "Multi-view consistent editing of Gaussian splatting scenes from text instructions with diffusion "
                "guidance.", "published": "2026-10-10", "cats": ["cs.CV", "cs.GR"],
     "doi": "10.1234/synthetic.mvedit", "journal_ref": "Synthetic Conference 2026"},
    {"id": "2610.22222", "title": "Diffusion Priors for Medical Volume Segmentation",
     "summary": "Segmentation of CT volumes with diffusion priors.", "published": "2026-10-05", "cats": ["eess.IV"]},
    {"id": "2106.33333", "title": "An Older Gaussian Scene Representation Study",
     "summary": "A study of Gaussian scene representations for editing and rendering.", "published": "2021-06-01",
     "cats": ["cs.CV"]},
]


def atom_entry(e: dict) -> str:
    cats = "".join(f'<category term="{c}" scheme="http://arxiv.org/schemas/atom"/>' for c in e["cats"])
    extra = ""
    if e.get("doi"):
        extra += f"<arxiv:doi>{e['doi']}</arxiv:doi>"
    if e.get("journal_ref"):
        extra += f"<arxiv:journal_ref>{e['journal_ref']}</arxiv:journal_ref>"
    return (
        f"<entry><id>http://arxiv.org/abs/{e['id']}v2</id><updated>{e['published']}T10:00:00Z</updated>"
        f"<published>{e['published']}T10:00:00Z</published>"
        f"<title>[SYNTHETIC] {e['title']}</title><summary>  {e['summary']}\n </summary>"
        f"<author><name>Ada Example</name></author><author><name>Bo Sample</name></author>"
        f'<link href="http://arxiv.org/abs/{e["id"]}v2" rel="alternate" type="text/html"/>'
        f'<link title="pdf" href="http://arxiv.org/pdf/{e["id"]}v2" rel="related" type="application/pdf"/>'
        f'<arxiv:primary_category term="{e["cats"][0]}" scheme="http://arxiv.org/schemas/atom"/>{cats}{extra}</entry>'
    )


def atom_feed(entries: list[dict]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        f"<title>ArXiv Query</title><opensearch:totalResults>{len(entries)}</opensearch:totalResults>"
        + "".join(atom_entry(e) for e in entries) + "</feed>"
    )


class FakeArxiv:
    """Implements the subset of the arXiv query API that paperscout uses."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.queries: list[str] = []
        self.pdf_responses: dict[str, httpx.Response] = {}  # arXiv ID -> response overriding the synthetic PDF

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path.startswith("/pdf/"):
            arxiv_id = request.url.path[len("/pdf/"):]
            if arxiv_id in self.pdf_responses:
                return self.pdf_responses[arxiv_id]
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=b"%PDF-1.4 SYNTHETIC test file for " + arxiv_id.encode() + b" %%EOF")
        params = request.url.params
        if params.get("id_list"):
            ids = params["id_list"].split(",")
            found = [e for e in ARXIV_ENTRIES if e["id"] in ids]
            return httpx.Response(200, text=atom_feed(found))
        query = params.get("search_query", "")
        self.queries.append(query)
        cats = re.findall(r"cat:([\w.\-]+)", query)
        window = re.search(r"submittedDate:\[(\d{8})\d{4} TO (\d{8})\d{4}\]", query)
        title_terms = re.findall(r"ti:(\w+)", query)
        terms = re.findall(r"all:(\w+)", query)
        any_mode = " OR all:" in query
        out = []
        for e in ARXIV_ENTRIES:
            full_title = f"[SYNTHETIC] {e['title']}".lower()
            text = f"{full_title} {e['summary']}".lower()
            if title_terms and not all(t in full_title for t in title_terms):
                continue
            if terms and not (any(t in text for t in terms) if any_mode else all(t in text for t in terms)):
                continue
            if cats and not set(cats) & set(e["cats"]):
                continue
            if window and not (window.group(1) <= e["published"].replace("-", "") <= window.group(2)):
                continue
            out.append(e)
        return httpx.Response(200, text=atom_feed(out))


@pytest.fixture
def fake_arxiv() -> FakeArxiv:
    return FakeArxiv()


@pytest.fixture
def fake_s2() -> FakeS2:
    return FakeS2()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path / "home", s2_min_interval=0.0, s2_max_retries=3, s2_max_backoff=30.0,
                    provider="semantic-scholar", download_dir=tmp_path / "downloads")


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def svc(settings, fake_s2, fake_arxiv, sleeps) -> Paperscout:
    store = Store(connect(settings.db_path))
    client = SemanticScholarClient(settings, cache=store, transport=httpx.MockTransport(fake_s2),
                                   sleep=sleeps.append)
    arxiv = ArxivClient(settings, cache=store, transport=httpx.MockTransport(fake_arxiv), sleep=sleeps.append)
    service = Paperscout(settings, store, client, today=TODAY, arxiv=arxiv)
    yield service
    service.close()


@pytest.fixture
def editing_profile(svc) -> ResearchProfile:
    profile = ResearchProfile(
        name="3D Editing",
        field="computer vision; 3D scene editing",
        research_question="How can text-guided editing of 3D Gaussian splatting scenes preserve the background?",
        project_description="Building a text-guided 3D scene editing pipeline on Gaussian splatting.",
        desired_outcomes=["choose a baseline editing method", "understand background preservation"],
        keywords=["gaussian splatting", "scene editing", "text-guided", "diffusion"],
        exclusion_criteria=["character animation"],
        weekly_reading_hours=5,
    )
    return svc.save_profile(profile, activate=True)
