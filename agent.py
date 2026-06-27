"""
agent.py — Hybrid Validation Agent (Phase 5)
---------------------------------------------
Architecture:
  1. Python fetches data from Oracle (via db.py) — reliable
  2. python_eval() evaluates the condition — 100% accurate math/comparisons
  3. LLM (Groq) writes a natural language summary — one simple call per rule
  4. No tool-calling loop — fast, deterministic, no rate-limit issues

Why hybrid?
  - LLMs make arithmetic errors → python_eval handles ALL calculations
  - LLM is great at explanation → used ONLY for the summary
  - Adding new rules to rulebook.json requires zero Python changes
"""

import json
import os
import time
from langchain_groq import ChatGroq
from langsmith import traceable
from config import GROQ_API_KEY
from db import get_course_catalog, get_enrollment, get_exam_eligibility, get_all_course_ids, execute_sql_rule
from tools import python_eval, compute_rule_comparison


# ─── LLM — used ONLY for summary, not for data fetching or math ───────────────

def _get_llm():
    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_key_here":
        raise ValueError(
            "GROQ_API_KEY is not set in your .env file.\n"
            "Get a free key at: https://console.groq.com/keys"
        )
    return ChatGroq(
        model="llama-3.1-8b-instant",
        api_key=GROQ_API_KEY,
        temperature=0,
        max_retries=2,
    )


# ─── LLM summary — single call, no tool loop ─────────────────────────────────

@traceable(name="llm_summarize")
def _summarize(llm, rule: dict, status: str, catalog: dict, enrollment: dict, exam: dict) -> str:
    """Ask the LLM to summarize the result in ONE sentence using actual values."""

    data_summary = []
    if catalog:
        data_summary.append(f"catalog.fee={catalog.get('fee')}")
    if enrollment:
        data_summary.append(f"enrollment.fee={enrollment.get('fee')}")
    if exam:
        data_summary.append(
            f"exam.fee_cleared={exam.get('fee_cleared')}, "
            f"exam.min_attendance_pct={exam.get('min_attendance_pct')}, "
            f"exam.is_eligible={exam.get('is_eligible')}"
        )

    prompt = (
        f"Rule: {rule['description']}\n"
        f"Condition: {rule['condition']}\n"
        f"Data: {', '.join(data_summary)}\n"
        f"Result: {status}\n\n"
        f"Write ONE short sentence summarizing why this rule {status}ED. "
        f"Include the actual numbers from the data. Do NOT recalculate — the result is already correct."
    )

    try:
        resp = llm.invoke(prompt)
        content = resp.content
        if isinstance(content, list):
            content = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        return str(content).strip()
    except Exception as e:
        err = str(e)
        if "429" in err or "rate_limit" in err.lower():
            time.sleep(20)
            try:
                resp = llm.invoke(prompt)
                return str(resp.content).strip()
            except Exception:
                pass
        return f"Condition '{rule['condition']}' evaluated to {status} with data: {', '.join(data_summary)}."


def _data_summary(catalog: dict, enrollment: dict, exam: dict) -> list[str]:
    parts = []
    if catalog:
        parts.append(f"catalog.fee={catalog.get('fee')}, course_name={catalog.get('course_name')}")
    if enrollment:
        parts.append(f"enrollment.fee={enrollment.get('fee')}, student={enrollment.get('student_name')}")
    if exam:
        parts.append(
            f"exam.fee_cleared={exam.get('fee_cleared')}, "
            f"exam.is_eligible={exam.get('is_eligible')}, "
            f"exam.min_attendance_pct={exam.get('min_attendance_pct')}"
        )
    return parts


@traceable(name="llm_evaluate_english")
def _evaluate_english_rule(
    llm, rule: dict, catalog: dict, enrollment: dict, exam: dict
) -> tuple[str, str]:
    """Evaluate a plain-English rule using the LLM. Returns (status, reason)."""
    prompt = (
        f"You are an expert university data validator. Analyze the data step-by-step:\n\n"
        f"Rule Description: {rule['description']}\n"
        f"Rule Check Condition: {rule['condition']}\n"
        f"Data from Database:\n  " + "\n  ".join(_data_summary(catalog, enrollment, exam)) + "\n\n"
        f"Let's think step-by-step:\n"
        f"1. What is the course name? Does it contain 'Basics' or 'Intro'?\n"
        f"2. Based on that, what are the requirements for this course?\n"
        f"3. Does the student's data (min_attendance_pct, fee_cleared, is_eligible) meet these requirements?\n\n"
        f"End your response with the final status and reason in this exact format:\n"
        f"STATUS: <PASS or FAIL>\n"
        f"REASON: <one sentence explanation with actual values>"
    )

    try:
        resp = llm.invoke(prompt)
        content = str(resp.content).strip()
        status, reason = "UNKNOWN", content
        for line in content.splitlines():
            if line.upper().startswith("STATUS:"):
                val = line.split(":", 1)[1].strip().upper()
                status = "PASS" if "PASS" in val else ("FAIL" if "FAIL" in val else "UNKNOWN")
            elif line.upper().startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        if status == "UNKNOWN":
            status = "PASS" if "PASS" in content.upper() else "FAIL"
        return status, reason
    except Exception as e:
        return "ERROR", f"LLM evaluation failed: {str(e)[:120]}"


