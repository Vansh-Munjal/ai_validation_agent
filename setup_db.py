"""
setup_db.py — Multi-Schema Oracle setup script
-----------------------------------------------
Run this ONCE to:
  1. Create 3 Oracle users (CATALOG_USER, ENROLL_USER, EXAM_USER)
  2. Grant them the necessary Oracle privileges
  3. Create a table in each user's schema
  4. Insert sample data — with an intentional fee mismatch on C001

Usage:
    python setup_db.py

What the schema looks like after running this:
    CATALOG_USER
    └── COURSE_CATALOG   (course_id, course_name, fee)

    ENROLL_USER
    └── ENROLLMENT       (enrollment_id, course_id, student_name, fee)

    EXAM_USER
    └── EXAM_ELIGIBILITY (eligibility_id, course_id, is_eligible,
                          min_attendance_pct, fee_cleared)
"""

import oracledb
from config import (
    DB_USER, DB_PASSWORD, DB_DSN,          # SYSTEM / DBA (admin)
    CATALOG_DB_USER, CATALOG_DB_PASSWORD, CATALOG_DB_DSN,
    ENROLL_DB_USER,  ENROLL_DB_PASSWORD,  ENROLL_DB_DSN,
    EXAM_DB_USER,    EXAM_DB_PASSWORD,    EXAM_DB_DSN,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Step 1 — Connect as SYSTEM and create the three schema users
# ─────────────────────────────────────────────────────────────────────────────

def create_users():
    """
    Connect as SYSTEM (DBA) and create the three Oracle users.
    Each user is granted CONNECT + RESOURCE so they can log in
    and create tables within their own schema.
    UNLIMITED TABLESPACE lets them store data without quota issues.
    """
    print("\n── Step 1: Creating Oracle users (connecting as SYSTEM) ──")
    conn   = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)
    cursor = conn.cursor()

    users = [
        (CATALOG_DB_USER, CATALOG_DB_PASSWORD, "CATALOG_USER"),
        (ENROLL_DB_USER,  ENROLL_DB_PASSWORD,  "ENROLL_USER"),
        (EXAM_DB_USER,    EXAM_DB_PASSWORD,     "EXAM_USER"),
    ]

    for username, password, label in users:
        # CREATE USER
        try:
            cursor.execute(
                f"CREATE USER {username} IDENTIFIED BY \"{password}\""
            )
            print(f"  ✓ Created user: {username}")
        except oracledb.DatabaseError as e:
            code = e.args[0].code
            if code == 1920:   # ORA-01920: user name already exists
                print(f"  ℹ  User '{username}' already exists — skipping creation.")
            else:
                raise

        # GRANT privileges
        try:
            cursor.execute(f"GRANT CONNECT, RESOURCE TO {username}")
            cursor.execute(f"GRANT UNLIMITED TABLESPACE TO {username}")
            print(f"  ✓ Granted privileges to: {username}")
        except oracledb.DatabaseError as e:
            print(f"  ⚠  Could not grant to {username}: {e}")

    conn.commit()
    conn.close()
    print("  → All users ready.\n")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 2 — CATALOG_USER: create COURSE_CATALOG and insert data
# ─────────────────────────────────────────────────────────────────────────────

def setup_catalog_schema():
    """
    Connect as CATALOG_USER and create + populate COURSE_CATALOG.
    This table is the "source of truth" for course fees.
    """
    print("── Step 2: Setting up CATALOG_USER schema ──")
    conn   = oracledb.connect(
        user=CATALOG_DB_USER, password=CATALOG_DB_PASSWORD, dsn=CATALOG_DB_DSN
    )
    cursor = conn.cursor()

    # Create table (ignore ORA-00955 if already exists)
    try:
        cursor.execute("""
            CREATE TABLE course_catalog (
                course_id   VARCHAR2(10)  PRIMARY KEY,
                course_name VARCHAR2(100) NOT NULL,
                fee         NUMBER(10, 2) NOT NULL
            )
        """)
        print("  ✓ Created table: CATALOG_USER.COURSE_CATALOG")
    except oracledb.DatabaseError as e:
        if e.args[0].code == 955:   # ORA-00955: name already used
            print("  ℹ  Table COURSE_CATALOG already exists — skipping.")
        else:
            raise

    # Insert sample data
    # C001 fee = 5000 ← intentionally different from ENROLLMENT (6000) → FAIL
    # C002 fee = 8000 ← same in ENROLLMENT → PASS
    rows = [
        ("C001", "Python Basics",          5000),
        ("C002", "Data Science Advanced",  8000),
        ("C003", "Machine Learning Intro", 12000),
    ]
    for row in rows:
        try:
            cursor.execute(
                "INSERT INTO course_catalog (course_id, course_name, fee) VALUES (:1, :2, :3)",
                row
            )
        except oracledb.DatabaseError as e:
            if e.args[0].code == 1:   # ORA-00001: unique constraint violated
                print(f"  ℹ  Row {row[0]} already exists — skipping.")
            else:
                raise

    conn.commit()
    conn.close()
    print("  ✓ Sample data inserted into CATALOG_USER.COURSE_CATALOG")
    print("    C001 | Python Basics          | fee = 5000  ← intentional mismatch")
    print("    C002 | Data Science Advanced  | fee = 8000")
    print("    C003 | Machine Learning Intro | fee = 12000\n")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 3 — ENROLL_USER: create ENROLLMENT and insert data
