"""Command grammar shared by the CLI and the Claude Code slash commands.

The text after a command is treated as natural-language topic/paper text
unless a token is a recognised ``--option``. Multiword topics need no quotes;
quoted values ("3D Scene Editing") are kept together. Unknown options are
rejected with suggestions instead of being silently ignored.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class UsageError(ValueError):
    pass


@dataclass(frozen=True)
class Opt:
    flag: str
    kind: str  # int | float | str | date | flag | multi | choice
    help: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None

    @property
    def dest(self) -> str:
        return self.flag.lstrip("-").replace("-", "_")


@dataclass(frozen=True)
class CommandSpec:
    name: str
    slash: str
    summary: str
    usage: str
    options: tuple[Opt, ...]
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def option(self, flag: str) -> Opt | None:
        return next((o for o in self.options if o.flag == flag), None)


@dataclass
class Parsed:
    command: str
    positionals: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(self.positionals).strip()

    def get(self, name: str, default: Any = None) -> Any:
        value = self.options.get(name)
        return default if value is None else value


# --------------------------------------------------------------------------- tokenizer

_QUOTES = {'"': '"', "'": "'", "“": "”", "‘": "’", "”": "”"}
_DASHES = ("—", "–", "−")  # em dash, en dash, minus (autocorrected "--")


def _find_close(text: str, start: int, close: str) -> int:
    """Index of a closing quote that ends a token (followed by whitespace/end), or -1."""
    j = start
    while True:
        j = text.find(close, j)
        if j == -1:
            return -1
        if j + 1 == len(text) or text[j + 1].isspace():
            return j
        j += 1


def tokenize_args(raw: str) -> list[str]:
    """Shell-like splitting that tolerates apostrophes (e.g. "Kirchhoff's law")."""
    tokens: list[str] = []
    text = raw.replace("﻿", "").strip()  # Windows PowerShell pipes may prepend a UTF-8 BOM
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        ch = text[i]
        if ch in _QUOTES:
            j = _find_close(text, i + 1, _QUOTES[ch])
            if j != -1:
                tokens.append(text[i + 1 : j])
                i = j + 1
                continue
        j = i
        while j < n and not text[j].isspace():
            # --opt="quoted value"
            if text[j] == "=" and j + 1 < n and text[j + 1] in _QUOTES and text[i:j].startswith(("--",) + _DASHES):
                close = _find_close(text, j + 2, _QUOTES[text[j + 1]])
                if close != -1:
                    tokens.append(text[i:j] + "=" + text[j + 2 : close])
                    j = close + 1
                    break
            j += 1
        else:
            tokens.append(text[i:j])
            i = j
            continue
        i = j
    return tokens


def _normalize_flag(token: str) -> str:
    for dash in _DASHES:
        if token.startswith(dash):
            return "--" + token[len(dash):].lstrip("-")
    return token


# --------------------------------------------------------------------------- parser


def _convert(spec: CommandSpec, opt: Opt, value: str) -> Any:
    try:
        if opt.kind == "int":
            result: Any = int(value)
        elif opt.kind == "float":
            result = float(value)
        elif opt.kind == "date":
            result = date.fromisoformat(value)
        elif opt.kind == "choice":
            if value not in opt.choices:
                raise UsageError(f"{opt.flag} must be one of: {', '.join(opt.choices)} (got {value!r})")
            result = value
        else:
            result = value
    except ValueError as exc:
        if isinstance(exc, UsageError):
            raise
        expected = {"int": "a whole number", "float": "a number", "date": "a date like 2026-01-31"}[opt.kind]
        raise UsageError(f"{opt.flag} expects {expected}, got {value!r}") from None
    if opt.kind in ("int", "float"):
        if opt.minimum is not None and result < opt.minimum:
            raise UsageError(f"{opt.flag} must be at least {opt.minimum:g}")
        if opt.maximum is not None and result > opt.maximum:
            raise UsageError(f"{opt.flag} must be at most {opt.maximum:g}")
    return result


def parse_tokens(spec: CommandSpec, tokens: list[str]) -> Parsed:
    parsed = Parsed(command=spec.name)
    for opt in spec.options:
        parsed.options[opt.dest] = [] if opt.kind == "multi" else (False if opt.kind == "flag" else opt.default)
    given: set[str] = set()
    i = 0
    literal = False
    while i < len(tokens):
        token = tokens[i]
        if literal:
            parsed.positionals.append(token)
            i += 1
            continue
        if token == "--":
            literal = True
            i += 1
            continue
        flagish = _normalize_flag(token)
        if flagish.startswith("--") and len(flagish) > 2:
            name, has_eq, inline = flagish.partition("=")
            opt = spec.option(name)
            if opt is None:
                valid = [o.flag for o in spec.options]
                close = difflib.get_close_matches(name, valid, n=2)
                hint = f" Did you mean {' or '.join(close)}?" if close else ""
                raise UsageError(
                    f"Unknown option {name} for '{spec.slash}'.{hint} Valid options: {', '.join(valid) or 'none'}. "
                    "Put `--` before text that starts with dashes."
                )
            if opt.kind == "flag":
                if has_eq:
                    raise UsageError(f"{opt.flag} does not take a value")
                parsed.options[opt.dest] = True
                i += 1
                continue
            if has_eq:
                value = inline
                i += 1
            else:
                if i + 1 >= len(tokens):
                    raise UsageError(f"{opt.flag} needs a value")
                value = tokens[i + 1]
                i += 2
            if opt.kind == "multi":
                parsed.options[opt.dest].append(value)
            else:
                if opt.dest in given:
                    raise UsageError(f"{opt.flag} was given more than once")
                parsed.options[opt.dest] = _convert(spec, opt, value)
            given.add(opt.dest)
            continue
        parsed.positionals.append(token)
        i += 1
    return parsed


def parse_raw(spec: CommandSpec, raw: str) -> Parsed:
    return parse_tokens(spec, tokenize_args(raw))


# --------------------------------------------------------------------------- command specs

VERBOSE = Opt("--verbose", "flag", "show full abstracts, provenance, and ordering scores")
JSON = Opt("--json", "flag", "print JSON instead of Markdown")
PROFILE = Opt("--profile", "str", "research profile name or ID (default: the active profile)")
FOLDER = Opt("--folder", "str", "local research folder name or ID")
SHORTLIST = Opt("--limit", "int", "shortlist size: the top papers that are judged, shown, saved and downloadable",
                minimum=1, maximum=50, default=20)
INCLUDE_READ = Opt("--include-read", "flag", "include papers you already reviewed")
SEED = Opt("--seed", "multi", "seed paper identifier for recommendations (repeatable)")
SOURCE = Opt("--source", "choice", "where the papers come from", choices=("local", "zotero", "s2-library", "s2-feed"),
             default="local")
DRY_RUN = Opt("--dry-run", "flag", "preview only; change nothing")
PROVIDER = Opt("--provider", "choice", "paper source for this command (default: PAPERSCOUT_PROVIDER, auto)",
               choices=("arxiv", "semantic-scholar", "s2"))
QUICK = Opt("--quick", "flag", "computed criteria only: ignore stored judge ratings (the skills also skip the judges)")

SPECS: dict[str, CommandSpec] = {
    s.name: s
    for s in [
        CommandSpec(
            "recent", "recent", "Find and prioritise recently published papers on a topic.",
            "recent [<topic>] [--days N | --since YYYY-MM-DD] [--until YYYY-MM-DD] [--folder NAME] [--profile NAME] "
            "[--limit N] [--include-read] [--seed ID]...",
            (Opt("--days", "int", "window length in days (default 90, configurable)", minimum=1, maximum=3650),
             Opt("--since", "date", "window start (YYYY-MM-DD)"), Opt("--until", "date", "window end (default today)"),
             FOLDER, PROFILE, SHORTLIST, INCLUDE_READ, SEED, PROVIDER, QUICK, VERBOSE, JSON),
            ("/recent text-guided 3D Gaussian scene editing", "/recent   (no topic: your profile's saved searches)",
             "/recent medical image segmentation --days 90",
             '/recent diffusion models --folder "Diffusion Models" --limit 10'),
            ("'Recent' uses Semantic Scholar's publicationDate (often arXiv v1), never the indexing date.",
             "Papers with only a publication year are kept but flagged."),
        ),
        CommandSpec(
            "discover", "discover", "Discover useful papers (foundational, recent, surveys) without a date limit.",
            "discover [<topic-or-question>] [--folder NAME] [--profile NAME] [--limit N] [--include-read] [--seed ID]...",
            (FOLDER, PROFILE, SHORTLIST, INCLUDE_READ, SEED, PROVIDER, QUICK, VERBOSE, JSON),
            ("/discover background preservation in 3D scene editing", '/discover --folder "3D Reconstruction"'),
            ("Fetches the reference lists of the top results to surface shared prerequisites (Semantic Scholar only).",
             "Without a topic, runs the saved searches in your profile and merges the results."),
        ),
        CommandSpec(
            "rank", "rank", "Rank papers already in your local research folders.",
            'rank "<folder>" [--unread] [--new] [--imported] [--profile NAME] [--source local] | rank --all [...]',
            (Opt("--all", "flag", "rank all local folders"), FOLDER, PROFILE,
             Opt("--unread", "flag", "only papers without a read-type feedback label"),
             Opt("--new", "flag", "only papers added since the folder was last ranked"),
             Opt("--imported", "flag", "only papers imported from files (BibTeX/RIS/ID lists)"),
             SOURCE, Opt("--limit", "int", "maximum papers per list", minimum=1, maximum=500), QUICK, VERBOSE,
             JSON),
            ('/rank "3D Scene Editing"', '/rank "3D Scene Editing" --unread', '/rank "3D Scene Editing" --quick',
             "/rank --all", '/rank --all --profile "Current Project"'),
            ("Only local folders can be ranked. Semantic Scholar website folders and feeds have no official API.",),
        ),
        CommandSpec(
            "folders", "folders", "List, show, propose, or create local research folders.",
            "folders list | show NAME | propose <field> [--profile NAME] | preview --file P.json | "
            "apply --file P.json --confirm TOKEN | create NAME [--description D] [--include C]... [--exclude C]... "
            "[--seed ID]... [--dry-run]",
            (PROFILE, Opt("--description", "str", "folder description"),
             Opt("--include", "multi", "inclusion criterion (repeatable)"),
             Opt("--exclude", "multi", "exclusion criterion (repeatable)"), SEED,
             Opt("--file", "str", "proposal JSON file for preview/apply"),
             Opt("--confirm", "str", "confirmation token printed by preview"),
             Opt("--no-sample", "flag", "propose without querying Semantic Scholar for sample papers"),
             DRY_RUN, JSON),
            ("/folders list", "/folders propose medical computer vision", '/folders propose --profile "3D Editing"',
             '/folders create "Dynamic 3D Generation"'),
            ("Folders live in the local paperscout database, not on the Semantic Scholar website.",),
        ),
        CommandSpec(
            "classify", "classify", "Suggest which local folders a paper belongs in (recommendation only).",
            "classify <DOI | arXiv id/URL | S2 id/URL | title> [--profile NAME] [--suggest-only]",
            (PROFILE, Opt("--suggest-only", "flag", "explicitly request suggestions only (the default)"), PROVIDER, JSON),
            ("/classify 10.1145/3592433", "/classify https://arxiv.org/abs/2308.04079",
             "/classify 3D Gaussian Splatting for Real-Time Radiance Field Rendering"),
        ),
        CommandSpec(
            "add", "add", "Add a paper to a local folder (explicit, previewable).",
            'add <paper> --folder "NAME" [--to local] [--dry-run]',
            (FOLDER, Opt("--to", "choice", "destination", choices=("local", "zotero", "s2-library"), default="local"),
             DRY_RUN, PROVIDER, JSON),
            ('/add https://arxiv.org/abs/2308.04079 --folder "3D Scene Editing"',),
        ),
        CommandSpec(
            "remove", "remove", "Remove a paper from a local folder.",
            'remove <paper> --folder "NAME" [--dry-run]', (FOLDER, DRY_RUN, JSON),
        ),
        CommandSpec(
            "assess", "assess", "Focused relevance and reading-priority assessment of one paper.",
            "assess <paper> [--profile NAME] [--topic TEXT]",
            (PROFILE, Opt("--topic", "str", "judge against this topic instead of the profile"), PROVIDER, VERBOSE, JSON),
            ("/assess https://arxiv.org/abs/2308.04079", '/assess 2308.04079 --profile "Current Project"'),
        ),
        CommandSpec(
            "plan", "reading-plan", "Build a realistic reading plan for a time budget.",
            "plan [--hours H | --week] [--folder NAME] [--profile NAME]",
            (Opt("--hours", "float", "time budget in hours", minimum=0.25, maximum=80),
             Opt("--week", "flag", "use weekly_reading_hours from the profile"), FOLDER, PROFILE, JSON),
            ("/reading-plan --hours 5", '/reading-plan --folder "3D Scene Editing" --hours 3', "/reading-plan --week"),
            ("Named /reading-plan because /plan is a built-in Claude Code command.",),
        ),
        CommandSpec(
            "feedback", "paper-feedback", "Record feedback on a paper (append-only).",
            'feedback <paper> <label> ["optional reason"]',
            (PROFILE, FOLDER, JSON),
            ("/paper-feedback <paper-id> relevant",
             '/paper-feedback <paper-id> irrelevant "Focuses on animation, not editing."',
             "/paper-feedback <paper-id> understood"),
            ("Labels: relevant, irrelevant, already_known, read_later, read_overview, understood, deep_study, "
             "implemented, not_useful.", "Named /paper-feedback because /feedback is a built-in Claude Code command.",
             "A paper given as a title must be quoted."),
        ),
        CommandSpec(
            "note", "note", "Create a short reading-note scaffold (template only).",
            "note <paper> [--pass first|second|deep] [--save]",
            (Opt("--pass", "choice", "reading pass", choices=("first", "second", "deep"), default="first"),
             Opt("--save", "flag", "write the scaffold to the local notes folder"), PROFILE),
            ("/note 2308.04079 --pass first", "/note 2308.04079 --pass deep --save"),
        ),
        CommandSpec(
            "import", "import", "Import papers from a BibTeX, RIS, or identifier-list file into a local folder.",
            'import <file> --folder "NAME" [--mark LABEL] [--dry-run]',
            (FOLDER, Opt("--mark", "str", "record this feedback label on every imported paper, e.g. already_known"),
             DRY_RUN, PROVIDER, JSON),
            ('import library-export.bib --folder "3D Reconstruction"',
             'import documents/reading-history/zotero.bib --folder "Reading history" --mark already_known'),
        ),
        CommandSpec(
            "profile", "profile", "Manage research profiles (the background itself is built by /setup).",
            "profile list | show [NAME] | use NAME | sync [--file profile/profile.json]",
            (Opt("--file", "str", "profile JSON file (default: profile/profile.json)"), JSON),
        ),
        CommandSpec(
            "assessment", "assessment", "Validate and store an assessment written by Claude.",
            "assessment record --file assessment.json [--profile NAME] | assessment list <paper>",
            (Opt("--file", "str", "assessment JSON file"), PROFILE, JSON),
        ),
        CommandSpec(
            "judge", "judge", "Judge subagents' 1-100 ratings: write a packet, record ratings, show the re-ranked run.",
            "judge packet --run RUN [--top N] [--out PATH] | judge record --run RUN --file F [--file F]... | "
            "judge show --run RUN",
            (Opt("--run", "str", "run ID printed by recent, discover, rank or a previous judge show"),
             Opt("--top", "int", "how many of the run's top papers to judge (default: its shortlist, up to 40)",
                 minimum=1, maximum=40),
             Opt("--out", "str", "packet path (default: proposals/judge-<run>/packet.json)"),
             Opt("--file", "multi", "judge output JSON (one per criterion; repeatable)"), VERBOSE, JSON),
            ("judge packet --run run_20261103T101500_ab12cd --top 15",
             "judge record --run run_20261103T101500_ab12cd --file relevance.json --file usefulness.json"),
            ("Used by the /rank, /recent and /discover skills; ratings are validated and stored append-only.",),
        ),
        CommandSpec(
            "authors", "authors", "Rank the recent papers of specific researchers for you (OpenAlex; each paper "
            "matched on its author list by name and affiliation).",
            'authors <name>[ @ affiliation][; <name>...] [--affiliation TEXT] [--days N | --since YYYY-MM-DD] '
            "[--until YYYY-MM-DD] [--topic TEXT] [--profile NAME] [--limit N] [--include-read]",
            (Opt("--affiliation", "str", "institution for every name without its own '@ affiliation', e.g. KAIST"),
             Opt("--days", "int", "window length in days (default 730)", minimum=1, maximum=3650),
             Opt("--since", "date", "window start (YYYY-MM-DD)"), Opt("--until", "date", "window end (default today)"),
             Opt("--topic", "str", "rank against this topic instead of your profile"),
             PROFILE, SHORTLIST, INCLUDE_READ, QUICK, VERBOSE, JSON),
            ("/authors Seungryong Kim --affiliation KAIST", "/authors Seungryong Kim; Jane Doe @ MIT --days 365",
             '/authors Seungryong Kim --affiliation KAIST --topic "3D scene editing"',
             "/authors   (no names: the followed_authors in your profile)"),
            ("Separate names with ';' or ','. Write names as First Last; an OpenAlex author ID (A...) also works.",
             "With an affiliation, a paper counts only if one of its authors has both the name and the institution, "
             "so OpenAlex splitting a person into several records doesn't lose papers.",
             "Researcher names and affiliations are sent to OpenAlex (keyless, shared daily allowance)."),
        ),
        CommandSpec(
            "trends", "trends", "Count where a field's recent papers are heading: rising and fading terms, "
            "methods others name, state-of-the-art claims, citation velocity, volume.",
            "trends [<topic>] [--months N] [--profile NAME] | trends packet --run RUN [--out PATH] | "
            "trends record --run RUN --file F | trends show --run RUN",
            (Opt("--months", "int", "how far back, in months; the window is split into calendar quarters",
                 minimum=6, maximum=36, default=12),
             PROFILE, Opt("--run", "str", "trends run ID (printed in the report's Run line)"),
             Opt("--out", "str", "packet path (default: proposals/trends-<run>/packet.json)"),
             Opt("--file", "str", "the trend analyst's JSON"),
             Opt("--quick", "flag", "counts only: the /trends skill skips the trend-analyst subagent"),
             PROVIDER, JSON),
            ("/trends", "/trends gaussian splatting editing --months 18", "/trends diffusion guidance --quick",
             "trends packet --run run_20261107T101500_ab12cd",
             "trends record --run run_20261107T101500_ab12cd --file analysis.json"),
            ("With no topic, counts your profile's saved searches. One request per search per quarter (arXiv: "
             "one every 3 s), so 5 searches over 12 months take 1.5-4 minutes; cached quarters are reused.",
             "Directions are interpretations of counts from a relevance-ranked sample. paperscout never says which "
             "method is state of the art; it counts the authors' claims."),
        ),
        CommandSpec(
            "download", "download", "Save the arXiv PDFs of a list's top papers, with a reading list, to a local folder.",
            "download [--top N] [--folder NAME | --run RUN] [--out DIR] [--dry-run]",
            (Opt("--top", "int", "how many of the list's top papers", minimum=1, maximum=50, default=10),
             Opt("--folder", "str", "use this local folder's latest /rank list instead of the latest /discover or "
                                    "/recent list"),
             Opt("--run", "str", "use this run's list"),
             Opt("--out", "str", "parent folder (default: PAPERSCOUT_DOWNLOAD_DIR, or Downloads/paperscout)"),
             DRY_RUN, JSON),
            ("/download", "/download --top 5", '/download --folder "Baselines"', "/download --top 20 --dry-run"),
            ("arXiv e-prints only, for your personal use (arXiv's terms don't allow redistributing them); one "
             "request every 3 s through export.arxiv.org. Papers not on arXiv are listed with their links.",
             "Files already saved are skipped, so re-running fetches only what's missing."),
        ),
        CommandSpec("capabilities", "capabilities", "Show the integration capability matrix.", "capabilities [--json]",
                    (JSON,)),
        CommandSpec("feedback-log", "feedback-log", "List recent feedback with paper titles (newest first).",
                    "feedback-log [--limit N]",
                    (Opt("--limit", "int", "entries", minimum=1, maximum=1000, default=100), JSON)),
        CommandSpec("audit", "audit", "Show recent audited write operations.", "audit [--limit N]",
                    (Opt("--limit", "int", "entries", minimum=1, maximum=500, default=20), JSON)),
        CommandSpec("eval", "eval", "Evaluate ranking against human labels and baselines.",
                    "eval template --run RUN_ID --out labels.json | eval run --labels labels.json [--k 5]",
                    (Opt("--run", "str", "run ID to label"), Opt("--out", "str", "output path"),
                     Opt("--labels", "str", "labels JSON"), Opt("--k", "int", "cut-off", minimum=1, maximum=50, default=5),
                     JSON)),
        CommandSpec("doctor", "doctor",
                    "Check setup: data location, stored data, API-key presence (value never shown), .env loading.",
                    "doctor [--live]",
                    (Opt("--live", "flag", "send ONE Semantic Scholar request (no retries) and show sanitized diagnostics"),
                     JSON)),
        CommandSpec("help", "research-help", "Show commands, examples, defaults, and limitations.",
                    "help [command]", ()),
        CommandSpec("init", "init", "Create the local database and show where data is stored.", "init", ()),
    ]
}

SLASH_TO_COMMAND = {spec.slash: spec.name for spec in SPECS.values()}


def spec_for(name: str) -> CommandSpec:
    key = SLASH_TO_COMMAND.get(name.lstrip("/"), name.lstrip("/"))
    if key not in SPECS:
        close = difflib.get_close_matches(key, list(SPECS) + list(SLASH_TO_COMMAND), n=3)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise UsageError(f"Unknown command {name!r}.{hint}")
    return SPECS[key]
