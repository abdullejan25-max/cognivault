"""Independent wrong rankings: never use search outputs to author expected IDs."""
import importlib.util
from pathlib import Path
import sys
import copy
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def metrics():
    assert (SCRIPTS / "retrieval_metrics.py").is_file(), "P15 evaluator is not implemented"
    import retrieval_metrics
    return retrieval_metrics


def gold():
    return {"case_id": "independent", "domain": "documents", "path": "documents",
            "should_have_result": True, "relevant_ids": ["a65", "a66"],
            "relevance_grades": {"a65": 3, "a66": 2, "b65": 0},
            "expected_locators": {"a65": {"source_id": "a", "page_number": 65, "chapter_id": None},
                                  "a66": {"source_id": "a", "page_number": 66, "chapter_id": None}},
            "expected_support": {"a65": ["Equal parts"], "a66": ["Use sixths"]},
            "filters": {"source_ids": ["a"], "page_range": [65, 66]}}


def hit(id, source="a", page=65, evidence="Equal parts"):
    return {"id": id, "locator": {"source_id": source, "page_number": page,
                                  "chapter_id": None}, "evidence": evidence}


def obs(ranking=(), **kw):
    return {"status": "ok", "ranking": list(ranking), "cold_ms": 9,
            "warm_ms": [1, 2, 3, 4, 5], **kw}


def test_duplicate_wrong_book_and_omission_do_not_earn_full_scores():
    r = metrics().evaluate_case(gold(), obs([hit("b65", "b"), hit("a65"), hit("a65")]))
    assert r["recall@1"] == 0
    assert r["recall@3"] == 0.5
    assert r["mrr@10"] == 0.5
    assert r["source_case"] == 0 and r["page_case"] == 0
    assert r["citation_case"] == 0 and r["filter_case"] == 0
    assert r["items"]["source"] == {"numerator": 1, "denominator": 2, "value": 0.5}


def test_each_relevant_id_uses_its_independent_page_and_support():
    r = metrics().evaluate_case(gold(), obs([hit("a65"), hit("a66", page=66, evidence="Use sixths")]))
    assert r["source_case"] == r["page_case"] == r["citation_case"] == 1
    assert r["filter_case"] == 1
    assert r["locator_coverage"]["chapter_id"]["na"] == 2
    bad = metrics().evaluate_case(gold(), obs([hit("a65"), hit("a66", page=65, evidence="Use sixths")]))
    assert bad["page_case"] == 0


@pytest.mark.parametrize("observation", [obs([]), obs([], status="error", error="BACKEND_TIMEOUT")])
def test_supported_miss_or_error_remains_positive_denominator(observation):
    r = metrics().evaluate_case(gold(), observation)
    s = metrics().summarize([r])
    assert s["groups"]["documents/documents"]["recall@10"]["denominator"] == 1
    assert r["recall@10"] == r["mrr@10"] == 0
    assert r["source_case"] == r["page_case"] == r["citation_case"] == 0


def test_false_empty_and_never_empty_and_error_have_honest_negative_metrics():
    negative = copy.deepcopy(gold())
    negative.update(case_id="negative", should_have_result=False, relevant_ids=[],
                    relevance_grades={}, expected_locators={}, expected_support={}, filters={})
    m = metrics()
    s = m.summarize([m.evaluate_case(gold(), obs([])), m.evaluate_case(negative, obs([])),
                     m.evaluate_case(negative, obs([], status="error", error="BACKEND_TIMEOUT"))])
    group = s["groups"]["documents/documents"]
    assert group["no_result_precision"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert group["negative_empty_recall"]["value"] == 0.5
    never = m.summarize([m.evaluate_case(negative, obs([hit("b65", "b")]))])["groups"]["documents/documents"]
    assert never["no_result_precision"]["value"] is None
    assert never["negative_empty_recall"]["value"] == 0
    assert never["false_positive_return_rate"]["value"] == 1


def test_unsupported_and_contract_have_explicit_coverage():
    m = metrics()
    unsupported = m.evaluate_case(gold(), obs([hit("a65")], status="unsupported", unsupported=["page_range"]))
    contract = dict(gold(), measurement="contract", expected_error="PAYLOAD_TOO_LARGE")
    probe = m.evaluate_case(contract, obs([], status="error", error="PAYLOAD_TOO_LARGE"))
    s = m.summarize([unsupported, probe])
    assert s["coverage"] == {"all": 2, "quality_supported": 0, "unsupported": 1,
                              "errors": 1, "contract": 1, "contract_pass": 1}
    assert unsupported["filter_case"] is None and not unsupported["quality_eligible"]
    assert probe["capability_gap"] == "capacity-2049"


def test_gold_validation_rejects_misaligned_keys_and_grades():
    m = metrics()
    for field in ("expected_locators", "expected_support", "relevance_grades"):
        wrong = copy.deepcopy(gold())
        wrong[field].pop("a66")
        with pytest.raises(ValueError):
            m.evaluate_case(wrong, obs([]))


def test_unknown_extra_hit_and_wrong_excerpt_fail_case_citation():
    m = metrics()
    r = m.evaluate_case(gold(), obs([hit("a65", evidence="unsupported claim"),
                                    hit("a66", page=66, evidence="Use sixths"), hit("unknown")]))
    assert r["citation_case"] == 0
    assert r["items"]["citation"]["denominator"] == 3


def test_errors_are_not_empty_and_latency_uses_all_samples():
    m = metrics()
    r = m.evaluate_case(gold(), obs([], status="error", error="BACKEND_TIMEOUT"))
    assert not r["empty_prediction"]
    s = m.summarize([r])["groups"]["documents/documents"]
    assert s["latency"]["warm"]["count"] == 5
    assert s["latency"]["warm"]["median_ms"] == 3
    assert s["latency"]["warm"]["p95_ms"] == 5


def test_missing_locator_and_item_aggregates_keep_raw_coverage():
    m = metrics()
    r = m.evaluate_case(gold(), obs([hit("a65")]))
    assert r["gold_locator_coverage"]["page_number"] == {"applicable": 2, "present": 1, "na": 0}
    s = m.summarize([r])["groups"]["documents/documents"]
    assert s["items"]["source"]["denominator"] == 1
    assert s["gold_locator_coverage"]["page_number"]["applicable"] == 2


def test_metadata_path_does_not_mix_canonical_macro():
    m = metrics()
    r = m.evaluate_case(dict(gold(), domain="history", path="canonical"), obs([]))
    metadata = m.evaluate_case(dict(gold(), domain="history", path="source_only"),
        obs([hit("a65"), hit("a66", page=66, evidence="Use sixths")]))
    s = m.summarize([r, metadata])
    assert s["macro"]["recall@10"]["value"] == 0


def test_paired_scope_control_records_exclusion_and_retention():
    m = metrics()
    case = gold()
    scoped = obs([hit("a65"), hit("a66", page=66, evidence="Use sixths")],
                 scope_control=obs([hit("a65"), hit("a66", page=66), hit("b65", "b")]))
    r = m.evaluate_case(case, scoped)
    assert r["scope_pair"] == {"status": "ok", "excluded": 1, "retained_gold": 2, "pass": True}
