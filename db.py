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
    Fetch enrollment record from ENROLL_USER.ENROLLMENT.

    Connects as ENROLL_USER — completely separate Oracle session
    from the catalog query above. Simulates a different system.

    Returns a dict like:
        { "enrollment_id": 1, "course_id": "C001", "fee": 6000 }
    Returns None if not found.
    """
    conn = get_enroll_connection()    # ← connects as ENROLL_USER
    try:
        cursor = conn.cursor()
        cursor.execute(
            # No schema prefix needed — ENROLL_USER owns this table
            "SELECT enrollment_id, course_id, student_name, fee FROM enrollment WHERE course_id = :cid",
            cid=course_id
        )
        row = cursor.fetchone()
        if row:
            return {
                "enrollment_id": row[0],
                "course_id":     row[1],
                "student_name":  row[2],
                "fee":           row[3]
            }
        return None
    finally:
        conn.close()


def get_exam_eligibility(course_id):
    """
    Fetch exam eligibility record from EXAM_USER.EXAM_ELIGIBILITY.

    Connects as EXAM_USER — third independent system.
    Reserved for future validation rules (Phase 2+).

    Returns a dict like:
        { "eligibility_id": 1, "course_id": "C001", "is_eligible": "Y",
          "min_attendance_pct": 75, "fee_cleared": "N" }
    Returns None if not found.
    """
    conn = get_exam_connection()      # ← connects as EXAM_USER
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT eligibility_id, course_id, is_eligible, min_attendance_pct, fee_cleared "
            "FROM exam_eligibility WHERE course_id = :cid",
            cid=course_id
        )
        row = cursor.fetchone()
        if row:
            return {
                "eligibility_id":    row[0],
                "course_id":         row[1],
                "is_eligible":       row[2],
                "min_attendance_pct": row[3],
                "fee_cleared":       row[4]
            }
        return None
    finally:
        conn.close()
