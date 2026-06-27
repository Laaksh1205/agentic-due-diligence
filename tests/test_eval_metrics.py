"""Unit tests for the evaluation metrics — Task 5.1.

Deterministic, no network/LLM/model load: they exercise the lexical matcher and
the pure metric calculations directly.
"""

from evaluation import metrics


def _sig(text, severity="HIGH", url="https://news.example.com/a", **kw):
    return {"text": text, "severity": severity, "source_url": url, **kw}


def _truth(tid, text, severity="HIGH", url="https://news.example.com/a"):
    return {"id": tid, "text": text, "severity": severity, "source_url": url}


# ── matching ──────────────────────────────────────────────────────────────────

def test_match_score_rewards_topical_overlap():
    # Short reworded signal vs long paragraph — blended score should be high.
    pred = "The FAA proposed $3.1M in fines against Boeing for safety violations."
    truth = ("FAA $3.1M fine (2024) for hundreds of quality system violations at the 737 "
             "Renton factory. Total fines approximately $845M across 36 safety violations.")
    assert metrics.match_score(pred, truth) >= 60


def test_match_score_low_for_unrelated():
    assert metrics.match_score("Quarterly revenue grew 20%", "Data breach exposed user records") < 45


def test_same_source_host_counts_as_match():
    preds = [_sig("totally different wording here", url="https://faa.gov/x")]
    truths = [_truth(1, "An FAA enforcement action", url="https://faa.gov/y")]
    r = metrics.recall_vs_truth(preds, truths)
    assert r["recall"] == 1.0  # matched via shared host


# ── recall / precision ────────────────────────────────────────────────────────

def test_recall_counts_distinct_truths():
    truths = [_truth(1, "machinists strike halted production", url="https://a.com/1"),
              _truth(2, "credit rating cut to junk by Moody's", url="https://b.com/2"),
              _truth(3, "data breach exposed customer records", url="https://c.com/3")]
    preds = [_sig("Boeing machinists strike stopped 737 production", url="https://x.com/1"),
             _sig("Moody's downgraded the credit rating toward junk", url="https://y.com/2")]
    r = metrics.recall_vs_truth(preds, truths)
    assert r["matched_truth"] == 2 and r["total_truth"] == 3
    assert abs(r["recall"] - 2 / 3) < 1e-9
    assert 3 in r["missed_truth_ids"]


def test_precision_proxy_bounds():
    truths = [_truth(1, "machinists strike halted production", url="https://a.com/1")]
    preds = [_sig("machinists strike stopped production", url="https://x.com/1"),
             _sig("unrelated cyber breach incident", url="https://y.com/2")]
    p = metrics.precision_proxy(preds, truths)
    assert p["total_pred"] == 2 and p["matched_pred"] == 1
    assert abs(p["precision_proxy"] - 0.5) < 1e-9


def test_precision_from_labels():
    assert metrics.precision_from_labels({"a": True, "b": True, "c": False})["precision"] == 2 / 3
    assert metrics.precision_from_labels({})["precision"] is None


# ── severity / sufficiency ────────────────────────────────────────────────────

def test_severity_accuracy_exact_and_within_one():
    truths = [_truth(1, "machinists strike halted production", severity="HIGH")]
    preds = [_sig("machinists strike stopped production", severity="MEDIUM")]
    s = metrics.severity_accuracy(preds, truths)
    assert s["severity_pairs"] == 1
    assert s["severity_exact"] == 0.0
    assert s["severity_within_one"] == 1.0  # HIGH vs MEDIUM is one band apart


def test_data_sufficiency_within_one():
    r = metrics.data_sufficiency_accuracy("ADEQUATE", "RICH")
    assert r["exact"] is False and r["within_one"] is True
    assert metrics.data_sufficiency_accuracy("SPARSE", "RICH")["within_one"] is False
    assert metrics.data_sufficiency_accuracy(None, "RICH")["exact"] is None


# ── verification / dedup / guardrails ─────────────────────────────────────────

def test_verification_stats():
    v = metrics.verification_stats({"signals_extracted": 20, "signals_rejected": 3})
    assert v["rejection_rate"] == 0.15
    assert v["verification_pass_rate"] == 0.85
    assert metrics.verification_stats({})["rejection_rate"] == 0.0


def test_dedup_stats():
    preds = [_sig("a", is_corroborated=True), _sig("b"), _sig("c", is_contradictory=True)]
    d = metrics.dedup_stats(preds)
    assert d["total_signals"] == 3 and d["corroborated"] == 1 and d["contradictory"] == 1


def test_guardrail_compliance():
    ok = metrics.guardrail_compliance({"llm_call_count": 40, "estimated_cost_usd": 0.2},
                                      max_llm_calls=50, max_cost_usd=1.0)
    assert ok["compliant"] is True
    bad = metrics.guardrail_compliance({"llm_call_count": 60, "estimated_cost_usd": 0.2},
                                       max_llm_calls=50, max_cost_usd=1.0)
    assert bad["compliant"] is False


# ── faithfulness / consistency / latency ──────────────────────────────────────

def test_citation_grounding_detects_orphans():
    report = {
        "risk_signals": [{"id": 1}, {"id": 2}],
        "positive_signals": [],
        "executive_summary": "A risk [Signal-1] and another [Signal-2] but also [Signal-9].",
        "detailed_sections": {},
    }
    cg = metrics.citation_grounding(report)
    assert cg["citations"] == 3 and cg["orphans"] == 1
    assert abs(cg["grounded"] - 2 / 3) < 1e-9


def test_jaccard_and_consistency():
    assert metrics.jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert metrics.jaccard({"a"}, {"b"}) == 0.0
    c = metrics.consistency([{"a", "b", "c"}, {"a", "b"}, {"a", "b", "c"}])
    assert c["runs"] == 3 and c["mean_jaccard"] is not None
    assert metrics.consistency([{"a"}])["mean_jaccard"] is None  # single run


def test_latency_percentiles():
    lp = metrics.latency_percentiles([10, 20, 30, 40, 100])
    assert lp["n"] == 5 and lp["p50"] == 30
    assert metrics.latency_percentiles([])["p50"] is None
    assert metrics.latency_percentiles([0, 0])["n"] == 0  # zeros ignored


def test_signal_fingerprints_use_truth_ids():
    truths = [_truth(1, "machinists strike halted production", url="https://a.com/1")]
    preds = [_sig("machinists strike stopped production", url="https://x.com/1"),
             _sig("an unrelated cyber breach", url="https://y.com/2")]
    fps = metrics.signal_fingerprints(preds, truths)
    assert "t1" in fps  # matched signal keyed by truth id
    assert any(k.startswith("n:") for k in fps)  # unmatched keyed by text shingle
