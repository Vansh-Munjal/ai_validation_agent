"""
tools.py — LangChain Tools for Oracle Data Fetching (Phase 3)
--------------------------------------------------------------
These are the 3 tools that the Validation Agent can call.
Each tool connects to one Oracle schema and returns data as a string.

Tool naming convention matches the schema they connect to:
    fetch_catalog_data    → CATALOG_USER.COURSE_CATALOG
    fetch_enrollment_data → ENROLL_USER.ENROLLMENT
    fetch_exam_data       → EXAM_USER.EXAM_ELIGIBILITY

The agent reads the rule from rulebook.json, decides which tools it
needs to call, fetches the data, and reasons about whether the
rule condition is satisfied.
"""

from langchain_core.tools import tool
from db import get_course_catalog, get_enrollment, get_exam_eligibility


@tool
def fetch_catalog_data(course_id: str) -> str:
    """
    Fetch course information from the CATALOG_USER schema in Oracle database.

    This tool connects to CATALOG_USER.COURSE_CATALOG and retrieves:
    - course_id   : unique identifier for the course
    - course_name : full name of the course
    - fee         : official course fee as per the catalog system

    Use this tool when a rule involves 'catalog.fee' or 'catalog.course_name'.

    Args:
        course_id: The course identifier (e.g., 'C001', 'C002')

    Returns:
        A string representation of the course data dict, or an error message.
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

    This tool connects to ENROLL_USER.ENROLLMENT and retrieves:
    - enrollment_id : unique enrollment record ID
    - course_id     : course this enrollment belongs to
    - student_name  : name of the enrolled student
    - fee           : fee recorded in the enrollment system

    Use this tool when a rule involves 'enrollment.fee' or 'enrollment.student_name'.
    Note: This is a SEPARATE system from the catalog — fees may differ!

    Args:
        course_id: The course identifier (e.g., 'C001', 'C002')

    Returns:
        A string representation of the enrollment data dict, or an error message.
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
    Fetch exam eligibility information from the EXAM_USER schema in Oracle database.

    This tool connects to EXAM_USER.EXAM_ELIGIBILITY and retrieves:
    - eligibility_id    : unique eligibility record ID
    - course_id         : course this record belongs to
    - is_eligible       : 'Y' if student is eligible, 'N' if not
    - min_attendance_pct: minimum attendance percentage recorded
    - fee_cleared       : 'Y' if exam fee is paid, 'N' if not paid

    Use this tool when a rule involves 'exam.fee_cleared', 'exam.is_eligible',
    or 'exam.min_attendance_pct'.

    Args:
        course_id: The course identifier (e.g., 'C001', 'C002')

    Returns:
        A string representation of the exam eligibility data, or an error message.
    """
    data = get_exam_eligibility(course_id)
    if data is None:
        return f"ERROR: No record found in EXAM_USER.EXAM_ELIGIBILITY for course_id='{course_id}'"
    return (
        f"EXAM_USER.EXAM_ELIGIBILITY data for {course_id}:\n"
        f"  eligibility_id    = {data['eligibility_id']}\n"
        f"  course_id         = {data['course_id']}\n"
        f"  is_eligible       = {data['is_eligible']}\n"
        f"  min_attendance_pct = {data['min_attendance_pct']}\n"
        f"  fee_cleared       = {data['fee_cleared']}"
    )


# List of all tools — imported by agent.py
ALL_TOOLS = [fetch_catalog_data, fetch_enrollment_data, fetch_exam_data]


# ─── Calculation Tools ────────────────────────────────────────────────────────
# These tools handle ALL arithmetic so the LLM never does math in its head.
# The LLM decides WHICH tool to call; Python does the actual computation.

@tool
def add(a: float, b: float) -> str:
    """Add two numbers together. Returns the sum.
    Use this for: fee addition, total calculations, adding allowances.
    Example: add(5000, 500) → 5500.0"""
    result = a + b
    return f"{a} + {b} = {result}"


@tool
def subtract(a: float, b: float) -> str:
    """Subtract b from a. Returns the difference.
    Use this for: fee differences, calculating discounts, finding gaps between values.
    Example: subtract(6000, 5000) → 1000.0"""
    result = a - b
    return f"{a} - {b} = {result}"


