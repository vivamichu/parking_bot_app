"""Tests for the evaluation metric functions (pure, no network)."""
from evaluation.run_eval import (
    load_gold,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_precision_and_recall_perfect():
    retrieved = ["a.md", "b.md", "c.md"]
    relevant = {"a.md"}
    assert precision_at_k(retrieved, relevant, 1) == 1.0
    assert recall_at_k(retrieved, relevant, 1) == 1.0
    assert precision_at_k(retrieved, relevant, 3) == 1 / 3


def test_recall_multiple_relevant():
    retrieved = ["a.md", "x.md", "b.md"]
    relevant = {"a.md", "b.md"}
    assert recall_at_k(retrieved, relevant, 3) == 1.0
    assert recall_at_k(retrieved, relevant, 1) == 0.5


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert reciprocal_rank(["a", "x"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_gold_set_is_wellformed():
    gold = load_gold()
    assert len(gold) >= 10
    assert all("question" in g and "relevant" in g for g in gold)
