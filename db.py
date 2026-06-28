"""
db.py — Database connection and query layer (Multi-Schema Edition)
------------------------------------------------------------------
Architecture change: instead of one schema, we now have THREE independent
Oracle schemas, each owned by a separate database user:

    CATALOG_USER  →  COURSE_CATALOG   (catalog system)
    ENROLL_USER   →  ENROLLMENT       (enrollment system)
    EXAM_USER     →  EXAM_ELIGIBILITY (exam system)

Each function in this file connects to the correct schema using
that schema's own credentials. This simulates three completely
independent university sub-systems.

The rest of the project (validator.py, app.py, rulebook.json)
is UNCHANGED — only this file knows about the multi-schema setup.
"""

import oracledb
from config import (
    DB_USER, DB_PASSWORD, DB_DSN,              # SYSTEM / admin (for SQL rules)
    CATALOG_DB_USER, CATALOG_DB_PASSWORD, CATALOG_DB_DSN,
    ENROLL_DB_USER,  ENROLL_DB_PASSWORD,  ENROLL_DB_DSN,
    EXAM_DB_USER,    EXAM_DB_PASSWORD,    EXAM_DB_DSN,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Connection helpers — one per schema
# ─────────────────────────────────────────────────────────────────────────────

def get_catalog_connection():
    """
    Open a connection to Oracle as CATALOG_USER.
    This user owns the COURSE_CATALOG table.
    """
    return oracledb.connect(
        user=CATALOG_DB_USER,
        password=CATALOG_DB_PASSWORD,
        dsn=CATALOG_DB_DSN
    )


def get_enroll_connection():
    """
    Open a connection to Oracle as ENROLL_USER.
    This user owns the ENROLLMENT table.
    """
    return oracledb.connect(
        user=ENROLL_DB_USER,
        password=ENROLL_DB_PASSWORD,
        dsn=ENROLL_DB_DSN
    )


def get_exam_connection():
    """
    Open a connection to Oracle as EXAM_USER.
    This user owns the EXAM_ELIGIBILITY table.
    """
    return oracledb.connect(
        user=EXAM_DB_USER,
        password=EXAM_DB_PASSWORD,
        dsn=EXAM_DB_DSN
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Query functions — each talks to its own schema
# ─────────────────────────────────────────────────────────────────────────────

def get_all_course_ids():
    """
    Fetch all course IDs from CATALOG_USER.COURSE_CATALOG.

    Used by 'any_course' scoped rules to iterate over every course
    and check whether the rule passes for at least one of them.

    Returns a list of course_id strings, e.g. ["C001", "C002", "C003"].
    """
    conn = get_catalog_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT course_id FROM course_catalog ORDER BY course_id")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def get_course_catalog(course_id):
    """
    Fetch course record from CATALOG_USER.COURSE_CATALOG.

    Connects as CATALOG_USER — that user owns this table, so no
    cross-schema grant is needed; it queries its own table directly.

    Returns a dict like:
        { "course_id": "C001", "course_name": "Python Basics", "fee": 5000 }
    Returns None if not found.
    """
    conn = get_catalog_connection()   # ← connects as CATALOG_USER
    try:
        cursor = conn.cursor()
        cursor.execute(
            # No schema prefix needed — CATALOG_USER owns this table
            "SELECT course_id, course_name, fee FROM course_catalog WHERE course_id = :cid",
            cid=course_id
        )
        row = cursor.fetchone()
        if row:
            return {
                "course_id":   row[0],
                "course_name": row[1],
                "fee":         row[2]
            }
        return None
    finally:
        conn.close()


def get_enrollment(course_id):
    """
    Fetch the FIRST enrollment record for a course from ENROLL_USER.ENROLLMENT.

    Connects as ENROLL_USER — completely separate Oracle session.
    Simulates a different system.

    Returns a dict like:
        { "enrollment_id": 1, "roll_no": "2024CS001", "course_id": "C001",
          "student_name": "Alice", "fee": 6700 }
    Returns None if not found.
    """
    conn = get_enroll_connection()    # ← connects as ENROLL_USER
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT enrollment_id, roll_no, course_id, student_name, fee "
            "FROM enrollment WHERE course_id = :cid ORDER BY enrollment_id",
            cid=course_id
        )
        row = cursor.fetchone()
        if row:
            return {
                "enrollment_id": row[0],
                "roll_no":       row[1],
                "course_id":     row[2],
                "student_name":  row[3],
                "fee":           row[4]
            }
        return None
    finally:
        conn.close()


def get_all_enrollments_for_course(course_id: str) -> list:
    """
    Fetch ALL enrolled students for a course from ENROLL_USER.ENROLLMENT.

    Unlike get_enrollment() which returns only the first row (fetchone),
    this returns every student enrolled in the course.

    Returns a list of dicts:
        [
          {"enrollment_id": 1, "roll_no": "2024CS001", "course_id": "C001",
           "student_name": "Alice", "fee": 6700},
          ...
        ]
    Returns [] if no rows found.
    """
    conn = get_enroll_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT enrollment_id, roll_no, course_id, student_name, fee "
            "FROM enrollment WHERE course_id = :cid ORDER BY enrollment_id",
            cid=course_id
        )
        return [
            {
                "enrollment_id": r[0],
                "roll_no":       r[1],
                "course_id":     r[2],
                "student_name":  r[3],
                "fee":           r[4]
            }
            for r in cursor.fetchall()
        ]
    finally:
        conn.close()


