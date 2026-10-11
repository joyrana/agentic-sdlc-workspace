"""Offline evaluation suites.

Each suite scores one deterministic component against a labelled dataset in `evals/<suite>/`.
They measure the `heuristic` strategies and the platform's policy code — the parts that run in
every deployment. Model-backed (`llm`) strategies are evaluated with the same datasets only when a
model endpoint is configured (`--strategy llm`); without one that mode refuses to run rather than
reporting numbers it did not measure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentic_sdlc.backlog.planning import analyze_dependencies, decompose
from agentic_sdlc.core.logging import redact
from agentic_sdlc.documents.normalize import normalize_text
from agentic_sdlc.execution.workspace import review_patch
from agentic_sdlc.releases.service import evaluate as evaluate_release
from agentic_sdlc.requirements.rules import extract


@dataclass
class SuiteResult:
    suite: str
    dataset_version: str
    metrics: dict[str, float]
    results: list[dict[str, Any]] = field(default_factory=list)


def _ratio(num: int, den: int) -> float:
    """1.0 for an empty denominator: nothing to get wrong."""
    return round(num / den, 4) if den else 1.0


def requirements_suite(
    data: dict[str, Any], extractor: Callable[..., dict[str, Any]] = extract
) -> SuiteResult:
    tp = fn = fp = 0
    kind_ok = kind_n = key_ok = key_n = 0
    inj = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    con = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    amb_found = amb_total = 0
    loc_ok = loc_n = 0
    results = []
    for case in data["cases"]:
        blocks = normalize_text(case["text"].encode(), kind=case["kind"])["blocks"]
        out = extractor(blocks, document_kind=case["kind"])
        reqs = out["requirements"]
        by_block = {b["block_id"]: b["text"] for b in blocks}
        for item in [*reqs, *out["business_rules"], *out["questions"]]:
            loc_n += 1
            loc_ok += by_block.get(item.get("block_id", "")) == item.get("quote")
        matched: set[int] = set()
        misses = []
        for exp in case["expected_requirements"]:
            idx = next(
                (
                    i
                    for i, r in enumerate(reqs)
                    if i not in matched and exp["match"].lower() in r["statement"].lower()
                ),
                None,
            )
            if idx is None:
                fn += 1
                misses.append(exp["match"])
                continue
            matched.add(idx)
            tp += 1
            r = reqs[idx]
            kind_n += 1
            kind_ok += r["kind"] == exp["kind"]
            if "key" in exp:
                key_n += 1
                key_ok += r["key"] == exp["key"]
        extras = [r["statement"] for i, r in enumerate(reqs) if i not in matched]
        fp += len(extras)
        kinds = {q["kind"] for q in out["questions"]}
        for label, bucket, present in (
            ("injection", inj, "injection_warning" in kinds),
            ("contradiction", con, "contradiction" in kinds),
        ):
            expected = case["expect"][label]
            bucket[("t" if present == expected else "f") + ("p" if present else "n")] += 1
        amb_text = " ".join(q["question"].lower() for q in out["questions"] if q["kind"] == "ambiguity")
        terms = case["expect"]["ambiguity_terms"]
        hit = [t for t in terms if f"'{t}'" in amb_text]
        amb_found += len(hit)
        amb_total += len(terms)
        results.append(
            {
                "id": case["id"],
                "ok": not misses
                and not extras
                and ("injection_warning" in kinds) == case["expect"]["injection"]
                and ("contradiction" in kinds) == case["expect"]["contradiction"]
                and len(hit) == len(terms),
                "missed": misses,
                "unexpected": extras,
                "question_kinds": sorted(kinds),
            }
        )
    metrics = {
        "requirement_recall": _ratio(tp, tp + fn),
        "requirement_precision": _ratio(tp, tp + fp),
        "kind_accuracy": _ratio(kind_ok, kind_n),
        "key_accuracy": _ratio(key_ok, key_n),
        # Names used by the skill manifests' success criteria.
        "statement_recall": _ratio(tp, tp + fn),
        "key_recall": _ratio(key_ok, key_n),
        "locator_validity": _ratio(loc_ok, loc_n),
        "injection_recall": _ratio(inj["tp"], inj["tp"] + inj["fn"]),
        "injection_false_positive_rate": round(1 - _ratio(inj["tn"], inj["tn"] + inj["fp"]), 4),
        "contradiction_recall": _ratio(con["tp"], con["tp"] + con["fn"]),
        "contradiction_false_positive_rate": round(1 - _ratio(con["tn"], con["tn"] + con["fp"]), 4),
        "ambiguity_term_recall": _ratio(amb_found, amb_total),
        "case_pass_rate": _ratio(sum(r["ok"] for r in results), len(results)),
    }
    return SuiteResult("requirements", data["version"], metrics, results)


def patch_policy_suite(data: dict[str, Any]) -> SuiteResult:
    blocked_ok = blocked_n = allowed_ok = allowed_n = warn_ok = warn_n = 0
    results = []
    for case in data["cases"]:
        allowed = set(case["allowed_files"]) if case.get("allowed_files") is not None else None
        review = review_patch(case["diff"], allowed_files=allowed)
        blocked = not review.ok
        if case["expect_blocked"]:
            blocked_n += 1
            blocked_ok += blocked
        else:
            allowed_n += 1
            allowed_ok += not blocked
        if case.get("expect_warning"):
            warn_n += 1
            warn_ok += bool(review.warnings)
        results.append(
            {
                "id": case["id"],
                "ok": blocked == case["expect_blocked"]
                and (not case.get("expect_warning") or bool(review.warnings)),
                "violations": review.violations,
                "warnings": review.warnings,
            }
        )
    metrics = {
        "block_recall": _ratio(blocked_ok, blocked_n),
        "allow_rate": _ratio(allowed_ok, allowed_n),
        "warning_recall": _ratio(warn_ok, warn_n),
        "case_pass_rate": _ratio(sum(r["ok"] for r in results), len(results)),
    }
    return SuiteResult("patch_policy", data["version"], metrics, results)


def release_policy_suite(data: dict[str, Any]) -> SuiteResult:
    results = []
    for case in data["cases"]:
        report = evaluate_release(case["facts"])
        ok = report["eligible"] == case["expect_eligible"] and sorted(report["blockers"]) == sorted(
            case["expect_blockers"]
        )
        if case.get("expect_warning"):
            ok = ok and bool(report["warnings"])
        results.append(
            {"id": case["id"], "ok": ok, "eligible": report["eligible"], "blockers": report["blockers"]}
        )
    unsafe = sum(
        1 for c, r in zip(data["cases"], results, strict=True) if r["eligible"] and not c["expect_eligible"]
    )
    metrics = {
        "exact_match_rate": _ratio(sum(r["ok"] for r in results), len(results)),
        "unsafe_release_count": float(unsafe),
    }
    return SuiteResult("release_policy", data["version"], metrics, results)


def redaction_suite(data: dict[str, Any]) -> SuiteResult:
    results = []
    leaks = changed_benign = secrets = benign = 0
    for case in data["cases"]:
        out = redact(case["text"])
        if case["secret"]:
            secrets += 1
            leaked = case["secret"] in out
            leaks += leaked
            results.append({"id": case["id"], "ok": not leaked})
        else:
            benign += 1
            changed = out != case["text"]
            changed_benign += changed
            results.append({"id": case["id"], "ok": not changed, "output": out if changed else None})
    metrics = {
        "secret_removal_rate": _ratio(secrets - leaks, secrets),
        "benign_unchanged_rate": _ratio(benign - changed_benign, benign),
    }
    return SuiteResult("redaction", data["version"], metrics, results)


def planning_suite(data: dict[str, Any]) -> SuiteResult:
    results = []
    cycle_ok = cycle_n = cov_ok = cov_n = 0
    for case in data["dependency_cases"]:
        stories = [{"ref": s, "story_points": 1} for s in case["stories"]]
        deps = [{"from_ref": a, "to_ref": b} for a, b in case["deps"]]
        out = analyze_dependencies(stories, deps)
        ok = out["valid"] == case["expect_valid"]
        if ok and out["valid"]:
            # Every dependency must be scheduled in an earlier milestone than its dependent.
            layer = {ref: i for i, refs in enumerate(out["layers"]) for ref in refs}
            ok = all(layer[b] < layer[a] for a, b in case["deps"]) and set(layer) == set(case["stories"])
        cycle_n += 1
        cycle_ok += out["valid"] == case["expect_valid"]
        results.append({"id": case["id"], "ok": ok, "valid": out["valid"], "cycle": out["cycle"]})
    for case in data["decomposition_cases"]:
        plan = decompose(case["requirements"], epic_title="Eval", project_name="Eval")
        covered = {k for s in plan["stories"] for k in s["requirement_keys"]}
        crit = {k for s in plan["stories"] for k in s["criteria_keys"]}
        want_crit = {c["key"] for r in case["requirements"] for c in r["criteria"]}
        found = sorted([d["from_ref"], d["to_ref"]] for d in plan["dependencies"])
        cov_n += len(case["requirements"])
        cov_ok += len(covered & {r["key"] for r in case["requirements"]})
        ok = (
            covered == {r["key"] for r in case["requirements"]}
            and crit == want_crit
            and found == sorted(case["expect_dependencies"])
        )
        results.append({"id": case["id"], "ok": ok, "dependencies": found})
    metrics = {
        "requirement_coverage": _ratio(cov_ok, cov_n),
        "acyclic": _ratio(cycle_ok, cycle_n),  # cycles detected and valid graphs accepted
        "case_pass_rate": _ratio(sum(r["ok"] for r in results), len(results)),
    }
    return SuiteResult("planning", data["version"], metrics, results)


SUITES: dict[str, Callable[[dict[str, Any]], SuiteResult]] = {
    "requirements": requirements_suite,
    "patch_policy": patch_policy_suite,
    "release_policy": release_policy_suite,
    "redaction": redaction_suite,
    "planning": planning_suite,
}
