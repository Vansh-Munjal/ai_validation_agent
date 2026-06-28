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
from db import get_course_catalog, get_enrollment, get_exam_eligibility, get_all_course_ids, execute_sql_rule, get_all_enrollments_for_course, get_enrollment_by_roll_no, get_all_roll_nos
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
def evaluate_single_rule(course_id: str, rule: dict, llm,
                         enrollment_override: dict = None) -> dict:
    """
    Evaluate ONE rule for a course:
      1. Fetch data from Oracle (Python)
      2. arithmetic rules → python_eval(); english rules → LLM evaluates
      3. LLM summarizes arithmetic rules; english rules include reason in evaluation

    enrollment_override: if provided, use this dict instead of fetching from DB.
                         Used for per-student validation loops.

    Special scope:
      scope="any_course" — rule PASSes if the condition is true for AT LEAST ONE
                           course in the database (not necessarily the submitted one).
    """
    catalog    = get_course_catalog(course_id)
    enrollment = enrollment_override if enrollment_override else get_enrollment(course_id)
    # Look up exam eligibility per-student using roll_no if available
    roll_no    = enrollment.get("roll_no") if enrollment else None
    exam       = get_exam_eligibility(course_id, roll_no=roll_no)
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



# ─── Natural Language Query Parser ───────────────────────────────────────────

