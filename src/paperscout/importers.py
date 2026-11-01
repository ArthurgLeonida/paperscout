"""Parse exported paper lists into references for resolution.

Supported formats: BibTeX (.bib), RIS (.ris), and plain text (one DOI, arXiv
id, Semantic Scholar id/URL, paper URL, or title per line; ``#`` comments).
Exports from the Semantic Scholar website or from Zotero work through these.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from paperscout.identifiers import IdentifierError, PaperRef, parse_paper_ref


@dataclass
class ImportEntry:
    ref: PaperRef | None
    title: str | None
    year: int | None
    raw: str
    error: str | None = None


def _clean_bib_value(value: str) -> str:
    value = re.sub(r"[{}]", "", value)
    value = value.replace("\\&", "&").replace("~", " ")
    return " ".join(value.split())


def _read_bib_value(text: str, i: int) -> tuple[str, int]:
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text):
        return "", i
    if text[i] == "{":
        depth, start = 0, i
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : i], i + 1
            i += 1
        return text[start + 1 :], i
    if text[i] == '"':
        start = i + 1
        i += 1
        while i < len(text) and not (text[i] == '"' and text[i - 1] != "\\"):
            i += 1
        return text[start:i], i + 1
    start = i
    while i < len(text) and text[i] not in ",}\n":
        i += 1
    return text[start:i].strip(), i


def parse_bibtex(text: str) -> list[dict[str, str]]:
    entries = []
    for match in re.finditer(r"@(\w+)\s*[{(]", text):
        kind = match.group(1).lower()
        if kind in ("comment", "string", "preamble"):
            continue
        i = match.end()
        comma = text.find(",", i)
        if comma == -1:
            continue
        fields: dict[str, str] = {"_type": kind, "_key": text[i:comma].strip()}
        i = comma + 1
        while i < len(text):
            field_match = re.compile(r"\s*([A-Za-z][\w\-]*)\s*=").match(text, i)
            if not field_match:
                break
            value, i = _read_bib_value(text, field_match.end())
            fields[field_match.group(1).lower()] = _clean_bib_value(value)
            while i < len(text) and text[i] in " \t\r\n,":
                i += 1
            if i < len(text) and text[i] in "})":
                break
        entries.append(fields)
    return entries


def parse_ris(text: str) -> list[dict[str, str]]:
    entries, current = [], {}
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", line)
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "ER":
            if current:
                entries.append(current)
            current = {}
            continue
        if tag in current and tag in ("UR", "L1", "L2"):
            continue
        current.setdefault(tag, value)
    if current:
        entries.append(current)
    return entries


def _entry_from_fields(raw: str, doi: str | None, arxiv: str | None, url: str | None, title: str | None,
                       year: str | None) -> ImportEntry:
    year_int = int(year[:4]) if year and year[:4].isdigit() else None
    for candidate in (f"doi:{doi}" if doi else None, f"arxiv:{arxiv}" if arxiv else None, url):
        if not candidate:
            continue
        try:
            return ImportEntry(parse_paper_ref(candidate), title, year_int, raw)
        except IdentifierError:
            continue
    if title:
        return ImportEntry(PaperRef("title", title, title), title, year_int, raw)
    return ImportEntry(None, None, year_int, raw, error="no identifier or title found")


def entries_from_bibtex(text: str) -> list[ImportEntry]:
    out = []
    for f in parse_bibtex(text):
        arxiv = None
        if f.get("eprint") and f.get("archiveprefix", "arxiv").lower() == "arxiv":
            arxiv = f["eprint"]
        elif "arxiv" in f.get("journal", "").lower():
            m = re.search(r"(\d{4}\.\d{4,5})", f["journal"])
            arxiv = m.group(1) if m else None
        out.append(_entry_from_fields(f.get("_key", ""), f.get("doi"), arxiv, f.get("url"), f.get("title"),
                                      f.get("year")))
    return out


def entries_from_ris(text: str) -> list[ImportEntry]:
    out = []
    for f in parse_ris(text):
        title = f.get("TI") or f.get("T1")
        out.append(_entry_from_fields(title or "", f.get("DO"), None, f.get("UR"), title, f.get("PY") or f.get("Y1")))
    return out


def entries_from_lines(text: str) -> list[ImportEntry]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            ref = parse_paper_ref(line)
            out.append(ImportEntry(ref, ref.value if ref.is_title else None, None, line))
        except IdentifierError as exc:
            out.append(ImportEntry(None, None, None, line, error=str(exc)))
    return out


def detect_format(path: Path, text: str) -> str:
    suffix = path.suffix.lower()
    if suffix in (".bib", ".bibtex"):
        return "bibtex"
    if suffix == ".ris":
        return "ris"
    if re.search(r"^\s*@\w+\s*[{(]", text, re.MULTILINE):
        return "bibtex"
    if re.search(r"^TY  - ", text, re.MULTILINE):
        return "ris"
    return "lines"


def read_entries(path: Path) -> tuple[str, list[ImportEntry]]:
    text = path.read_text(encoding="utf-8-sig")
    fmt = detect_format(path, text)
    parser = {"bibtex": entries_from_bibtex, "ris": entries_from_ris, "lines": entries_from_lines}[fmt]
    return fmt, parser(text)