def get_enrollment_by_roll_no(roll_no: str) -> dict:
    """
    Fetch a single enrollment record by roll_no from ENROLL_USER.ENROLLMENT.

    Used when the user queries by roll number (e.g. '2024ML004') so we can
    pinpoint the exact student regardless of name ambiguity.

    Returns a dict like:
        { "enrollment_id": 12, "roll_no": "2024ML004", "course_id": "C003",
          "student_name": "Leo", "fee": 13500 }
    Returns None if not found.
    """
    conn = get_enroll_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT enrollment_id, roll_no, course_id, student_name, fee "
            "FROM enrollment WHERE roll_no = :rno",
            rno=roll_no
        )
        row = cursor.fetchone()
        if row:
            return {
                "enrollment_id": row[0],
                "roll_no":       row[1],
                "course_id":     row[2],
                "student_name":  row[3],
                "fee":           row[4]
            }
        return None
    finally:
        conn.close()


def get_all_roll_nos() -> list:
    """
    Fetch all roll numbers across all enrollments.
    Used by parse_natural_query to recognise roll numbers in user queries.

    Returns a list like ["2024CS001", "2024CS002", ..., "2024ML004"].
    """
    conn = get_enroll_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT roll_no FROM enrollment ORDER BY roll_no")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def get_exam_eligibility(course_id, roll_no=None):
    """
    Fetch exam eligibility from EXAM_USER.EXAM_ELIGIBILITY.

    Now supports per-student lookup via roll_no (preferred), or falls back
    to returning the first row for the course when roll_no is not provided.

    Returns a dict like:
        { "eligibility_id": 1, "roll_no": "2024CS001", "course_id": "C001",
          "is_eligible": "Y", "min_attendance_pct": 80, "fee_cleared": "Y" }
    Returns None if not found.
    """
    conn = get_exam_connection()      # ← connects as EXAM_USER
    try:
        cursor = conn.cursor()
        if roll_no:
            cursor.execute(
                "SELECT eligibility_id, roll_no, course_id, is_eligible, "
                "min_attendance_pct, fee_cleared "
                "FROM exam_eligibility WHERE roll_no = :rno",
                rno=roll_no
            )
        else:
            cursor.execute(
                "SELECT eligibility_id, roll_no, course_id, is_eligible, "
                "min_attendance_pct, fee_cleared "
                "FROM exam_eligibility WHERE course_id = :cid "
                "ORDER BY eligibility_id",
                cid=course_id
            )
        row = cursor.fetchone()
        if row:
            return {
                "eligibility_id":    row[0],
                "roll_no":           row[1],
                "course_id":         row[2],
                "is_eligible":       row[3],
                "min_attendance_pct": row[4],
                "fee_cleared":       row[5]
            }
        return None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Admin connection — for SQL-type rules requiring cross-schema access
# ─────────────────────────────────────────────────────────────────────────────

def get_admin_connection():
    """
    Open a connection to Oracle as SYSTEM (admin).

    Used exclusively by SQL-type validation rules that need:
      - Aggregate functions: COUNT, SUM, AVG, MAX, MIN
      - DISTINCT selections across the full table
      - GROUP BY / HAVING
      - Window functions: RANK(), ROW_NUMBER(), DENSE_RANK(), LEAD(), LAG()
      - Cross-schema joins (CATALOG_USER.COURSE_CATALOG JOIN ENROLL_USER.ENROLLMENT)

    Full schema-qualified table names:
      CATALOG_USER.COURSE_CATALOG      → course_id, course_name, fee
      ENROLL_USER.ENROLLMENT           → enrollment_id, course_id, student_name, fee
      EXAM_USER.EXAM_ELIGIBILITY       → eligibility_id, course_id, is_eligible,
                                         min_attendance_pct, fee_cleared
    """
    return oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=DB_DSN
    )


def execute_sql_rule(sql: str, course_id: str = None) -> dict:
    """
    Execute a SQL validation rule against Oracle using the admin connection.

    Supports ALL Oracle SQL features:
      - Aggregates  : COUNT, SUM, AVG, MAX, MIN, COUNT(DISTINCT ...)
      - Set ops     : DISTINCT, UNION, INTERSECT, MINUS
      - Grouping    : GROUP BY, HAVING
      - Window fns  : RANK() OVER (...), ROW_NUMBER() OVER (...),
                      DENSE_RANK(), LEAD(), LAG(), NTILE()
      - Cross-schema: JOIN across CATALOG_USER, ENROLL_USER, EXAM_USER

    SQL CONTRACT — the query MUST return exactly one row with one numeric column:
        1  →  rule PASSES
        0  →  rule FAILS

    Use :course_id as a bind variable when filtering by the current course (optional).

    Example (count distinct students per course must equal 1):
        SELECT CASE WHEN COUNT(DISTINCT student_name) = 1 THEN 1 ELSE 0 END
        FROM ENROLL_USER.ENROLLMENT
        WHERE course_id = :course_id

    Returns:
        {
          "passed":       bool,
          "result_value": int | None,  # raw value returned by SQL
          "sql":          str          # the executed SQL (for debugging)
        }
    """
    conn = get_admin_connection()
    try:
        cursor = conn.cursor()
        params = {}
        if course_id and ":course_id" in sql:
            params["course_id"] = course_id

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        if not rows:
            # No rows returned → treat as PASS (no violations found)
            return {"passed": True, "result_value": None, "sql": sql}

        result_value = rows[0][0]

        # 1 → PASS, anything else (0, None, negative) → FAIL
        try:
            passed = int(result_value) == 1
        except (TypeError, ValueError):
            passed = False

        return {"passed": passed, "result_value": result_value, "sql": sql}

    finally:
        conn.close()
