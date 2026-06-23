"""
tools.py — Oracle fetch tools + python_eval engine
---------------------------------------------------
Fetch tools: LangChain tools for Oracle schema data (optional LLM use).
python_eval: deterministic rule evaluation — all arithmetic/comparisons in Python.
"""

from langchain_core.tools import tool
from db import get_course_catalog, get_enrollment, get_exam_eligibility


# ─── python_eval engine ───────────────────────────────────────────────────────

class _Namespace:
    """Wraps a dict so rule conditions can use catalog.fee dot notation."""

    def __init__(self, data: dict):
        for k, v in (data or {}).items():
            setattr(self, k, v)

    def __repr__(self):
        return str(self.__dict__)


def _eval_context(catalog: dict, enrollment: dict, exam: dict) -> dict:
    return {
        "catalog":      _Namespace(catalog    or {}),
        "enrollment":   _Namespace(enrollment or {}),
        "exam":         _Namespace(exam       or {}),
        "__builtins__": {},
    }


def python_eval(condition: str, catalog: dict, enrollment: dict, exam: dict) -> bool:
    """
    Evaluate a rule condition string against fetched Oracle data.

    Handles all arithmetic and comparisons deterministically:
        catalog.fee, enrollment.fee, exam.fee_cleared, etc.

    Returns True if the condition passes, False otherwise.
    Raises SyntaxError, NameError, TypeError, or AttributeError on bad conditions.
    """
    ctx = _eval_context(catalog, enrollment, exam)
    return bool(eval(condition, ctx))  # noqa: S307 — sandboxed namespace


def compute_rule_comparison(
    condition: str, catalog: dict, enrollment: dict, exam: dict
) -> dict:
    """
    For a failed rule, evaluate both sides of the condition for UI display.
    Returns: {lhs_expr, lhs_val, operator, rhs_expr, rhs_val} or {}.
    """
    ctx = _eval_context(catalog, enrollment, exam)
    for op in ["==", ">=", "<=", "!=", ">", "<"]:
        if op in condition:
            lhs_str, rhs_str = condition.split(op, 1)
            lhs_str, rhs_str = lhs_str.strip(), rhs_str.strip()
            try:
                return {
                    "lhs_expr": lhs_str,
                    "lhs_val":  eval(lhs_str, ctx),  # noqa: S307
                    "operator": op,
                    "rhs_expr": rhs_str,
                    "rhs_val":  eval(rhs_str, ctx),  # noqa: S307
                }
            except Exception:
                pass
    return {}


# ─── Oracle fetch tools ───────────────────────────────────────────────────────

@tool
def fetch_catalog_data(course_id: str) -> str:
    """
    Fetch course information from the CATALOG_USER schema in Oracle database.

    Retrieves: course_id, course_name, fee (official catalog fee).
    Use when a rule involves catalog.fee or catalog.course_name.
    """
    data = get_course_catalog(course_id)
    if data is None:
        return f"ERROR: No record found in CATALOG_USER.COURSE_CATALOG for course_id='{course_id}'"
    return (
        f"CATALOG_USER.COURSE_CATALOG data for {course_id}:\n"
        f"  course_id   = {data['course_id']}\n"
        f"  course_name = {data['course_name']}\n"
        f"  fee         = {data['fee']}"
    )


@tool
def fetch_enrollment_data(course_id: str) -> str:
    """
    Fetch enrollment information from the ENROLL_USER schema in Oracle database.

    Retrieves: enrollment_id, course_id, student_name, fee (enrollment system fee).
    Use when a rule involves enrollment.fee or enrollment.student_name.
    """
    data = get_enrollment(course_id)
    if data is None:
        return f"ERROR: No record found in ENROLL_USER.ENROLLMENT for course_id='{course_id}'"
    return (
        f"ENROLL_USER.ENROLLMENT data for {course_id}:\n"
        f"  enrollment_id = {data['enrollment_id']}\n"
        f"  course_id     = {data['course_id']}\n"
        f"  student_name  = {data['student_name']}\n"
        f"  fee           = {data['fee']}"
    )


@tool
def fetch_exam_data(course_id: str) -> str:
    """
    Fetch exam eligibility from the EXAM_USER schema in Oracle database.

    Retrieves: is_eligible, min_attendance_pct, fee_cleared.
    Use when a rule involves exam.fee_cleared, exam.is_eligible, etc.
    """
    data = get_exam_eligibility(course_id)
    if data is None:
        return f"ERROR: No record found in EXAM_USER.EXAM_ELIGIBILITY for course_id='{course_id}'"
    return (
        f"EXAM_USER.EXAM_ELIGIBILITY data for {course_id}:\n"
        f"  eligibility_id     = {data['eligibility_id']}\n"
        f"  course_id          = {data['course_id']}\n"
        f"  is_eligible        = {data['is_eligible']}\n"
        f"  min_attendance_pct = {data['min_attendance_pct']}\n"
        f"  fee_cleared        = {data['fee_cleared']}"
    )


FETCH_TOOLS = [fetch_catalog_data, fetch_enrollment_data, fetch_exam_data]
ALL_TOOLS = FETCH_TOOLS