# ─────────────────────────────────────────────────────────────────────────────

def setup_enroll_schema():
    """
    Connect as ENROLL_USER and create + populate ENROLLMENT.
    C001 enrollment fee is intentionally set to 6000 (≠ catalog's 5000)
    to trigger a FAIL on rule R1.
    """
    print("── Step 3: Setting up ENROLL_USER schema ──")
    conn   = oracledb.connect(
        user=ENROLL_DB_USER, password=ENROLL_DB_PASSWORD, dsn=ENROLL_DB_DSN
    )
    cursor = conn.cursor()

    # No FOREIGN KEY to course_catalog — these are independent systems!
    try:
        cursor.execute("""
            CREATE TABLE enrollment (
                enrollment_id NUMBER        PRIMARY KEY,
                course_id     VARCHAR2(10)  NOT NULL,
                student_name  VARCHAR2(100) NOT NULL,
                fee           NUMBER(10, 2) NOT NULL
            )
        """)
        print("  ✓ Created table: ENROLL_USER.ENROLLMENT")
    except oracledb.DatabaseError as e:
        if e.args[0].code == 955:
            print("  ℹ  Table ENROLLMENT already exists — skipping.")
        else:
            raise

    rows = [
        (1, "C001", "Alice",   6000),   # ← 6000 ≠ catalog's 5000 → FAIL R1
        (2, "C002", "Bob",     8000),   # ← 8000 == catalog's 8000 → PASS R1
        (3, "C003", "Charlie", 12000),  # ← matches
    ]
    for row in rows:
        try:
            cursor.execute(
                "INSERT INTO enrollment (enrollment_id, course_id, student_name, fee) "
                "VALUES (:1, :2, :3, :4)",
                row
            )
        except oracledb.DatabaseError as e:
            if e.args[0].code == 1:
                print(f"  ℹ  Row {row[0]} already exists — skipping.")
            else:
                raise

    conn.commit()
    conn.close()
    print("  ✓ Sample data inserted into ENROLL_USER.ENROLLMENT")
    print("    1 | C001 | Alice   | fee = 6000  ← MISMATCH with catalog (5000)")
    print("    2 | C002 | Bob     | fee = 8000")
    print("    3 | C003 | Charlie | fee = 12000\n")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 4 — EXAM_USER: create EXAM_ELIGIBILITY and insert data
# ─────────────────────────────────────────────────────────────────────────────

def setup_exam_schema():
    """
    Connect as EXAM_USER and create + populate EXAM_ELIGIBILITY.
    This table is not used by any rule yet — reserved for Phase 2.
    """
    print("── Step 4: Setting up EXAM_USER schema ──")
    conn   = oracledb.connect(
        user=EXAM_DB_USER, password=EXAM_DB_PASSWORD, dsn=EXAM_DB_DSN
    )
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE exam_eligibility (
                eligibility_id    NUMBER        PRIMARY KEY,
                course_id         VARCHAR2(10)  NOT NULL,
                is_eligible       CHAR(1)       DEFAULT 'Y',  -- Y or N
                min_attendance_pct NUMBER(5,2)  DEFAULT 75,
                fee_cleared       CHAR(1)       DEFAULT 'N'   -- Y or N
            )
        """)
        print("  ✓ Created table: EXAM_USER.EXAM_ELIGIBILITY")
    except oracledb.DatabaseError as e:
        if e.args[0].code == 955:
            print("  ℹ  Table EXAM_ELIGIBILITY already exists — skipping.")
        else:
            raise

    rows = [
        (1, "C001", "Y", 75, "N"),   # fee not cleared (matches enrollment mismatch)
        (2, "C002", "Y", 80, "Y"),   # all good
        (3, "C003", "N", 60, "N"),   # not eligible — attendance too low
    ]
    for row in rows:
        try:
            cursor.execute(
                "INSERT INTO exam_eligibility "
                "(eligibility_id, course_id, is_eligible, min_attendance_pct, fee_cleared) "
                "VALUES (:1, :2, :3, :4, :5)",
                row
            )
        except oracledb.DatabaseError as e:
            if e.args[0].code == 1:
                print(f"  ℹ  Row {row[0]} already exists — skipping.")
            else:
                raise

    conn.commit()
    conn.close()
    print("  ✓ Sample data inserted into EXAM_USER.EXAM_ELIGIBILITY")
    print("    1 | C001 | eligible=Y | attendance=75% | fee_cleared=N")
    print("    2 | C002 | eligible=Y | attendance=80% | fee_cleared=Y")
    print("    3 | C003 | eligible=N | attendance=60% | fee_cleared=N\n")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AI Validation Agent — Multi-Schema DB Setup")
    print("=" * 55)

    create_users()          # Step 1: SYSTEM creates 3 users
    setup_catalog_schema()  # Step 2: CATALOG_USER creates its table
    setup_enroll_schema()   # Step 3: ENROLL_USER creates its table
    setup_exam_schema()     # Step 4: EXAM_USER creates its table

    print("=" * 55)
    print("✅ All schemas ready!")
    print()
    print("Expected validation results:")
    print("  C001 → ❌ FAIL  (catalog fee 5000 ≠ enrollment fee 6000)")
    print("  C002 → ✅ PASS  (both fees = 8000)")
    print("  C003 → ✅ PASS  (both fees = 12000)")
    print("=" * 55)