@traceable(name="evaluate_sql")
def _evaluate_sql_rule(
    course_id: str, rule: dict, llm,
    catalog: dict, enrollment: dict, exam: dict
) -> tuple[str, str]:
    """
    Execute a SQL-type rule against Oracle (via admin connection) and return
    (status, reason).  The SQL query must return exactly one row, one column:
        1  →  PASS
        0  →  FAIL
    Supports all Oracle SQL: COUNT, DISTINCT, GROUP BY, HAVING,
    window functions (RANK, ROW_NUMBER, DENSE_RANK, LEAD, LAG),
    and cross-schema JOINs.
    """
    try:
        result = execute_sql_rule(rule["condition"], course_id=course_id)
        passed       = result["passed"]
        result_value = result["result_value"]
        status       = "PASS" if passed else "FAIL"

        # Use the LLM to produce a human-readable one-liner
        reason = _summarize(llm, rule, status, catalog, enrollment, exam)
        return status, reason

    except Exception as e:
        return "ERROR", f"SQL execution error — {e}"


# ─── Evaluate one rule ────────────────────────────────────────────────────────

@traceable(name="evaluate_rule", tags=["validation"])
def evaluate_single_rule(course_id: str, rule: dict, llm) -> dict:
    """
    Evaluate ONE rule for a course:
      1. Fetch data from Oracle (Python)
      2. arithmetic rules → python_eval(); english rules → LLM evaluates
      3. LLM summarizes arithmetic rules; english rules include reason in evaluation

    Special scope:
      scope="any_course" — rule PASSes if the condition is true for AT LEAST ONE
                           course in the database (not necessarily the submitted one).
    """
    catalog    = get_course_catalog(course_id)
    enrollment = get_enrollment(course_id)
    exam       = get_exam_eligibility(course_id)
    rule_type  = rule.get("type", "arithmetic")
    rule_scope = rule.get("scope", "this_course")

    comparison = {}

    # ── any_course scope: pass if condition holds for at least one course ──────
    if rule_type == "arithmetic" and rule_scope == "any_course":
        all_ids = get_all_course_ids()
        passing_courses = []
        failing_courses = []

        for cid in all_ids:
            c_cat  = get_course_catalog(cid)
            c_enr  = get_enrollment(cid)
            c_exam = get_exam_eligibility(cid)
            if c_cat is None or c_enr is None:
                failing_courses.append(cid)
                continue
            try:
                ok = python_eval(rule["condition"], c_cat, c_enr, c_exam)
                (passing_courses if ok else failing_courses).append(cid)
            except Exception:
                failing_courses.append(cid)

        if passing_courses:
            status = "PASS"
            reason = (
                f"Rule satisfied by at least one course: "
                f"{', '.join(passing_courses)} ✔ "
                f"(also checked: {', '.join(failing_courses) or 'none'})."
            )
        else:
            status = "FAIL"
            reason = (
                f"Rule failed for all courses: "
                f"{', '.join(failing_courses)}. "
                f"No course satisfies the condition."
            )
            comparison = compute_rule_comparison(
                rule["condition"], catalog, enrollment, exam
            )

        return {
            "rule_id":     rule["rule_id"],
            "description": rule["description"],
            "condition":   rule["condition"],
            "severity":    rule["severity"],
            "status":      status,
            "reason":      reason,
            "comparison":  comparison,
            "on_failure":  rule.get("on_failure", ""),
            "sources":     rule.get("data_sources", []),
        }

    # ── standard single-course evaluation ──────────────────────────────────────────────
    if rule_type == "english":
        status, reason = _evaluate_english_rule(llm, rule, catalog, enrollment, exam)
    elif rule_type == "sql":
        status, reason = _evaluate_sql_rule(course_id, rule, llm, catalog, enrollment, exam)
    else:
        try:
            passed = python_eval(rule["condition"], catalog, enrollment, exam)
            status = "PASS" if passed else "FAIL"
            reason = _summarize(llm, rule, status, catalog, enrollment, exam)
            if not passed:
                comparison = compute_rule_comparison(
                    rule["condition"], catalog, enrollment, exam
                )
        except (SyntaxError, NameError, TypeError, AttributeError) as e:
            status = "ERROR"
            reason = f"Invalid condition — {e}"
        except Exception as e:
            status = "ERROR"
            reason = f"Could not evaluate condition: {e}"

    return {
        "rule_id":     rule["rule_id"],
        "description": rule["description"],
        "condition":   rule["condition"],
        "severity":    rule["severity"],
        "status":      status,
        "reason":      reason,
        "comparison":  comparison,
        "on_failure":  rule.get("on_failure", ""),
        "sources":     rule.get("data_sources", []),
    }


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_validation_agent(course_id: str, rule_ids: list = None) -> list:
    """
    Run validation for a course — checks each rule ONE BY ONE.

    Parameters:
        course_id : e.g. "C001"
        rule_ids  : optional filter, e.g. ["R1", "R2"]

    Returns:
        List of result dicts compatible with reporter.py and index.html
    """
    rulebook_path = os.path.join(os.path.dirname(__file__), "rulebook.json")
    with open(rulebook_path) as f:
        rules = json.load(f)["rules"]

    if rule_ids:
        rules = [r for r in rules if r["rule_id"] in rule_ids]

    llm     = _get_llm()
    results = []

    print(f"\n{'='*55}")
    print(f"  Validation Agent — Course: {course_id}")
    print(f"  Evaluating: {[r['rule_id'] for r in rules]}")
    print(f"  Engine: python_eval (arithmetic) + LLM (english)")
    print(f"{'='*55}\n")

    for i, rule in enumerate(rules):
        print(f"── {rule['rule_id']}: {rule['description']}")
        result = evaluate_single_rule(course_id, rule, llm)
        icon   = "✅" if result["status"] == "PASS" else "❌"
        print(f"   {icon} {result['status']} — {result['reason']}\n")
        results.append(result)

        if i < len(rules) - 1:
            time.sleep(3)

    passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"{'='*55}")
    print(f"  Result: {passed}/{len(results)} rules passed for {course_id}")
    print(f"{'='*55}\n")

    return results
