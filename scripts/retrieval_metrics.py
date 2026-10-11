"""Pure P15 metrics. No stores, retrieval calls, or inferred relevance."""
from collections import defaultdict
import math
import statistics


def fraction(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def validate_gold(case):
    relevant = set(case["relevant_ids"])
    graded = {key for key, grade in case["relevance_grades"].items() if grade >= 2}
    if len(relevant) != len(case["relevant_ids"]) or not (
        relevant == graded == set(case["expected_locators"]) == set(case["expected_support"])
    ):
        raise ValueError("Relevant IDs, independent support, locators and grades differ")
    if case["should_have_result"] != bool(relevant) and case.get("measurement") != "contract":
        raise ValueError("Positive/negative gold differs from relevant IDs")
    for key in relevant:
        if set(case["expected_locators"][key]) != {"source_id", "page_number", "chapter_id"}:
            raise ValueError("All locator fields must be authored, including null N/A")
        if not case["expected_support"][key] or not all(case["expected_support"][key]):
            raise ValueError("Independent support must be nonempty")


def _in_scope(hit, filters):
    loc = hit.get("locator", {})
    for key, field in (("source_ids", "source_id"), ("chapter_ids", "chapter_id")):
        if filters.get(key) and loc.get(field) not in filters[key]:
            return False
    for key, field in (("page_range", "page_number"), ("time_range", "occurred_at")):
        if filters.get(key):
            value = loc.get(field)
            if value is None or not filters[key][0] <= value <= filters[key][1]:
                return False
    return not filters.get("source_system") or loc.get("source_system") == filters["source_system"]


def evaluate_case(case, observation):
    validate_gold(case)
    status = observation["status"]
    if status not in {"ok", "error", "unsupported"}:
        raise ValueError("Unknown observation status")
    contract = case.get("measurement") == "contract"
    eligible = status != "unsupported" and not contract
    relevant = set(case["relevant_ids"])
    ranking = observation.get("ranking", []) if status != "error" else []
    unique = list({hit["id"]: hit for hit in reversed(ranking)}.values())
    unique.reverse()
    result = {"case_id": case["case_id"], "domain": case["domain"],
              "path": case.get("path", case["domain"]), "status": status,
              "quality_eligible": eligible, "positive": bool(relevant),
              "contract": contract, "contract_pass": contract and observation.get("error") == case.get("expected_error"),
              "capability_gap": "capacity-2049" if contract else observation.get("unsupported", []),
              "empty_prediction": status == "ok" and not ranking,
              "returned_nonempty": status == "ok" and bool(ranking),
              "observation": observation, "relevant_count": len(relevant)}
    for k in (1, 3, 5, 10):
        matched = len({hit["id"] for hit in ranking[:k]} & relevant)
        result[f"recall@{k}"] = matched / len(relevant) if relevant and eligible else None
        result[f"recall@{k}_counts"] = fraction(matched, len(relevant))
    result["mrr@10"] = next((1 / i for i, hit in enumerate(ranking[:10], 1) if hit["id"] in relevant), 0) if relevant and eligible else None
    checks = {"source": [], "page": [], "citation": []}
    coverage = {field: {"applicable": 0, "present": 0, "na": 0, "unknown": 0}
                for field in ("source_id", "page_number", "chapter_id")}
    for hit in unique:
        expected = case["expected_locators"].get(hit["id"])
        actual = hit.get("locator", {})
        for field, counts in coverage.items():
            if expected is None:
                counts["unknown"] += 1
            elif expected[field] is None:
                counts["na"] += 1
            else:
                counts["applicable"] += 1
                counts["present"] += actual.get(field) is not None
        source_ok = expected is not None and actual.get("source_id") == expected["source_id"]
        checks["source"].append(source_ok)
        if expected is None or expected["page_number"] is not None:
            checks["page"].append(source_ok and actual.get("page_number") == expected["page_number"])
        checks["citation"].append(source_ok and all(
            text.casefold() in hit.get("evidence", "").casefold()
            for text in case["expected_support"].get(hit["id"], ["\0missing gold"])))
    returned = {hit["id"] for hit in unique}
    complete = status == "ok" and returned == relevant and bool(relevant)
    result["items"] = {key: fraction(sum(values), len(values)) for key, values in checks.items()}
    result["locator_coverage"] = coverage
    by_id = {hit["id"]: hit for hit in unique}
    result["gold_locator_coverage"] = {
        field: {"applicable": sum(loc[field] is not None for loc in case["expected_locators"].values()),
                "present": sum(loc[field] is not None and by_id.get(id, {}).get("locator", {}).get(field) is not None
                               for id, loc in case["expected_locators"].items()),
                "na": sum(loc[field] is None for loc in case["expected_locators"].values())}
        for field in ("source_id", "page_number", "chapter_id")}
    for key, values in checks.items():
        applicable = key != "page" or any(loc["page_number"] is not None for loc in case["expected_locators"].values())
        result[key + "_case"] = int(complete and all(values)) if relevant and eligible and applicable else None
    scoped = any(value is not None for value in case.get("filters", {}).values())
    result["filter_case"] = int(status == "ok" and all(_in_scope(hit, case["filters"]) for hit in unique)
                                and (not relevant or relevant <= returned)) if scoped and eligible else None
    control = observation.get("scope_control")
    result["scope_pair"] = {"status": "not_measured", "excluded": None, "retained_gold": None, "pass": None}
    if control and eligible:
        outside = {hit["id"] for hit in control.get("ranking", []) if not _in_scope(hit, case["filters"])}
        pair_pass = control["status"] == "ok" and result["filter_case"] == 1 and not (returned & outside)
        result["scope_pair"] = {"status": control["status"], "excluded": len(outside - returned),
                                "retained_gold": len(returned & relevant), "pass": pair_pass}
        if not pair_pass:
            result["filter_case"] = 0
    return result


def latency(samples):
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in samples):
        raise ValueError("Latency samples must be finite and nonnegative")
    ordered = sorted(samples)
    return {"count": len(samples), "samples_ms": samples,
            "median_ms": statistics.median(samples) if samples else None,
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None}