def parse_natural_query(query_text: str, llm) -> dict:
    """
    Parse a plain English query into a structured validation request.

    The user can type anything like:
      - "validate Python Basics"
      - "check all courses"
      - "validate all rules for Charlie"
      - "show me C001 and C002"
      - "check only R1 for Data Science"
      - "check all rules for roll no 2024ML004"

    Returns:
        {
          "course_ids":   ["C003"],   # course IDs to validate
          "rule_ids":     ["R1"],     # "all" or list like ["R1", "R2"]
          "student_name": "Leo",      # "all", or a specific name like "Charlie"
          "roll_no":      "2024ML004" # None, or the exact roll_no if mentioned
        }
    """
    import re
    from db import get_all_course_ids, get_course_catalog, get_all_enrollments_for_course

    all_ids = get_all_course_ids()
    all_roll_nos = get_all_roll_nos()   # e.g. ["2024CS001", ..., "2024ML004"]
    detected_roll_no = None

    # Pattern: 4-digit year + 2-letter dept code + 3-digit seq (e.g. 2024ML004)
    roll_pattern = re.compile(r'\b(\d{4}[A-Za-z]{2}\d{3})\b')
    roll_match   = roll_pattern.search(query_text)

    if roll_match:
        candidate = roll_match.group(1).upper()
        matched_roll = next(
            (r for r in all_roll_nos if r.upper() == candidate), None
        )
        if matched_roll:
            detected_roll_no = matched_roll

    # ── Fast-path: detect rule IDs in query before calling the LLM ────────────
    # Matches: "rule 1", "R1", "rule R1", "rules 1 and 2", "R1,R2", etc.
    rulebook_path = os.path.join(os.path.dirname(__file__), "rulebook.json")
    with open(rulebook_path) as f:
        all_rules_data = json.load(f)["rules"]
    rule_ids_available = [r["rule_id"] for r in all_rules_data]

    # Build a pattern that matches "R1", "R 1", "rule 1", "rule R1", etc.
    # It captures the numeric part and we map it to R<N>
    rule_pattern = re.compile(
        r'\b(?:rules?\s*)?[Rr]\s*(\d+)\b'      # "R1", "R 1", "rule R1"
        r'|\b(?:rules?\s+)(\d+)\b',             # "rule 1", "rules 2"
        re.IGNORECASE
    )
    rule_matches = rule_pattern.findall(query_text)
    detected_rule_ids = None
    if rule_matches:
        nums = [m[0] or m[1] for m in rule_matches if (m[0] or m[1])]
        candidates = [f"R{n}" for n in nums]
        # Keep only valid rule IDs that exist in the rulebook
        valid_detected = [c for c in candidates if c in rule_ids_available]
        if valid_detected:
            detected_rule_ids = valid_detected

    # ── If roll_no was found, return immediately (no LLM needed) ─────────────
    if detected_roll_no:
        enroll = get_enrollment_by_roll_no(detected_roll_no)
        if enroll:
            return {
                "course_ids":   [enroll["course_id"]],
                "rule_ids":     detected_rule_ids if detected_rule_ids else "all",
                "student_name": enroll["student_name"],
                "roll_no":      detected_roll_no,
            }
    # ──────────────────────────────────────────────────────────────────────────

    courses_context = []
    all_students = set()
    for cid in all_ids:
        c = get_course_catalog(cid)
        if c:
            courses_context.append(f"{cid}: {c['course_name']} (fee={c['fee']})")
        for e in get_all_enrollments_for_course(cid):
            all_students.add(e["student_name"])

    prompt = f"""You are a university validation system assistant.

Available courses:
{chr(10).join(courses_context)}

All enrolled students: {sorted(all_students)}
All roll numbers: {all_roll_nos}

Available rule IDs: {rule_ids_available}

User query: "{query_text}"

Extract:
- course_ids: which courses to validate. Use ALL course IDs if user says "all", "every", or doesn't specify a course.
- rule_ids: "all" unless the user mentions specific rules. The user may write "rule 1", "R1", "rule R1" — all mean the same R1. Return a list like ["R1"] for specific rules, or "all" for all.
- student_name: the exact student name if mentioned, otherwise "all".
- roll_no: the roll number if mentioned (e.g. "2024ML004"), otherwise null.

Your response MUST be a single, valid JSON object block with no markdown block around it and no python code or explanation. Do not wrap it in code. Output ONLY JSON.

Few-shot examples:
Query: "validate Python Basics"
Response:
{{
  "course_ids": ["C001"],
  "rule_ids": "all",
  "student_name": "all",
  "roll_no": null
}}

Query: "validate all rules for Charlie"
Response:
{{
  "course_ids": ["C001", "C002", "C003"],
  "rule_ids": "all",
  "student_name": "Charlie",
  "roll_no": null
}}

Query: "check rule 1 for Charlie"
Response:
{{
  "course_ids": ["C001", "C002", "C003"],
  "rule_ids": ["R1"],
  "student_name": "Charlie",
  "roll_no": null
}}

Query: "run only R2 and R3 for Data Science"
Response:
{{
  "course_ids": ["C002"],
  "rule_ids": ["R2", "R3"],
  "student_name": "all",
  "roll_no": null
}}

Query: "check all rules for roll no 2024ML004"
Response:
{{
  "course_ids": ["C003"],
  "rule_ids": "all",
  "student_name": "Leo",
  "roll_no": "2024ML004"
}}

Query: "{query_text}"
Response:"""

    try:
        resp = llm.invoke(prompt)
        content = str(resp.content).strip()

        # Robust JSON extraction: find the outermost { ... }
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            json_str = content[start_idx:end_idx+1]
        else:
            json_str = content

        parsed = json.loads(json_str)

        valid_ids = set(all_ids)
        course_ids = parsed.get("course_ids", all_ids)
        if isinstance(course_ids, str) and course_ids.upper() == "ALL":
            course_ids = all_ids
        else:
            course_ids = [c for c in course_ids if c in valid_ids]
            if not course_ids:
                course_ids = all_ids

        # Validate student_name
        student_name = parsed.get("student_name", "all")
        if isinstance(student_name, str) and student_name.lower() != "all":
            matched = next((s for s in all_students if s.lower() == student_name.lower()), None)
            student_name = matched if matched else "all"

        # Validate roll_no
        roll_no = parsed.get("roll_no") or detected_roll_no
        if roll_no and roll_no not in all_roll_nos:
            roll_no = None

        # If regex detected specific rule IDs, they override the LLM output
        # (the LLM may have missed "rule 1" → R1 even with examples)
        llm_rule_ids = parsed.get("rule_ids", "all")
        final_rule_ids = detected_rule_ids if detected_rule_ids else llm_rule_ids

        return {
            "course_ids":   course_ids,
            "rule_ids":     final_rule_ids,
            "student_name": student_name,
            "roll_no":      roll_no,
        }

    except Exception:
        return {"course_ids": all_ids, "rule_ids": "all", "student_name": "all", "roll_no": None}


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_validation_agent(
    course_id: str,
    rule_ids: list = None,
    student_name: str = "all",
    roll_no: str = None
) -> dict:
    """
    Run validation for a course, per student.

    Parameters:
        course_id    : e.g. "C001"
        rule_ids     : optional filter e.g. ["R1", "R2"], or None for all
        student_name : "all" (validate every student) or a specific name like "Charlie"
        roll_no      : if provided, filter by this exact roll number (takes priority
                       over student_name when both match different students)

    Returns a dict:
        {
          "per_student": [
            { "student_name": "Alice",  "enrollment": {...}, "results": [...] },
            { "student_name": "Bob",    "enrollment": {...}, "results": [...] },
          ],
          "sql_results":  [ {rule_id, status, ...}, ... ],  # SQL rules run once per course
        }
    """
    rulebook_path = os.path.join(os.path.dirname(__file__), "rulebook.json")
    with open(rulebook_path) as f:
        all_rules = json.load(f)["rules"]

    if rule_ids:
        all_rules = [r for r in all_rules if r["rule_id"] in rule_ids]

    # Split rules into per-student (arithmetic/english) and per-course (sql)
    student_rules = [r for r in all_rules if r.get("type") != "sql"]
    sql_rules     = [r for r in all_rules if r.get("type") == "sql"]

    llm   = _get_llm()
    exam  = get_exam_eligibility(course_id)   # course-level fallback for SQL rules
    catalog = get_course_catalog(course_id)

    # Fetch students to validate
    all_enrollments = get_all_enrollments_for_course(course_id)

    # roll_no filter takes priority — it is a unique key, so at most one match
    if roll_no:
        all_enrollments = [e for e in all_enrollments if e["roll_no"] == roll_no]
        if not all_enrollments:
            print(f"  ℹ  roll_no '{roll_no}' is not in course {course_id} — skipping.")
            return {"per_student": [], "sql_results": []}
    elif student_name != "all":
        all_enrollments = [e for e in all_enrollments
                           if e["student_name"].lower() == student_name.lower()]
        if not all_enrollments:
            # Student is not enrolled in this course — skip it entirely
            print(f"  ℹ  '{student_name}' is not enrolled in {course_id} — skipping.")
            return {"per_student": [], "sql_results": []}

    print(f"\n{'='*55}")
    print(f"  Validation Agent — Course: {course_id}")
    print(f"  Students: {[e['student_name'] for e in all_enrollments]}")
    print(f"  Rules: {[r['rule_id'] for r in all_rules]}")
    print(f"{'='*55}\n")

    # ── Evaluate per-student rules for each student ───────────────────────────
    per_student = []
    for enrollment in all_enrollments:
        sname = enrollment["student_name"]
        student_results = []
        for i, rule in enumerate(student_rules):
            result = evaluate_single_rule(course_id, rule, llm,
                                          enrollment_override=enrollment)
            icon = "✅" if result["status"] == "PASS" else "❌"
            print(f"   [{sname}] {rule['rule_id']}: {icon} {result['status']}")
            student_results.append(result)
            if i < len(student_rules) - 1:
                time.sleep(2)

        per_student.append({
            "student_name": sname,
            "enrollment":   enrollment,
            "results":      student_results,
        })

    # ── Evaluate SQL (aggregate) rules once per course ────────────────────────
    sql_results = []
    for i, rule in enumerate(sql_rules):
        result = evaluate_single_rule(course_id, rule, llm)
        icon = "✅" if result["status"] == "PASS" else "❌"
        print(f"   [Course-wide SQL] {rule['rule_id']}: {icon} {result['status']}")
        sql_results.append(result)
        if i < len(sql_rules) - 1:
            time.sleep(2)

    total_results = [r for s in per_student for r in s["results"]] + sql_results
    passed = sum(1 for r in total_results if r["status"] == "PASS")
    print(f"\n{'='*55}")
    print(f"  Result: {passed}/{len(total_results)} rule checks passed for {course_id}")
    print(f"{'='*55}\n")

    return {"per_student": per_student, "sql_results": sql_results}
