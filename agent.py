"""
agent.py — The Validation Agent (Phase 3 — Hybrid Architecture)
----------------------------------------------------------------
Architecture:
  1. Python fetches data from Oracle (via db.py) — reliable
  2. Python evaluates the condition using eval() — 100% accurate math
  3. LLM (Groq) writes a natural language explanation — one simple call
  4. No LangChain agent loop = no empty-response errors

Why hybrid?
  - LangChain agent loops cause empty-response errors with small models
  - LLM is bad at arithmetic → Python does ALL math
  - LLM is great at explanation → used ONLY for that
  - Result: fast, accurate, no rate-limit issues from multi-call loops
"""

import json
import os
import time
from langchain_groq import ChatGroq
from config import GROQ_API_KEY
from db import get_course_catalog, get_enrollment, get_exam_eligibility


# ─── LLM — used ONLY for explanation, not for data fetching or math ───────────

def _get_llm():
    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_key_here":
        raise ValueError(
            "GROQ_API_KEY is not set in your .env file.\n"
            "Get a free key at: https://console.groq.com/keys"
        )
    return ChatGroq(
        model="llama-3.1-8b-instant",   # 20,000 TPM — plenty for single explanation calls
        api_key=GROQ_API_KEY,
        temperature=0,
        max_retries=2,
    )


# ─── Condition evaluator — pure Python, 100% accurate ────────────────────────

class _Namespace:
    """Converts a dict like {'fee': 5000} into an object with catalog.fee syntax."""
    def __init__(self, data: dict):
        for k, v in (data or {}).items():
            setattr(self, k, v)

    def __repr__(self):
        return str(self.__dict__)


def _evaluate_condition(condition: str, catalog: dict, enrollment: dict, exam: dict) -> bool:
    """
    Evaluate a rule condition using Python eval().
    Supports: catalog.fee, enrollment.fee, exam.fee_cleared, exam.attendance, etc.
    Safe eval — no builtins allowed.
    """
    ns = {
        "catalog":    _Namespace(catalog    or {}),
        "enrollment": _Namespace(enrollment or {}),
        "exam":       _Namespace(exam       or {}),
        "__builtins__": {},   # block any dangerous builtins
    }
    return bool(eval(condition, ns))


# ─── Compute actual vs expected values for failed rules ───────────────────────

def _compute_comparison(condition: str, catalog: dict, enrollment: dict, exam: dict) -> dict:
    """
    For a FAILED condition, evaluate both sides and return:
      { lhs_expr, lhs_val, operator, rhs_expr, rhs_val }
    e.g. condition "catalog.fee == enrollment.fee - 500"
      → { lhs_expr:"catalog.fee", lhs_val:5000,
          operator:"==",
          rhs_expr:"enrollment.fee - 500", rhs_val:9000 }
    """
    # Try operators longest first so >= isn't split at >
    for op in ["==", ">=", "<=", "!=", ">", "<"]:
        if op in condition:
            lhs_str, rhs_str = condition.split(op, 1)
            lhs_str = lhs_str.strip()
            rhs_str = rhs_str.strip()
            ns = {
                "catalog":      _Namespace(catalog    or {}),
                "enrollment":   _Namespace(enrollment or {}),
                "exam":         _Namespace(exam       or {}),
                "__builtins__": {},
            }
            try:
                lhs_val = eval(lhs_str, ns)
                rhs_val = eval(rhs_str, ns)
                return {
                    "lhs_expr": lhs_str,
                    "lhs_val":  lhs_val,
                    "operator": op,
                    "rhs_expr": rhs_str,
                    "rhs_val":  rhs_val,
                }
            except Exception:
                pass
    return {}


# ─── LLM explanation — single call, no loop ──────────────────────────────────

def _explain(llm, rule: dict, status: str, catalog: dict, enrollment: dict, exam: dict) -> str:
    """Ask the LLM to explain the result in ONE sentence using actual values."""

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
        f"Write ONE short sentence explaining why the condition is {status}. "
        f"Show the actual numbers used in the calculation."
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
        # Fallback — Python generates the explanation if LLM fails
        return f"Condition '{rule['condition']}' evaluated to {status} with data: {', '.join(data_summary)}."


