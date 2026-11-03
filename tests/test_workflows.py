import json

import pytest

from paperscout import evaluation, planning, taxonomy
from paperscout.importers import entries_from_bibtex, entries_from_lines, entries_from_ris
from paperscout.models import ReadingRole
from paperscout.service import ServiceError
from paperscout.store.db import connect, migrate

from .conftest import pid


def folder_with(svc, name, *papers):
    svc.create_folder(name, f"{name} papers", [name.lower()])
    for n in papers:
        svc.add_to_folder(pid(n), name)


# ----------------------------------------------------------------------------- reading plan


def test_plan_respects_budget_and_orders_prerequisites_first(svc, editing_profile):
    folder_with(svc, "Gaussian Scene Editing", 1, 2, 3, 4, 7)
    plan = planning.build_plan(svc, hours=3)
    assert plan.budget_minutes == 180
    assert plan.reading_minutes + plan.notes_buffer_minutes + plan.questions_buffer_minutes <= plan.budget_minutes
    assert plan.notes_buffer_minutes > 0 and plan.questions_buffer_minutes > 0
    roles = [e.role for e in plan.entries]
    if ReadingRole.foundational in roles and ReadingRole.recent_method in roles:
        assert roles.index(ReadingRole.foundational) < roles.index(ReadingRole.recent_method)
    text = planning.render_plan(plan)
    assert "planning heuristics" in text


def test_plan_uses_profile_weekly_hours_and_validates(svc, editing_profile):
    folder_with(svc, "Gaussian Scene Editing", 1, 2)
    plan = planning.build_plan(svc, week=True)
    assert plan.hours == 5 and any("weekly reading time" in n for n in plan.notices)
    with pytest.raises(ServiceError, match="either --hours or --week"):
        planning.build_plan(svc, hours=2, week=True)


def test_plan_without_budget_or_papers(svc):
    with pytest.raises(ServiceError, match="No time budget"):
        planning.build_plan(svc)
    with pytest.raises(ServiceError, match="Nothing to plan"):
        planning.build_plan(svc, hours=2)


def test_plan_skips_read_papers_and_downgrades_to_fit(svc, editing_profile):
    folder_with(svc, "Gaussian Scene Editing", 1, 2, 3)
    svc.record_feedback(pid(3), "understood")
    plan = planning.build_plan(svc, hours=0.75)
    assert pid(3) not in {e.paper.paper_id for e in plan.entries}
    assert all(e.budgeted_minutes <= 45 for e in plan.entries)


# ----------------------------------------------------------------------------- notes


def test_note_template_has_prompts_not_content(svc, editing_profile):
    text, paper = planning.note_template(svc, pid(1), pass_="second")
    for heading in ("Purpose of reading", "Problem", "Main contribution", "Method", "Evidence", "Limitations",
                    "Connection to my project", "Questions", "Next action"):
        assert f"## {heading}" in text
    assert "diffusion model guidance" not in text  # abstract text is never pasted in as "understanding"
    path = planning.save_note(svc, text, paper, "second")
    assert path.exists()
    with pytest.raises(ServiceError, match="not overwriting"):
        planning.save_note(svc, text, paper, "second")


def test_note_pass_validation(svc):
    with pytest.raises(ServiceError):
        planning.note_template(svc, pid(1), pass_="fourth")


# ----------------------------------------------------------------------------- folder proposals


PROPOSAL = {
    "profile": None,
    "rationale": "test",
    "folders": [
        {"name": "Text-Guided Editing", "description": "Editing 3D scenes from text prompts",
         "inclusion_criteria": ["text-guided 3D scene editing"], "exclusion_criteria": ["2D-only image editing"]},
        {"name": "Scene Representations", "description": "Gaussian splatting and radiance fields",
         "inclusion_criteria": ["gaussian splatting", "neural radiance fields"]},
    ],
}


def test_proposal_preview_then_confirmed_apply(svc):
    preview = taxonomy.preview_proposal(svc, PROPOSAL)
    assert preview["dry_run"] and "NOT Semantic Scholar" in preview["destination"]
    assert svc.store.list_folders() == []
    with pytest.raises(ServiceError, match="does not match"):
        taxonomy.apply_proposal(svc, PROPOSAL, "wrongtoken")
    changed = json.loads(json.dumps(PROPOSAL))
    changed["folders"][0]["name"] = "Changed"
    with pytest.raises(ServiceError, match="does not match"):
        taxonomy.apply_proposal(svc, changed, preview["confirmation_token"])
    result = taxonomy.apply_proposal(svc, PROPOSAL, preview["confirmation_token"])
    assert [c["name"] for c in result["created"]] == ["Text-Guided Editing", "Scene Representations"]
    again = taxonomy.apply_proposal(svc, PROPOSAL, preview["confirmation_token"])
    assert again["created"] == [] and len(again["skipped_existing"]) == 2


