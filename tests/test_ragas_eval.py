"""Deterministic unit tests for the RAGAS-style LLM-judge metrics (Task 5.1.5).

These stub the LLM provider and embedder so they run with no network/model load,
exercising the metric algorithms (statement decomposition + verdict scoring,
question-generation + cosine relevancy, reference-weighted context precision).
"""

import asyncio
import re

import numpy as np
import pytest

from evaluation import ragas_eval


class _StubProvider:
    """Returns schema-shaped canned responses; verdicts default to all-supported."""

    def __init__(self, verdict_value=1, raise_503_times=0):
        self.verdict_value = verdict_value
        self.raise_503_times = raise_503_times
        self.calls = 0

    async def complete(self, prompt, schema, *, system="", use_fast=True):
        self.calls += 1
        if self.raise_503_times > 0:
            self.raise_503_times -= 1
            raise RuntimeError("503 UNAVAILABLE high demand")
        name = schema.__name__
        if name == "Statements":
            return schema(statements=["claim one", "claim two", "claim three"])
        if name == "Questions":
            return schema(questions=["q one", "q two", "q three"])
        if name == "Verdicts":
            n = len(re.findall(r"^\d+\.", prompt, flags=re.M))
            return schema(verdicts=[self.verdict_value] * n)
        raise ValueError(name)


def _embedder(texts):
    # Deterministic pseudo-embeddings (callable form, like get_default_embedder()).
    return np.array([[hash(t) % 11 + 1, len(t) % 7 + 1, 2.0] for t in texts], dtype=float)


def _report():
    return {
        "executive_summary": "Boeing faces DOJ fraud charges and a door-plug blowout.",
        "detailed_sections": {"legal": "The DOJ criminal case is ongoing."},
        "risk_signals": [
            {"text": "DOJ fraud", "source_snippet": "The DOJ charged Boeing with fraud over the 737 MAX."},
            {"text": "door plug", "source_snippet": "An Alaska Airlines door plug blew out mid-flight."},
        ],
    }


_TRUTH = {"signals": [{"text": "DOJ criminal fraud"}, {"text": "door plug blowout"}]}


def test_all_metrics_populate():
    res = asyncio.run(ragas_eval.evaluate_ragas_async(
        _report(), _TRUTH, "What are Boeing's risks?", _embedder, _StubProvider()))
    assert res["faithfulness"] == 1.0          # all statements supported
    assert res["context_precision"] == 1.0     # all contexts relevant
    assert res["answer_relevancy"] is not None  # cosine computed
    assert res["errors"] == []
    assert "LLM judge" in res["source"]


def test_faithfulness_counts_unsupported():
    res = asyncio.run(ragas_eval.evaluate_ragas_async(
        _report(), _TRUTH, "Q?", _embedder, _StubProvider(verdict_value=0)))
    assert res["faithfulness"] == 0.0
    assert res["context_precision"] == 0.0


def test_empty_report_returns_none_not_crash():
    res = asyncio.run(ragas_eval.evaluate_ragas_async(
        {"risk_signals": []}, _TRUTH, "Q?", _embedder, _StubProvider()))
    assert res["faithfulness"] is None
    assert res["context_precision"] is None


def test_retry_rides_out_transient_503():
    # First two calls 503, then succeed — _complete_retry should recover.
    res = asyncio.run(ragas_eval.evaluate_ragas_async(
        _report(), _TRUTH, "Q?", _embedder, _StubProvider(raise_503_times=2)))
    # At least one metric should have succeeded after retries.
    assert any(res[k] is not None for k in ("faithfulness", "answer_relevancy", "context_precision"))


def test_context_precision_weighted_average():
    # Direct check of the precision@k weighting with a known relevance pattern.
    async def run():
        class P:
            async def complete(self, prompt, schema, *, system="", use_fast=True):
                # relevant, irrelevant, relevant → AP = (1/1 + 2/3)/2 = 0.833...
                return schema(verdicts=[1, 0, 1])
        return await ragas_eval._context_precision(
            P(), ["c1", "c2", "c3"], "reference text")
    val = asyncio.run(run())
    assert abs(val - (1.0 + 2 / 3) / 2) < 1e-3


@pytest.mark.parametrize("a,b,expected", [
    ([1, 0, 0], [1, 0, 0], 1.0),
    ([1, 0], [0, 1], 0.0),
])
def test_cosine(a, b, expected):
    assert abs(ragas_eval._cosine(a, b) - expected) < 1e-9
