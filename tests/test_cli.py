import io
import sys

from paperscout import cli
from paperscout.commands import SPECS, parse_raw

from .conftest import pid


def run(svc, command, *tokens):
    return cli.run(command, list(tokens), svc=svc)


def test_recent_via_slash_raw_arguments(svc, editing_profile):
    parsed = parse_raw(SPECS["recent"], "text-guided gaussian scene editing --days 120 --limit 3")
    code, out = cli.run("recent", parsed, svc=svc)
    assert code == 0
    assert "Date window: 2026-07-04 → 2026-11-01" in out
    assert "not your website Research Feed" in out
    assert "untrusted paper text" in out
    assert "Saved full report" in out


def test_usage_errors_exit_2(svc):
    code, out = run(svc, "recent")
    assert code == 2 and "Give a topic" in out
    code, out = run(svc, "rank", "Some Folder", "--all")
    assert code == 2 and "not both" in out
    code, out = run(svc, "recent", "topic", "--bogus")
    assert code == 2 and "Unknown option --bogus" in out
    code, out = run(svc, "plan", "five", "hours")
    assert code == 2


def test_unsupported_source_exit_3(svc):
    code, out = run(svc, "rank", "--all", "--source", "s2-feed")
    assert code == 3 and "Not supported" in out and "Alternative" in out


def test_folder_lifecycle_and_add_boundaries(svc):
    code, out = run(svc, "folders", "create", "Dynamic 3D Generation", "--dry-run")
    assert code == 0 and "Dry run" in out and svc.store.list_folders() == []
    code, out = run(svc, "folders", "create", "Dynamic 3D Generation", "--description", "4D generation")
    assert code == 0 and "does not create a folder on the Semantic Scholar website" in out
    code, out = run(svc, "add", pid(1))
    assert code == 2 and "--folder" in out
    code, out = run(svc, "add", pid(1), "--folder", "Dynamic 3D Generation", "--dry-run")
    assert "Dry run" in out
    code, out = run(svc, "add", pid(1), "--folder", "Dynamic 3D Generation")
    assert code == 0 and out.startswith("Added")
    code, out = run(svc, "folders", "list")
    assert "1 papers" in out
    code, out = run(svc, "add", pid(1), "--folder", "Dynamic 3D Generation", "--to", "zotero")
    assert code == 3


def test_feedback_and_note_commands(svc, editing_profile):
    code, out = run(svc, "feedback", pid(1), "irrelevant", "Focuses on animation, not editing.")
    assert code == 0 and "irrelevant" in out and "animation" in out
    code, out = run(svc, "feedback", pid(1))
    assert code == 2
    code, out = run(svc, "note", pid(1), "--pass", "deep")
    assert code == 0 and "## Reproduction / implementation notes" in out


def test_add_failures_exit_nonzero(svc):
    run(svc, "folders", "create", "Editing")
    code, out = run(svc, "add", "https://example.com/not-a-paper", "--folder", "Editing")
    assert code == 1 and "Not added" in out and "Unsupported paper URL host" in out
    code, out = run(svc, "add", "Text-Guided Editing of 3D Gaussian Splatting Scenes", "--folder", "Editing")
    assert code == 1 and pid(10) in out  # ambiguous title lists options
    code, out = run(svc, "add", "doi:10.9999/does-not-exist", "--folder", "Editing")
    assert code == 1 and "no record" in out


def test_classify_ambiguous_title_lists_options(svc):
    code, out = run(svc, "classify", "Text-Guided", "Editing", "of", "3D", "Gaussian", "Splatting", "Scenes")
    assert code == 0 and "could not resolve" in out and pid(10) in out


def test_help_and_capabilities_need_no_service():
    code, out = cli.run("help", [])
    assert code == 0 and "/reading-plan" in out and "/paper-feedback" in out
    code, out = cli.run("help", ["recent"])
    assert "--days" in out and "/recent text-guided 3D Gaussian scene editing" in out
    code, out = cli.run("capabilities", [])
    assert "Research Feeds" in out and "unsupported" in out


def test_main_slash_entrypoint_reads_stdin(monkeypatch, svc, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("--bogus option"))
    code = cli.main(["slash", "recent"])
    assert code == 2 and "Unknown option --bogus" in capsys.readouterr().out
    code = cli.main(["slash", "research-help", "--args", "rank"])
    assert code == 0 and "--unread" in capsys.readouterr().out


def test_slash_rejects_split_arguments(capsys):
    # What Windows PowerShell 5.1 produces for --args '"3D Scene Editing" --unread'
    code = cli.main(["slash", "rank", "--args", "3D", "Scene", "Editing --unread"])
    assert code == 2 and "exactly ONE string" in capsys.readouterr().out


def test_ranking_report_opens_with_summary_and_table(svc, editing_profile):
    code, out = run(svc, "discover", "text-guided gaussian scene editing", "--limit", "4")
    assert code == 0
    summary, table = out.index("papers shortlisted"), out.index("| # | Score | Priority | Paper |")
    about, details = out.index("## About this list"), out.index("## Details")
    assert summary < table < about < details < out.index("### 1. ")
    rows = [line for line in out.splitlines() if line.startswith("| ") and not line.startswith("| #")]
    assert len(rows) == 4 and rows[0].startswith("| 1 | ")
    assert "new" in rows[0] and "untrusted paper text" in out


def test_single_paper_report_has_no_table(svc, editing_profile):
    code, out = run(svc, "assess", pid(1))
    assert code == 0 and "| # | Score |" not in out and "## Details" not in out