def test_proposal_overlap_and_size_limits(svc):
    overlapping = json.loads(json.dumps(PROPOSAL))
    overlapping["folders"][1] = {"name": "Text Guided 3D Editing", "description": "Editing 3D scenes from text prompts",
                                 "inclusion_criteria": ["text-guided 3D scene editing"]}
    preview = taxonomy.preview_proposal(svc, overlapping)
    assert preview["overlaps"] and preview["overlaps"][0]["kind"] == "proposed/proposed"
    too_many = {"folders": [{"name": f"F{i}", "description": "a folder description",
                             "inclusion_criteria": ["x"]} for i in range(9)]}
    with pytest.raises(ServiceError, match="at most 7"):
        taxonomy.preview_proposal(svc, too_many)


def test_proposal_context_is_grounded_and_local(svc, editing_profile):
    ctx = taxonomy.proposal_context(svc, "gaussian splatting")
    assert ctx["sample_papers"] and "NOT Semantic Scholar" in ctx["destination"]
    assert ctx["constraints"]["max_folders"] == 7


# ----------------------------------------------------------------------------- import


BIB = r"""
@inproceedings{kerbl2023,
  title = {{3D} Gaussian Splatting for Real-Time Radiance Field Rendering},
  author = {Kerbl, Bernhard and others},
  year = 2023,
  doi = {10.1234/SYNTHETIC.3DGS},
}
@article{editing,
  title = "Text guided editing",
  eprint = {2609.00001}, archivePrefix = {arXiv},
  year = {2026}
}
@misc{nothing, note = {no identifiers}}
"""

RIS = """TY  - JOUR
TI  - Some Title Only Paper
PY  - 2024
ER  -
TY  - JOUR
DO  - 10.1234/synthetic.3dgs
ER  -
"""


def test_parsers():
    bib = entries_from_bibtex(BIB)
    assert [e.ref.kind if e.ref else None for e in bib] == ["doi", "arxiv", None]
    assert bib[0].title == "3D Gaussian Splatting for Real-Time Radiance Field Rendering"
    ris = entries_from_ris(RIS)
    assert ris[0].ref.is_title and ris[1].ref.kind == "doi"
    lines = entries_from_lines("# comment\n2609.00001\nhttps://example.com/x\nA Paper Title\n")
    assert [e.ref.kind if e.ref else "error" for e in lines] == ["arxiv", "error", "title"]


def test_import_dry_run_then_apply(svc, tmp_path):
    svc.create_folder("Imported", "imported papers")
    path = tmp_path / "lib.bib"
    path.write_text(BIB, encoding="utf-8")
    preview = svc.import_file(path, "Imported", dry_run=True)
    assert len(preview["resolved"]) == 2 and preview["added"] == [] and preview["problems"]
    result = svc.import_file(path, "Imported")
    assert len(result["added"]) == 2
    folder = svc.store.get_folder("Imported")
    assert all(m.source == "import:bibtex" for m in svc.store.memberships(folder.id))
    assert [i.paper.paper_id for i in svc.rank_folder("Imported", imported=True).items]


# ----------------------------------------------------------------------------- evaluation (synthetic labels)


def test_evaluation_metrics_and_template(svc, editing_profile, tmp_path):
    report = svc.recent("gaussian scene editing", days=200)
    path = evaluation.write_template(svc, report.run_id, tmp_path / "labels.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert all(item["label"] is None for item in data["items"])
    # SYNTHETIC labels for testing the metric code only (not a human-labeled evaluation set).
    for item in data["items"]:
        item["label"] = 2 if "Text-Guided" in item["title"] else (1 if "Gaussian" in item["title"] else 0)
    path.write_text(json.dumps(data), encoding="utf-8")
    result = evaluation.evaluate(svc, path, k=3)
    assert set(result["results"]) == {"heuristic (paperscout)", "search order", "recency only", "BM25 lexical"}
    assert "noisy" in result["caveat"]


def test_metric_functions():
    labels = {"a": 2, "b": 0, "c": 1}
    assert evaluation.precision_at_k(["a", "b", "c"], labels, 2) == 0.5
    assert evaluation.ndcg_at_k(["a", "c", "b"], labels, 3) == pytest.approx(1.0)
    assert evaluation.ndcg_at_k(["b", "c", "a"], labels, 3) < 1.0


# ----------------------------------------------------------------------------- migrations


def test_migrations_are_idempotent(tmp_path):
    conn = connect(tmp_path / "m.sqlite3")
    assert migrate(conn) == []
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == [1, 2]
