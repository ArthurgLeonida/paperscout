"""Merge candidates retrieved from several sources into one record per paper.

Keys, in order: Semantic Scholar paperId, DOI, arXiv id, CorpusId, then
normalised title + year. Provenance from every duplicate is preserved, and the
richest metadata snapshot (abstract > TLDR > metadata) is kept.
"""

from __future__ import annotations

from paperscout.identifiers import normalize_title
from paperscout.models import Candidate, Paper


def _keys(paper: Paper) -> list[str]:
    keys = [f"s2:{paper.paper_id}"]
    if paper.doi:
        keys.append(f"doi:{paper.doi}")
    if paper.arxiv_id:
        keys.append(f"arxiv:{paper.arxiv_id.lower()}")
    if paper.corpus_id is not None:
        keys.append(f"corpus:{paper.corpus_id}")
    title = normalize_title(paper.title)
    if len(title) >= 20:  # very short titles collide too easily
        keys.append(f"title:{title}|{paper.year or '?'}")
    return keys


def _richer(a: Paper, b: Paper) -> Paper:
    if b.evidence_level.rank > a.evidence_level.rank:
        return b
    if b.evidence_level.rank == a.evidence_level.rank and b.publication_date and not a.publication_date:
        return b
    return a


def deduplicate(candidates: list[Candidate]) -> tuple[list[Candidate], int]:
    """Return (unique candidates in first-seen order, number of duplicates merged)."""
    index: dict[str, int] = {}
    merged: list[Candidate] = []
    duplicates = 0
    for cand in candidates:
        keys = _keys(cand.paper)
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            merged.append(cand.model_copy(deep=True))
            position = len(merged) - 1
        else:
            duplicates += 1
            existing = merged[hit]
            existing.paper = _richer(existing.paper, cand.paper)
            existing.provenance.extend(cand.provenance)
            position = hit
        for key in _keys(merged[position].paper) + keys:
            index.setdefault(key, position)
    return merged, duplicates
