"""Offline evaluation against human relevance labels.

Workflow: run /recent or /discover, export a labeling template for that run
(items shuffled so the system order does not anchor the labeler), label each
paper 0/1/2, then compare the heuristic ranking with three baselines:
Semantic Scholar search order, recency-only, and a plain BM25 relevance
baseline (no profile, seeds, or recency). Small label sets give noisy numbers;
the report says so.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

from paperscout.models import Paper, Provenance
from paperscout.ranking.text import tokenize
from paperscout.reports import sanitize_untrusted
from paperscout.service import Paperscout, ServiceError

LABEL_HELP = "2 = must read for this goal, 1 = useful, 0 = not relevant. Leave null to skip."


def write_template(svc: Paperscout, run_id: str, path: Path) -> Path:
    run = svc.store.get_run(run_id)
    rows = svc.store.run_candidates(run_id)
    papers = svc.store.get_papers(r["paper_id"] for r in rows)
    items = [
        {
            "paper_id": r["paper_id"],
            "title": papers[r["paper_id"]].title,
            "year": papers[r["paper_id"]].year,
            "abstract": sanitize_untrusted(papers[r["paper_id"]].abstract, 700),
            "link": papers[r["paper_id"]].link,
            "label": None,
            "notes": "",
        }
        for r in rows
        if r["paper_id"] in papers
    ]
    random.Random(run_id).shuffle(items)
    payload = {
        "run_id": run_id,
        "query": run["query"],
        "kind": run["kind"],
        "labeling_instructions": LABEL_HELP + " Judge against your research goal, not citation counts.",
        "labeled_by": "",
        "items": items,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(order: list[str], labels: dict[str, int], k: int) -> float:
    gains = [labels[pid] for pid in order[:k]]
    ideal = sorted(labels.values(), reverse=True)[:k]
    return _dcg(gains) / _dcg(ideal) if _dcg(ideal) > 0 else 0.0


def precision_at_k(order: list[str], labels: dict[str, int], k: int) -> float:
    top = order[:k]
    return sum(1 for pid in top if labels[pid] >= 1) / len(top) if top else 0.0


def redundancy_at_k(order: list[str], papers: dict[str, Paper], k: int, threshold: float = 0.5) -> int:
    """Number of near-duplicate pairs (title token Jaccard >= threshold) in the top k."""
    titles = [set(tokenize(papers[pid].title)) for pid in order[:k]]
    pairs = 0
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            union = titles[i] | titles[j]
            if union and len(titles[i] & titles[j]) / len(union) >= threshold:
                pairs += 1
    return pairs


def bm25_order(query: str, papers: dict[str, Paper], k1: float = 1.2, b: float = 0.75) -> list[str]:
    docs = {pid: tokenize(f"{p.title} {p.abstract or ''}") for pid, p in papers.items()}
    avg = sum(len(d) for d in docs.values()) / max(1, len(docs))
    df: Counter[str] = Counter()
    for tokens in docs.values():
        df.update(set(tokens))
    n = len(docs)
    q = set(tokenize(query or ""))

    def score(tokens: list[str]) -> float:
        counts = Counter(tokens)
        total = 0.0
        for term in q:
            if term not in counts:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            tf = counts[term]
            total += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / max(avg, 1)))
        return total

    return sorted(docs, key=lambda pid: -score(docs[pid]))


def evaluate(svc: Paperscout, labels_path: Path, k: int = 5) -> dict[str, Any]:
    data = json.loads(labels_path.read_text(encoding="utf-8"))
    labels = {i["paper_id"]: int(i["label"]) for i in data["items"] if i.get("label") is not None}
    if len(labels) < 3:
        raise ServiceError("Label at least 3 papers before evaluating (more is better; 20+ recommended).")
    if any(v not in (0, 1, 2) for v in labels.values()):
        raise ServiceError("Labels must be 0, 1, or 2.")
    rows = [r for r in svc.store.run_candidates(data["run_id"]) if r["paper_id"] in labels]
    papers = svc.store.get_papers(labels)
    system = [r["paper_id"] for r in rows]

    def search_rank(row) -> int:
        provs = [Provenance.model_validate(p) for p in json.loads(row["provenance"])]
        ranks = [p.rank for p in provs if p.source == "s2.search" and p.rank]
        return min(ranks) if ranks else 10_000

    search = [r["paper_id"] for r in sorted(rows, key=search_rank)]
    recency = sorted(system, key=lambda pid: papers[pid].publication_date or f"{papers[pid].year or 0}-00-00",
                     reverse=True)
    lexical = [pid for pid in bm25_order(data.get("query") or "", {pid: papers[pid] for pid in system})]
    orders = {"heuristic (paperscout)": system, "search order": search, "recency only": recency, "BM25 lexical": lexical}
    results = {
        name: {
            f"precision@{k}": round(precision_at_k(order, labels, k), 3),
            f"nDCG@{k}": round(ndcg_at_k(order, labels, k), 3),
            f"near-duplicate pairs@{k}": redundancy_at_k(order, papers, k),
        }
        for name, order in orders.items()
    }
    return {
        "run_id": data["run_id"],
        "labeled": len(labels),
        "relevant": sum(1 for v in labels.values() if v >= 1),
        "k": k,
        "results": results,
        "caveat": "Small, single-labeler samples are noisy; treat differences as hypotheses, not conclusions.",
    }


def render(result: dict[str, Any]) -> str:
    k = result["k"]
    lines = [
        f"# Ranking evaluation for run `{result['run_id']}`",
        f"Labeled papers: {result['labeled']} (relevant: {result['relevant']}), cut-off k = {k}",
        "",
        f"| Ordering | precision@{k} | nDCG@{k} | near-duplicate pairs@{k} |",
        "|---|---|---|---|",
    ]
    for name, metrics in result["results"].items():
        lines.append(f"| {name} | {metrics[f'precision@{k}']} | {metrics[f'nDCG@{k}']} | "
                     f"{metrics[f'near-duplicate pairs@{k}']} |")
    lines += ["", f"_{result['caveat']}_"]
    return "\n".join(lines)
