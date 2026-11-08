"""Command-line interface.

    paperscout <command> [args...]          # e.g. paperscout recent text-guided 3D editing --days 60
    paperscout slash <slash-name> < args    # raw slash-command arguments on stdin (used by the skills)
    paperscout slash <slash-name> --args "<raw arguments>"

Both forms use the same grammar (paperscout.commands) and the same service
layer as every other interface.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Callable

from paperscout.capabilities import CAPABILITIES, UnsupportedCapability, check_rank_source, require
from paperscout.commands import SPECS, Parsed, UsageError, parse_raw, parse_tokens, spec_for
from paperscout.config import load_settings, normalize_provider, profile_dir
from paperscout.reports import RankingReport, render_ranking, sanitize_untrusted, save_report, to_json
from paperscout.http import ApiError
from paperscout.service import Paperscout, ServiceError

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_UNSUPPORTED = 0, 1, 2, 3


class Output:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, text: str = "") -> None:
        self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts).rstrip() + "\n"


# --------------------------------------------------------------------------- helpers


def _emit_report(svc: Paperscout, report: RankingReport, p: Parsed, out: Output) -> None:
    markdown = render_ranking(report, verbose=bool(p.get("verbose")))
    stem = f"{report.generated_at:%Y%m%d-%H%M%S}-{report.kind}-{(report.query or report.heading)[:40]}"
    md_path, json_path = save_report(report, render_ranking(report, verbose=True), svc.settings.reports_dir, stem)
    if p.get("json"):
        out.write(to_json(report))
    else:
        out.write(markdown)
        out.write(f"_Saved full report: {md_path} and {json_path}_")


def _require_text(p: Parsed, what: str) -> str:
    if not p.text:
        raise UsageError(f"Missing {what}. Usage: {SPECS[p.command].usage}")
    return p.text


# --------------------------------------------------------------------------- handlers


def _require_topic_or_profile(svc: Paperscout, p: Parsed, command: str) -> None:
    """No topic is fine when a profile exists: its saved searches (or research question) are used."""
    if not p.text and not p.get("folder") and svc.resolve_profile(p.get("profile"))[0] is None:
        raise UsageError(f"Give a topic or --folder, or build a profile with /setup. Usage: {SPECS[command].usage}")


def h_recent(svc: Paperscout, p: Parsed, out: Output) -> None:
    _require_topic_or_profile(svc, p, "recent")
    report = svc.recent(p.text or None, days=p.get("days"), since=p.get("since"), until=p.get("until"),
                        folder=p.get("folder"), profile=p.get("profile"), limit=p.get("limit"),
                        include_read=p.get("include_read"), seeds=p.get("seed"), quick=p.get("quick"))
    _emit_report(svc, report, p, out)


def h_discover(svc: Paperscout, p: Parsed, out: Output) -> None:
    _require_topic_or_profile(svc, p, "discover")
    report = svc.discover(p.text or None, folder=p.get("folder"), profile=p.get("profile"), limit=p.get("limit"),
                          include_read=p.get("include_read"), seeds=p.get("seed"), quick=p.get("quick"))
    _emit_report(svc, report, p, out)


def h_rank(svc: Paperscout, p: Parsed, out: Output) -> None:
    folder = p.get("folder") or p.text or None
    if p.get("all"):
        if folder:
            raise UsageError("Use either a folder name or --all, not both.")
        report = svc.rank_all(profile=p.get("profile"), unread=p.get("unread"), new=p.get("new"),
                              imported=p.get("imported"), source=p.get("source"), limit=p.get("limit"),
                              quick=p.get("quick"))
    else:
        if not folder:
            raise UsageError('Name a folder (e.g. rank "3D Scene Editing") or use --all.')
        report = svc.rank_folder(folder, profile=p.get("profile"), unread=p.get("unread"), new=p.get("new"),
                                 imported=p.get("imported"), source=p.get("source"), limit=p.get("limit"),
                                 quick=p.get("quick"))
    _emit_report(svc, report, p, out)


def h_folders(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout import taxonomy

    action = p.positionals[0].lower() if p.positionals else "list"
    rest = " ".join(p.positionals[1:]).strip()
    if action == "list":
        rows = svc.folder_overview()
        if p.get("json"):
            out.write(to_json(rows))
            return
        out.write("# Local research folders")
        out.write(f"_Stored in {svc.settings.db_path}. These are paperscout folders, not Semantic Scholar website folders._")
        out.write("")
        if not rows:
            out.write('No folders yet. Try `/folders propose <field>` or `/folders create "Name"`.')
        for r in rows:
            ranked = r["last_ranked_at"].date().isoformat() if r["last_ranked_at"] else "never"
            out.write(f"- **{r['name']}** (`{r['id']}`): {r['papers']} papers, {r['unread']} unread, "
                      f"{r['imported']} imported; profile: {r['profile'] or 'none'}; last ranked: {ranked}")
            if r["description"]:
                out.write(f"  - {r['description']}")
        return
    if action == "show":
        folder = svc.get_folder(rest or _require_text(p, "folder name"))
        members = svc.store.memberships(folder.id)
        papers = svc.store.get_papers(m.paper_id for m in members)
        if p.get("json"):
            out.write(to_json({"folder": folder.model_dump(mode="json"),
                               "papers": [papers[m.paper_id].model_dump(mode="json", include={"paper_id", "title", "year"})
                                          for m in members if m.paper_id in papers]}))
            return
        out.write(f"# Local folder: {folder.name} (`{folder.id}`)")
        out.write(folder.description or "(no description)")
        out.write(f"- Include: {'; '.join(folder.inclusion_criteria) or 'not set'}")
        out.write(f"- Exclude: {'; '.join(folder.exclusion_criteria) or 'not set'}")
        out.write(f"- Seeds: {', '.join(folder.seed_papers) or 'none'}")
        out.write("")
        for m in members:
            paper = papers.get(m.paper_id)
            if paper:
                out.write(f"- {sanitize_untrusted(paper.title)} ({paper.year or 'year unknown'}) `{paper.paper_id}` — "
                          f"added {m.added_at.date()} via {m.source}")
        return
    if action == "create":
        name = rest or _require_text(p, "folder name")
        folder, created = svc.create_folder(name, p.get("description", ""), p.get("include"), p.get("exclude"),
                                            p.get("seed"), profile_name=p.get("profile"), dry_run=p.get("dry_run"))
        where = f"local paperscout database ({svc.settings.db_path})"
        if created:
            out.write(f'Created local folder "{folder.name}" (`{folder.id}`) in the {where}.')
        else:
            out.write(f'Dry run: would create local folder "{folder.name}" in the {where}. Nothing changed.')
        out.write("This does not create a folder on the Semantic Scholar website (no official API exists for that).")
        return
    if action == "propose":
        ctx = taxonomy.proposal_context(svc, rest or None, p.get("profile"), sample=not p.get("no_sample"))
        out.write(to_json(ctx))
        return
    if action in ("preview", "apply"):
        path = p.get("file")
        if not path:
            raise UsageError(f"folders {action} needs --file proposal.json")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if action == "preview":
            out.write(to_json(taxonomy.preview_proposal(svc, data)))
        else:
            token = p.get("confirm")
            if not token:
                raise UsageError("folders apply needs --confirm TOKEN from the preview (explicit confirmation).")
            out.write(to_json(taxonomy.apply_proposal(svc, data, token)))
        return
    raise UsageError(f"Unknown folders action {action!r}. Usage: {SPECS['folders'].usage}")


def h_classify(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout.taxonomy import classify, render_classification

    result = classify(svc, _require_text(p, "paper identifier, URL, or title"), profile=p.get("profile"))
    out.write(to_json(result) if p.get("json") else render_classification(result))


def h_add(svc: Paperscout, p: Parsed, out: Output) -> None:
    folder = p.get("folder")
    if not folder:
        raise UsageError('add needs --folder "NAME" (folders are never inferred for writes).')
    result = svc.add_to_folder(_require_text(p, "paper"), folder, destination=p.get("to"), dry_run=p.get("dry_run"))
    status = result["status"]
    if status in ("ambiguous", "not_found"):
        options = "".join(f"\n- {o['title']} ({o.get('year') or 'year unknown'}) id `{o['paper_id']}`"
                          for o in result.get("options", []))
        raise ServiceError(f"Not added: {result['message']}{options}")
    if p.get("json"):
        out.write(to_json(result))
        return
    paper = result["paper"]
    if status == "preview":
        extra = " (already present; applying would change nothing)" if result["already_present"] else ""
        out.write(f"Dry run: would add \"{paper['title']}\" to {result['destination']}{extra}. Nothing changed.")
    else:
        out.write(f"{result['message'].capitalize()}: \"{paper['title']}\" → {result['destination']}.")


def h_remove(svc: Paperscout, p: Parsed, out: Output) -> None:
    if not p.get("folder"):
        raise UsageError('remove needs --folder "NAME"')
    out.write(to_json(svc.remove_from_folder(_require_text(p, "paper"), p.get("folder"), dry_run=p.get("dry_run"))))


def h_assess(svc: Paperscout, p: Parsed, out: Output) -> None:
    report = svc.assess(_require_text(p, "paper"), profile=p.get("profile"), topic=p.get("topic"))
    _emit_report(svc, report, p, out)


def h_plan(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout.planning import build_plan, render_plan

    if p.text:
        raise UsageError(f"Unexpected text {p.text!r}. Usage: {SPECS['plan'].usage}")
    plan = build_plan(svc, hours=p.get("hours"), week=p.get("week"), folder=p.get("folder"), profile=p.get("profile"))
    out.write(to_json(plan) if p.get("json") else render_plan(plan))


def h_feedback(svc: Paperscout, p: Parsed, out: Output) -> None:
    if len(p.positionals) < 2:
        raise UsageError(f"Usage: {SPECS['feedback'].usage}")
    paper_text, label = p.positionals[0], p.positionals[1]
    reason = " ".join(p.positionals[2:]).strip() or None
    event, paper, history = svc.record_feedback(paper_text, label, reason, profile=p.get("profile"),
                                                folder=p.get("folder"))
    if p.get("json"):
        out.write(to_json({"recorded": event.model_dump(mode="json"), "history": [h.model_dump(mode="json") for h in history]}))
        return
    out.write(f"Recorded feedback **{event.label.value}** for \"{sanitize_untrusted(paper.title)}\" (`{paper.paper_id}`)"
              + (f": {reason}" if reason else "") + ".")
    if len(history) > 1:
        out.write("Earlier feedback is kept (append-only):")
        for h in history[:-1]:
            out.write(f"- {h.created_at.date()} {h.label.value}" + (f": {h.reason}" if h.reason else ""))
    out.write("It will be used to filter and rank future results.")


def h_note(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout.planning import note_template, save_note

    text, paper = note_template(svc, _require_text(p, "paper"), pass_=p.get("pass"), profile=p.get("profile"))
    out.write(text)
    if p.get("save"):
        path = save_note(svc, text, paper, p.get("pass"))
        out.write(f"_Saved scaffold to {path} (local file only)._")


def h_import(svc: Paperscout, p: Parsed, out: Output) -> None:
    if not p.get("folder"):
        raise UsageError('import needs --folder "NAME"')
    result = svc.import_file(Path(_require_text(p, "file path")), p.get("folder"), dry_run=p.get("dry_run"),
                             mark=p.get("mark"))
    out.write(to_json(result))


def h_profile(svc: Paperscout, p: Parsed, out: Output) -> None:
    action = p.positionals[0].lower() if p.positionals else "list"
    rest = " ".join(p.positionals[1:]).strip()
    if action == "list":
        active = svc.store.active_profile()
        profiles = svc.store.list_profiles()
        if not profiles:
            out.write("No profiles yet. Run /setup in Claude Code to build your research background, "
                      "then `paperscout profile sync`.")
        for prof in profiles:
            mark = " (active)" if active and prof.id == active.id else ""
            out.write(f"- **{prof.name}**{mark} `{prof.id}`: {prof.research_question or prof.field}")
        return
    if action == "show":
        prof = svc.resolve_profile(rest or None)[0]
        if prof is None:
            raise ServiceError("No active profile.")
        out.write(to_json(prof))
        return
    if action == "use":
        prof = svc.store.set_active_profile(rest or _require_text(p, "profile name"))
        out.write(f"Active profile: {prof.name}")
        return
    if action == "sync":
        path = Path(p.get("file")) if p.get("file") else profile_dir() / "profile.json"
        prof = svc.sync_profile(path)
        knowledge = f", {len(prof.knowledge)} knowledge-map topics" if prof.knowledge else ""
        out.write(f"Synced {path} into profile {prof.name} (`{prof.id}`){knowledge}; it is now the active profile.")
        return
    raise UsageError(f"Unknown profile action {action!r}. Usage: {SPECS['profile'].usage}")


def h_assessment(svc: Paperscout, p: Parsed, out: Output) -> None:
    action = p.positionals[0].lower() if p.positionals else ""
    if action == "record":
        path = p.get("file")
        if not path:
            raise UsageError("assessment record needs --file assessment.json")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        stored = [svc.record_claude_assessment(item, profile=p.get("profile")) for item in items]
        out.write(f"Stored {len(stored)} validated assessment(s) (append-only).")
        return
    if action == "list":
        paper = svc.require_paper(" ".join(p.positionals[1:]))
        out.write(to_json(svc.store.assessments_for(paper.paper_id)))
        return
    raise UsageError(f"Usage: {SPECS['assessment'].usage}")


def h_judge(svc: Paperscout, p: Parsed, out: Output) -> None:
    action = p.positionals[0].lower() if p.positionals else ""
    run_id = p.get("run")
    if action not in ("packet", "record", "show") or len(p.positionals) > 1:
        raise UsageError(f"Usage: {SPECS['judge'].usage}")
    if not run_id:
        raise UsageError(f"judge {action} needs --run RUN_ID (printed in the report's Run line)")
    if action == "packet":
        packet = svc.judge_packet(run_id, top=p.get("top"))
        path = Path(p.get("out") or Path("proposals") / f"judge-{run_id}" / "packet.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
        out.write(f"Wrote a judge packet with {len(packet['papers'])} paper(s) to {path} (local file; paper text is "
                  "marked untrusted).")
        out.write(f"Next: run the relevance-judge, usefulness-judge and foundation-judge subagents on it, save each "
                  f"result next to it, then `paperscout judge record --run {run_id} --file <result.json>` (repeat "
                  "--file for each criterion).")
        return
    if action == "record":
        files = p.get("file")
        if not files:
            raise UsageError("judge record needs --file judge-output.json (repeat --file for each criterion)")
        batches = []
        for name in files:
            data = json.loads(Path(name).read_text(encoding="utf-8-sig"))
            batches += data if isinstance(data, list) else [data]
        stored = svc.record_judgments(run_id, batches)
        out.write("Stored judge ratings (append-only): " + ", ".join(f"{k} {v}" for k, v in stored.items()) + ".")
        out.write("")
    _emit_report(svc, svc.rescore_run(run_id), p, out)


TREND_ACTIONS = ("packet", "record", "show")


def h_trends(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout import trends

    first = p.positionals[0].lower() if p.positionals else ""
    run_id = p.get("run")
    if first in TREND_ACTIONS and (run_id or len(p.positionals) == 1):
        if len(p.positionals) > 1:
            raise UsageError(f"Usage: {SPECS['trends'].usage}")
        if not run_id:
            raise UsageError(f"trends {first} needs --run RUN_ID (printed in the report's Run line)")
        if first == "packet":
            packet = trends.trend_packet(svc, run_id)
            path = Path(p.get("out") or Path("proposals") / f"trends-{run_id}" / "packet.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
            out.write(f"Wrote a trend packet with {len(packet['signals'])} signal(s) and {len(packet['papers'])} "
                      f"paper(s) to {path} (local file; paper text is marked untrusted).")
            out.write(f"Next: run the trend-analyst subagent on it, save its JSON next to it, then "
                      f"`paperscout trends record --run {run_id} --file <analysis.json>`.")
            return
        if first == "record":
            if not p.get("file"):
                raise UsageError("trends record needs --file analysis.json")
            data = json.loads(Path(p.get("file")).read_text(encoding="utf-8-sig"))
            report = trends.record_analysis(svc, run_id, data)
            out.write(f"Stored the trend analysis (append-only) as run {report.run_id}.")
            out.write("")
        else:
            report = trends.load_report(svc, run_id)
    else:
        if run_id or p.get("file") or p.get("out"):
            raise UsageError("--run, --file and --out go with `trends packet`, `trends record` or `trends show`. "
                             f"Usage: {SPECS['trends'].usage}")
        if not p.text and svc.resolve_profile(p.get("profile"))[0] is None:
            raise UsageError(f"Give a topic, or build a profile with /setup. Usage: {SPECS['trends'].usage}")
        report = trends.analyse(svc, p.text or None, months=p.get("months"), profile_name=p.get("profile"),
                                progress=lambda line: print(line, file=sys.stderr, flush=True))
    markdown = trends.render_trends(report)
    stem = f"{report.generated_at:%Y%m%d-%H%M%S}-trends-{report.query[:40]}"
    md_path, json_path = save_report(report, markdown, svc.settings.reports_dir, stem)
    if p.get("json"):
        out.write(to_json(report))
    else:
        out.write(markdown)
        out.write(f"_Saved full report: {md_path} and {json_path}_")


def h_download(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout.downloads import download_list, render_download

    if p.text:
        raise UsageError(f"Unexpected text {p.text!r}. Usage: {SPECS['download'].usage}")
    result = download_list(svc, top=p.get("top"), run_id=p.get("run"), folder=p.get("folder"),
                           out=Path(p.get("out")) if p.get("out") else None, dry_run=p.get("dry_run"),
                           progress=lambda line: print(line, file=sys.stderr, flush=True))
    if not result.dry_run and result.count("saved") == 0 and result.count("failed"):
        raise ServiceError(render_download(result))
    out.write(to_json(result) if p.get("json") else render_download(result))


def h_capabilities(svc: Paperscout | None, p: Parsed, out: Output) -> None:
    if p.get("json"):
        out.write(to_json({k: vars(c) for k, c in CAPABILITIES.items()}))
        return
    out.write("# Capability matrix (verified 2026-11-01; see docs/capability-matrix.md for sources)")
    out.write("")
    out.write("| Capability | Provider | Access | paperscout | Fallback |")
    out.write("|---|---|---|---|---|")
    for c in CAPABILITIES.values():
        out.write(f"| {c.description} | {c.provider_status} | {c.access} | {c.implemented} | {c.fallback} |")


def h_feedback_log(svc: Paperscout, p: Parsed, out: Output) -> None:
    entries = svc.store.feedback_log(p.get("limit"))
    if p.get("json"):
        out.write(to_json(entries))
        return
    if not entries:
        out.write("No feedback recorded yet.")
    for e in entries:
        reason = f": {e['reason']}" if e["reason"] else ""
        out.write(f"- {e['at'][:10]} **{e['label']}** {sanitize_untrusted(e['title'] or e['paper_id'])}{reason}")


def h_audit(svc: Paperscout, p: Parsed, out: Output) -> None:
    out.write(to_json(svc.store.audit_entries(p.get("limit"))))


def h_eval(svc: Paperscout, p: Parsed, out: Output) -> None:
    from paperscout import evaluation

    action = p.positionals[0].lower() if p.positionals else ""
    if action == "template":
        if not p.get("run") or not p.get("out"):
            raise UsageError("eval template needs --run RUN_ID --out labels.json")
        path = evaluation.write_template(svc, p.get("run"), Path(p.get("out")))
        out.write(f"Wrote a labeling template to {path}. Fill in each 'label' (2 = must read, 1 = useful, 0 = not relevant).")
        return
    if action == "run":
        if not p.get("labels"):
            raise UsageError("eval run needs --labels labels.json")
        result = evaluation.evaluate(svc, Path(p.get("labels")), k=p.get("k"))
        out.write(to_json(result) if p.get("json") else evaluation.render(result))
        return
    raise UsageError(f"Usage: {SPECS['eval'].usage}")


def h_help(svc: Paperscout | None, p: Parsed, out: Output) -> None:
    if p.text:
        spec = spec_for(p.text)
        out.write(f"## /{spec.slash}  (CLI: paperscout {spec.name})")
        out.write(spec.summary)
        out.write(f"Usage: {spec.usage}")
        for opt in spec.options:
            default = f" (default: {opt.default})" if opt.default not in (None, False) else ""
            choices = f" [{'|'.join(opt.choices)}]" if opt.choices else ""
            out.write(f"  {opt.flag}{choices}: {opt.help}{default}")
        if spec.examples:
            out.write("Examples:")
            for ex in spec.examples:
                out.write(f"  {ex}")
        for note in spec.notes:
            out.write(f"Note: {note}")
        return
    out.write("# paperscout commands")
    out.write("")
    slash_order = ["recent", "discover", "trends", "rank", "download", "folders", "classify", "add", "assess", "plan",
                   "feedback", "note", "help"]
    for name in slash_order:
        spec = SPECS[name]
        out.write(f"- `/{spec.slash}`: {spec.summary}")
    out.write("- `/setup`: build or update your private research background (profile/); a Claude Code skill only")
    out.write("")
    out.write("CLI-only: `paperscout import`, `profile`, `assessment`, `judge`, `capabilities`, `audit`, `feedback-log`, "
              "`eval`, `init`, `doctor`.")
    out.write("")
    out.write("Limitations: Semantic Scholar website folders and Research Feeds have no official API, so paperscout "
              "uses local research folders and topic- and seed-based discovery through the public API instead. "
              "/plan and /feedback are built-in Claude Code commands, so use /reading-plan and /paper-feedback. "
              "Run `/research-help <command>` for details.")


def h_init(svc: Paperscout, p: Parsed, out: Output) -> None:
    out.write(f"Database ready: {svc.settings.db_path}")
    out.write(f"Settings: {json.dumps(svc.settings.redacted())}")


def h_doctor(svc: Paperscout | None, p: Parsed, out: Output) -> None:
    from paperscout import doctor

    settings = svc.settings if svc is not None else load_settings()
    report = doctor.run_doctor(settings, live=p.get("live"))
    out.write(to_json(report) if p.get("json") else doctor.render(report))


def _precheck(name: str, p: Parsed) -> None:
    """Refuse unsupported external operations before touching the local database."""
    if name == "rank":
        check_rank_source(p.get("source"))
    elif name == "add" and p.get("to") == "zotero":
        require("zotero.web_api.write")
    elif name == "add" and p.get("to") == "s2-library":
        require("s2.website_library.write")


HANDLERS: dict[str, Callable[..., None]] = {
    "recent": h_recent, "discover": h_discover, "rank": h_rank, "folders": h_folders, "classify": h_classify,
    "add": h_add, "remove": h_remove, "assess": h_assess, "plan": h_plan, "feedback": h_feedback, "note": h_note,
    "import": h_import, "profile": h_profile, "assessment": h_assessment, "judge": h_judge, "trends": h_trends,
    "download": h_download,
    "capabilities": h_capabilities,
    "audit": h_audit, "feedback-log": h_feedback_log, "eval": h_eval, "help": h_help, "init": h_init, "doctor": h_doctor,
}
NO_SERVICE = {"help", "capabilities", "doctor"}


def run(command: str, parsed_or_tokens: Parsed | list[str], svc: Paperscout | None = None) -> tuple[int, str]:
    """Run one command; returns (exit code, output text). Used by the CLI, tests, and MCP."""
    out = Output()
    own_service = False
    try:
        spec = spec_for(command)
        parsed = parsed_or_tokens if isinstance(parsed_or_tokens, Parsed) else parse_tokens(spec, parsed_or_tokens)
        _precheck(spec.name, parsed)
        if svc is None and spec.name not in NO_SERVICE:
            svc = Paperscout.open()
            own_service = True
        if svc is not None and parsed.options.get("provider"):
            svc.provider = normalize_provider(parsed.options["provider"])
        HANDLERS[spec.name](svc, parsed, out)
        return EXIT_OK, out.text()
    except UsageError as exc:
        return EXIT_USAGE, f"Usage error: {exc}\n"
    except UnsupportedCapability as exc:
        return EXIT_UNSUPPORTED, f"Not supported: {exc}\n"
    except ServiceError as exc:
        return EXIT_ERROR, f"Error: {exc}\n"
    except ApiError as exc:
        return EXIT_ERROR, f"Error: {exc}\n"
    except (OSError, json.JSONDecodeError) as exc:
        return EXIT_ERROR, f"Error: {exc}\n"
    finally:
        if own_service and svc is not None:
            svc.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not argv or argv[0] in ("-h", "--help"):
        code, text = run("help", [])
        sys.stdout.write(text)
        return code
    if argv[0] == "-v" or argv[0] == "--verbose-log":
        logging.getLogger("paperscout").setLevel(logging.INFO)
        argv = argv[1:]
    if argv[0] == "slash":
        if len(argv) < 2:
            sys.stderr.write("Usage: paperscout slash <command> [--args \"raw text\"]  (or raw text on stdin)\n")
            return EXIT_USAGE
        name = argv[1]
        extra = argv[2:]
        if extra and (extra[0] != "--args" or len(extra) != 2):
            # Windows PowerShell 5.1 splits a quoted string containing double quotes into several
            # arguments; never silently drop the rest.
            sys.stdout.write(
                "Usage error: pass the raw slash arguments as exactly ONE string after --args, or on stdin. "
                f"Received {len(extra)} separate argument(s). In Windows PowerShell, pipe them instead: "
                "'\"3D Scene Editing\" --unread' | uv run -q paperscout slash rank\n"
            )
            return EXIT_USAGE
        raw = extra[1] if extra else ("" if sys.stdin.isatty() else sys.stdin.read())
        try:
            parsed = parse_raw(spec_for(name), raw)
        except UsageError as exc:
            sys.stdout.write(f"Usage error: {exc}\n")
            return EXIT_USAGE
        code, text = run(name, parsed)
    else:
        code, text = run(argv[0], argv[1:])
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
