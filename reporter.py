"""
reporter.py — Human-readable Validation Report Generator (Phase 2.5)
---------------------------------------------------------------------
This module takes raw rule evaluation results and converts them into
a structured, human-readable report with:

  - Overall health score
  - Data snapshot from all 3 Oracle schemas
  - Passed / Failed rule breakdown
  - Remediation recommendations
  - Overall status: HEALTHY / WARNING / CRITICAL

This module is STATELESS — it only reads data passed to it.
It does NOT connect to the database or read rulebook.json directly.

In Phase 3, this reporter will be replaced/enhanced by an LLM
that generates even richer natural language reports.
"""

from datetime import datetime


# ─── Status thresholds ────────────────────────────────────────────────────────
# These define what percentage of rules must pass for each health level
HEALTHY_THRESHOLD  = 100   # all rules pass
WARNING_THRESHOLD  = 60    # at least 60% pass
# below 60% → CRITICAL


def generate_report(course_id, catalog, enrollment, exam, results):
    """
    Generate a structured report dict from validation results.

    Parameters:
        course_id  (str)       : e.g. "C001"
        catalog    (dict)      : row from CATALOG_USER.COURSE_CATALOG
        enrollment (dict)      : row from ENROLL_USER.ENROLLMENT
        exam       (dict|None) : row from EXAM_USER.EXAM_ELIGIBILITY
        results    (list)      : output of evaluate_rules()

    Returns:
        A report dict with the following keys:
        {
            "course_id"      : "C001",
            "timestamp"      : "2026-06-10 14:05:33",
            "health_score"   : 60.0,       # % of rules that passed
            "overall_status" : "WARNING",  # HEALTHY / WARNING / CRITICAL
            "total_rules"    : 5,
            "passed_count"   : 3,
            "failed_count"   : 2,
            "data_snapshot"  : { ... },    # what was fetched from each schema
            "passed_rules"   : [ ... ],    # list of passed rule dicts
            "failed_rules"   : [ ... ],    # list of failed rule dicts with detail
            "recommendations": [ ... ],    # list of action strings
            "summary_line"   : "3 of 5 rules passed — WARNING"
        }
    """

    # ── Count pass / fail ─────────────────────────────────────────────────────
    total      = len(results)
    passed     = [r for r in results if r["status"] == "PASS"]
    failed     = [r for r in results if r["status"] != "PASS"]
    pass_count = len(passed)
    fail_count = len(failed)

    # ── Health score as percentage ────────────────────────────────────────────
    health_score = round((pass_count / total * 100), 1) if total > 0 else 0.0

    # ── Overall status ────────────────────────────────────────────────────────
    if health_score == HEALTHY_THRESHOLD:
        overall_status = "HEALTHY"
    elif health_score >= WARNING_THRESHOLD:
        overall_status = "WARNING"
    else:
        overall_status = "CRITICAL"

    # ── Data snapshot: what was fetched from each schema ─────────────────────
    data_snapshot = {
        "CATALOG_USER.course_catalog": {
            "course_id":   catalog.get("course_id"),
            "course_name": catalog.get("course_name"),
            "fee":         catalog.get("fee"),
        },
        "ENROLL_USER.enrollment": {
            "enrollment_id": enrollment.get("enrollment_id"),
            "course_id":     enrollment.get("course_id"),
            "fee":           enrollment.get("fee"),
        },
        "EXAM_USER.exam_eligibility": {
            "eligibility_id":    exam.get("eligibility_id")    if exam else "N/A",
            "is_eligible":       exam.get("is_eligible")       if exam else "N/A",
            "min_attendance_pct": exam.get("min_attendance_pct") if exam else "N/A",
            "fee_cleared":       exam.get("fee_cleared")       if exam else "N/A",
        }
    }

    # ── Build failed rule detail list ─────────────────────────────────────────
    # For each failed rule, compute a human-readable "found vs expected" detail
    failed_rules_detail = []
    for r in failed:
        detail = _build_failure_detail(r, catalog, enrollment, exam)
        failed_rules_detail.append({
            "rule_id":     r["rule_id"],
            "description": r["description"],
            "severity":    r["severity"],
            "condition":   r["condition"],
            "status":      r["status"],
            "on_failure":  r["on_failure"],
            "detail":      detail,
        })

    # ── Build recommendations ─────────────────────────────────────────────────
    # Prioritise HIGH severity failures first
    high_failures   = [r for r in failed_rules_detail if r["severity"] == "HIGH"]
    medium_failures = [r for r in failed_rules_detail if r["severity"] == "MEDIUM"]
    recommendations = []

    for i, r in enumerate(high_failures + medium_failures, start=1):
        recommendations.append(f"{i}. [{r['severity']}] {r['on_failure']}")

    if not recommendations:
        recommendations.append("✓ No action required — all validations passed.")

    # ── Summary line ──────────────────────────────────────────────────────────
    summary_line = (
        f"{pass_count} of {total} rule(s) passed — {overall_status} "
        f"(Health Score: {health_score}%)"
    )

    return {
        "course_id":       course_id,
        "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "health_score":    health_score,
        "overall_status":  overall_status,
        "total_rules":     total,
        "passed_count":    pass_count,
        "failed_count":    fail_count,
        "data_snapshot":   data_snapshot,
        "passed_rules":    passed,
        "failed_rules":    failed_rules_detail,
        "recommendations": recommendations,
        "summary_line":    summary_line,
    }


def _build_failure_detail(rule, catalog, enrollment, exam):
    """
    Build a human-readable 'found vs expected' explanation for a failed rule.

    This function inspects which fields the condition references
    and constructs a plain-English description of what went wrong.
    """
    condition = rule["condition"]
    parts     = []

    # ── Extract relevant field values referenced in the condition ─────────────
    # Check which schema objects are mentioned in the condition string
    if "catalog.fee" in condition:
        parts.append(f"CATALOG_USER fee = ₹{catalog.get('fee')}")

    if "enrollment.fee" in condition:
        parts.append(f"ENROLL_USER fee = ₹{enrollment.get('fee')}")

    if exam and "exam.fee_cleared" in condition:
        parts.append(f"EXAM_USER fee_cleared = '{exam.get('fee_cleared')}'")

    if exam and "exam.min_attendance_pct" in condition:
        parts.append(f"EXAM_USER attendance = {exam.get('min_attendance_pct')}%")

    if exam and "exam.is_eligible" in condition:
        parts.append(f"EXAM_USER is_eligible = '{exam.get('is_eligible')}'")

    # ── Fee mismatch: compute the difference ──────────────────────────────────
    if "catalog.fee" in condition and "enrollment.fee" in condition:
        cat_fee = catalog.get("fee", 0)
        enr_fee = enrollment.get("fee", 0)
        diff    = abs(enr_fee - cat_fee)
        if diff > 0:
            parts.append(f"Discrepancy = ₹{diff}")

    found_str    = " | ".join(parts) if parts else "See condition below"
    expected_str = f"Condition must be TRUE: {condition}"

    return {
        "found":    found_str,
        "expected": expected_str,
    }