def summarize(results):
    groups = defaultdict(list)
    for result in results:
        groups[result["domain"] + "/" + result["path"]].append(result)
    output = {"groups": {}, "coverage": {"all": len(results),
        "quality_supported": sum(r["quality_eligible"] for r in results),
        "unsupported": sum(r["status"] == "unsupported" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "contract": sum(r["contract"] for r in results),
        "contract_pass": sum(r["contract_pass"] for r in results)}}
    for key, rows in groups.items():
        eligible = [r for r in rows if r["quality_eligible"]]
        negative = [r for r in eligible if not r["positive"]]
        empty = [r for r in eligible if r["empty_prediction"]]
        summary = {"cases": len(rows), "quality_supported": len(eligible),
            "unsupported_cases": [r["case_id"] for r in rows if r["status"] == "unsupported"],
            "error_cases": [r["case_id"] for r in rows if r["status"] == "error"],
            "no_result_precision": fraction(sum(not r["positive"] for r in empty), len(empty)),
            "negative_empty_recall": fraction(sum(r["empty_prediction"] for r in negative), len(negative)),
            "false_positive_return_rate": fraction(sum(r["returned_nonempty"] for r in negative), len(negative))}
        for metric in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr@10", "source_case", "page_case", "citation_case", "filter_case"):
            values = [r[metric] for r in eligible if r[metric] is not None]
            summary[metric] = fraction(sum(values), len(values))
        summary["latency"] = {"cold": latency([r["observation"]["cold_ms"] for r in rows if r["observation"].get("cold_ms") is not None]),
                              "warm": latency([sample for r in rows for sample in r["observation"].get("warm_ms", [])])}
        summary["items"] = {metric: fraction(sum(r["items"][metric]["numerator"] for r in eligible),
                                            sum(r["items"][metric]["denominator"] for r in eligible))
                            for metric in ("source", "page", "citation")}
        for coverage_key in ("locator_coverage", "gold_locator_coverage"):
            summary[coverage_key] = {field: {count: sum(r[coverage_key][field][count] for r in eligible)
                for count in ("applicable", "present", "na", "unknown") if any(count in r[coverage_key][field] for r in eligible)}
                for field in ("source_id", "page_number", "chapter_id")}
        output["groups"][key] = summary
    output["macro"] = {metric: fraction(sum(values), len(values)) for metric in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr@10")
                       for values in [[group[metric]["value"] for key, group in output["groups"].items()
                                       if key not in {"history/source_only", "history/legacy"} and group[metric]["value"] is not None]]}
    return output