# ─── Evaluate one rule ────────────────────────────────────────────────────────

def _evaluate_with_llm(llm, rule: dict, catalog: dict, enrollment: dict, exam: dict) -> tuple:
    """
    Fallback: ask LLM to evaluate a condition that Python cannot parse.
    Used when the condition is plain English or complex logic.
    Returns (status, reason).
    """
    data_summary = []
    if catalog:
        data_summary.append(f"catalog.fee={catalog.get('fee')}, course_name={catalog.get('course_name')}")
    if enrollment:
        data_summary.append(f"enrollment.fee={enrollment.get('fee')}, student={enrollment.get('student_name')}")
    if exam:
        data_summary.append(
            f"exam.fee_cleared={exam.get('fee_cleared')}, "
            f"exam.min_attendance_pct={exam.get('min_attendance_pct')}, "
            f"exam.is_eligible={exam.get('is_eligible')}"
        )

    prompt = (
        f"You are a university data validator.\n"
        f"Rule: {rule['description']}\n"
        f"Condition to evaluate: {rule['condition']}\n"
        f"Data from database:\n  " + "\n  ".join(data_summary) + "\n\n"
        f"Does the data satisfy the condition? Reply with EXACTLY:\n"
        f"STATUS: PASS\nREASON: <one sentence with actual values>\n\n"
        f"OR\n\nSTATUS: FAIL\nREASON: <one sentence with actual values>"
    )

    try:
        resp = llm.invoke(prompt)
        content = str(resp.content).strip()
        # Parse STATUS and REASON from LLM response
        status, reason = "UNKNOWN", content
        for line in content.splitlines():
            if line.upper().startswith("STATUS:"):
                val = line.split(":", 1)[1].strip().upper()
                status = "PASS" if "PASS" in val else ("FAIL" if "FAIL" in val else "UNKNOWN")
            elif line.upper().startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        return status, reason
    except Exception as e:
        return "ERROR", f"LLM fallback also failed: {str(e)[:100]}"


def evaluate_single_rule(course_id: str, rule: dict, llm) -> dict:
    """
    Evaluate ONE rule for a course:
      1. Fetch data from Oracle (Python)
      2. Try Python eval() for the condition (100% accurate for math)
      3. If eval() fails (e.g. plain English) → LLM evaluates it
      4. LLM always generates the final explanation
    """
    # Step 1: Fetch data from Oracle
    catalog    = get_course_catalog(course_id)
    enrollment = get_enrollment(course_id)
    exam       = get_exam_eligibility(course_id)

    # Step 2: Try Python eval first (handles all math/comparison conditions)
    try:
        passed = _evaluate_condition(rule["condition"], catalog, enrollment, exam)
        status = "PASS" if passed else "FAIL"
        reason = _explain(llm, rule, status, catalog, enrollment, exam)
        comparison = {} if passed else _compute_comparison(rule["condition"], catalog, enrollment, exam)

    except (SyntaxError, NameError, TypeError, AttributeError):
        print(f"   ℹ️  Non-Python condition detected — using LLM to evaluate...")
        status, reason = _evaluate_with_llm(llm, rule, catalog, enrollment, exam)
        comparison = {}

    except Exception as e:
        status = "ERROR"
        reason = f"Could not evaluate condition: {str(e)}"
        comparison = {}

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
    # Load rules from rulebook.json (read fresh every call — no restart needed)
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
    print(f"{'='*55}\n")

    for i, rule in enumerate(rules):
        print(f"── {rule['rule_id']}: {rule['description']}")
        result = evaluate_single_rule(course_id, rule, llm)
        icon   = "✅" if result["status"] == "PASS" else "❌"
        print(f"   {icon} {result['status']} — {result['reason']}\n")
        results.append(result)

        # Small delay between rules to avoid rate limits
        if i < len(rules) - 1:
            time.sleep(3)

    passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"{'='*55}")
    print(f"  Result: {passed}/{len(results)} rules passed for {course_id}")
    print(f"{'='*55}\n")

    return results