@tool
def multiply(a: float, b: float) -> str:
    """Multiply two numbers. Returns the product.
    Use this for: percentage multiplications, scaling fees, credit calculations.
    Example: multiply(5000, 1.10) → 5500.0"""
    result = a * b
    return f"{a} × {b} = {result}"


@tool
def divide(a: float, b: float) -> str:
    """Divide a by b. Returns the quotient.
    Use this for: per-unit calculations, ratio checks, averages.
    Example: divide(6000, 5000) → 1.2"""
    if b == 0:
        return "ERROR: Cannot divide by zero"
    result = round(a / b, 6)
    return f"{a} ÷ {b} = {result}"


@tool
def percentage(part: float, total: float) -> str:
    """Calculate what percentage 'part' is of 'total'. Returns the percentage.
    Use this for: attendance percentage checks, fee percentage validations.
    Example: percentage(75, 100) → 75.0%"""
    if total == 0:
        return "ERROR: Cannot calculate percentage with total=0"
    result = round((part / total) * 100, 4)
    return f"({part} / {total}) × 100 = {result}%"


@tool
def percentage_of(percent: float, value: float) -> str:
    """Calculate 'percent'% of 'value'. Returns the amount.
    Use this for: computing 10% of a fee, calculating expected amounts.
    Example: percentage_of(10, 5000) → 500.0 (10% of 5000)"""
    result = round((percent / 100) * value, 4)
    return f"{percent}% of {value} = {result}"


@tool
def modulo(a: float, b: float) -> str:
    """Calculate the remainder of a divided by b.
    Use this for: checking divisibility, rounding validations.
    Example: modulo(6000, 1000) → 0.0"""
    if b == 0:
        return "ERROR: Cannot use modulo with divisor=0"
    result = a % b
    return f"{a} mod {b} = {result}"


@tool
def compare_equal(a: float, b: float) -> str:
    """Check if two numbers are exactly equal. Returns TRUE or FALSE.
    Use this for: == conditions in rules. This is the ONLY reliable way to check equality.
    Example: compare_equal(6000, 5500) → FALSE (6000 ≠ 5500)
    Example: compare_equal(6000, 6000) → TRUE"""
    result = (a == b)
    return f"{a} == {b} → {'TRUE' if result else 'FALSE'}"


@tool
def compare_greater(a: float, b: float) -> str:
    """Check if a is strictly greater than b. Returns TRUE or FALSE.
    Use this for: > conditions in rules.
    Example: compare_greater(6000, 0) → TRUE"""
    result = (a > b)
    return f"{a} > {b} → {'TRUE' if result else 'FALSE'}"


@tool
def compare_less(a: float, b: float) -> str:
    """Check if a is strictly less than b. Returns TRUE or FALSE.
    Use this for: < conditions in rules.
    Example: compare_less(5000, 6000) → TRUE"""
    result = (a < b)
    return f"{a} < {b} → {'TRUE' if result else 'FALSE'}"


@tool
def compare_greater_equal(a: float, b: float) -> str:
    """Check if a is greater than or equal to b. Returns TRUE or FALSE.
    Use this for: >= conditions in rules.
    Example: compare_greater_equal(6000, 5000) → TRUE"""
    result = (a >= b)
    return f"{a} >= {b} → {'TRUE' if result else 'FALSE'}"


@tool
def compare_less_equal(a: float, b: float) -> str:
    """Check if a is less than or equal to b. Returns TRUE or FALSE.
    Use this for: <= conditions in rules.
    Example: compare_less_equal(5000, 6000) → TRUE"""
    result = (a <= b)
    return f"{a} <= {b} → {'TRUE' if result else 'FALSE'}"


# Updated list of ALL tools — Oracle data tools + math/comparison tools
ALL_TOOLS = [
    # Oracle data fetching
    fetch_catalog_data,
    fetch_enrollment_data,
    fetch_exam_data,
    # Arithmetic
    add,
    subtract,
    multiply,
    divide,
    percentage,
    percentage_of,
    modulo,
    # Comparison (always use these, never reason about equality in your head)
    compare_equal,
    compare_greater,
    compare_less,
    compare_greater_equal,
    compare_less_equal,
]
