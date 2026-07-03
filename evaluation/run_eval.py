"""Evaluate the RAG retriever.

Metrics:
- Precision@K, Recall@K, MRR for K in {1, 3, 5}
- Retrieval latency (p50 / p95 / mean)

Requires the vector store to be built (run `python -m parking_bot.ingest`) and
OPENAI_API_KEY to be set (query embedding).

Usage:
    python -m evaluation.run_eval
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from parking_bot.retrieval import search_documents

HERE = Path(__file__).resolve().parent
KS = [1, 3, 5]


def load_gold() -> list[dict]:
    return json.loads((HERE / "gold_set.json").read_text(encoding="utf-8"))


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    hits = sum(1 for s in top if s in relevant)
    return hits / len(top)


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = set(retrieved[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    for i, s in enumerate(retrieved, start=1):
        if s in relevant:
            return 1.0 / i
    return 0.0


def evaluate(k_max: int = 5) -> dict:
    gold = load_gold()
    per_k = {k: {"precision": [], "recall": []} for k in KS}
    rr, latencies = [], []

    for item in gold:
        relevant = set(item["relevant"])
        t0 = time.perf_counter()
        docs = search_documents(item["question"], k=k_max)
        latencies.append((time.perf_counter() - t0) * 1000)  # ms
        retrieved = [d.metadata.get("source", "") for d in docs]

        for k in KS:
            per_k[k]["precision"].append(precision_at_k(retrieved, relevant, k))
            per_k[k]["recall"].append(recall_at_k(retrieved, relevant, k))
        rr.append(reciprocal_rank(retrieved, relevant))

    def avg(xs):
        return round(statistics.mean(xs), 4) if xs else 0.0

    def pct(xs, p):
        xs = sorted(xs)
        if not xs:
            return 0.0
        idx = min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1))))
        return round(xs[idx], 1)

    return {
        "n_questions": len(gold),
        "precision_at_k": {k: avg(per_k[k]["precision"]) for k in KS},
        "recall_at_k": {k: avg(per_k[k]["recall"]) for k in KS},
        "mrr": avg(rr),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1),
            "p50": pct(latencies, 50),
            "p95": pct(latencies, 95),
        },
    }


def render_report(results: dict) -> str:
    lines = [
        "# RAG Evaluation Report",
        "",
        f"Questions evaluated: **{results['n_questions']}**",
        "",
        "## Retrieval accuracy",
        "",
        "| K | Precision@K | Recall@K |",
        "|---|-------------|----------|",
    ]
    for k in KS:
        lines.append(
            f"| {k} | {results['precision_at_k'][k]:.3f} | "
            f"{results['recall_at_k'][k]:.3f} |"
        )
    lines += [
        "",
        f"**MRR:** {results['mrr']:.3f}",
        "",
        "## Latency (retrieval, ms)",
        "",
        "| mean | p50 | p95 |",
        "|------|-----|-----|",
        f"| {results['latency_ms']['mean']} | {results['latency_ms']['p50']} | "
        f"{results['latency_ms']['p95']} |",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    results = evaluate()
    report = render_report(results)
    out = HERE / "report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved report to {out}")


if __name__ == "__main__":
    main()
