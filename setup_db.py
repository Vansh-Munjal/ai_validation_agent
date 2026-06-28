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
    └── ENROLLMENT       (enrollment_id, roll_no, course_id, student_name, fee)
                          ^ roll_no added to uniquely identify students

    EXAM_USER
    └── EXAM_ELIGIBILITY (eligibility_id, roll_no, course_id,
                          is_eligible, min_attendance_pct, fee_cleared)
                          ^ now per-student (12 rows instead of 3)
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

    rows = [
        ("C001", "Python Basics",          5000),
        ("C002", "Data Science Advanced",  8000),
        ("C003", "Machine Learning Intro", 12000),
    ]
    cursor.execute("DELETE FROM course_catalog")

    for row in rows:
        cursor.execute(
            "INSERT INTO course_catalog (course_id, course_name, fee) VALUES (:1, :2, :3)",
            row
        )

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

    NEW: roll_no column added as a unique student identifier.
         Format: YYYY<DEPT><SEQ> e.g. 2024CS001
         This prevents ambiguity when two students share the same name.

    C001 enrollment fee is intentionally different from catalog fee
    to trigger FAILs on rule R1 for some students.
    """
    print("── Step 3: Setting up ENROLL_USER schema ──")
    conn   = oracledb.connect(
        user=ENROLL_DB_USER, password=ENROLL_DB_PASSWORD, dsn=ENROLL_DB_DSN
    )
    cursor = conn.cursor()

    # Drop old table to add roll_no column cleanly
    try:
        cursor.execute("DROP TABLE enrollment")
        print("  ℹ  Dropped old ENROLLMENT table to apply schema changes (adding roll_no).")
    except oracledb.DatabaseError as e:
        if e.args[0].code == 942:   # ORA-00942: table or view does not exist
            pass
        else:
            raise

    # No FOREIGN KEY to course_catalog — these are independent systems!
    cursor.execute("""
        CREATE TABLE enrollment (
            enrollment_id NUMBER        PRIMARY KEY,
            roll_no       VARCHAR2(20)  UNIQUE NOT NULL,
            course_id     VARCHAR2(10)  NOT NULL,
            student_name  VARCHAR2(100) NOT NULL,
            fee           NUMBER(10, 2) NOT NULL
        )
    """)
    print("  ✓ Created table: ENROLL_USER.ENROLLMENT (with roll_no)")

    rows = [
        # (enrollment_id, roll_no,       course_id, student_name, fee)
        # ── C001: Python Basics (catalog.fee = 5000) ──────────────────────────
        # Fee diff 1700 = PASS R1  |  Fee diff != 1700 = FAIL R1
        (1,  "2024CS001", "C001", "Alice",   6700),  # 6700-5000=1700 → PASS R1
        (2,  "2024CS002", "C001", "Bob",     6500),  # 6500-5000=1500 → FAIL R1
        (3,  "2024CS003", "C001", "Charlie", 6700),  # 6700-5000=1700 → PASS R1
        (4,  "2024CS004", "C001", "Diana",   6000),  # 6000-5000=1000 → FAIL R1

        # ── C002: Data Science Advanced (catalog.fee = 8000) ──────────────────
        (5,  "2024DS001", "C002", "Eve",     9700),  # 9700-8000=1700 → PASS R1
        (6,  "2024DS002", "C002", "Frank",   8000),  # 8000-8000=0    → FAIL R1
        (7,  "2024DS003", "C002", "Grace",   9700),  # 9700-8000=1700 → PASS R1
        (8,  "2024DS004", "C002", "Henry",   9500),  # 9500-8000=1500 → FAIL R1

        # ── C003: Machine Learning Intro (catalog.fee = 12000) ────────────────
        (9,  "2024ML001", "C003", "Ivy",     13700), # 13700-12000=1700 → PASS R1
        (10, "2024ML002", "C003", "Jack",    13700), # 13700-12000=1700 → PASS R1
        (11, "2024ML003", "C003", "Kate",    12000), # 12000-12000=0    → FAIL R1
        (12, "2024ML004", "C003", "Leo",     13500), # 13500-12000=1500 → FAIL R1
    ]

    for row in rows:
        cursor.execute(
            "INSERT INTO enrollment (enrollment_id, roll_no, course_id, student_name, fee) "
            "VALUES (:1, :2, :3, :4, :5)",
            row
        )

    conn.commit()
    conn.close()
    print("  ✓ Sample data inserted into ENROLL_USER.ENROLLMENT")
    print("    C001 | 2024CS001-004 | Alice, Bob, Charlie, Diana   (4 students)")
    print("    C002 | 2024DS001-004 | Eve, Frank, Grace, Henry     (4 students)")
    print("    C003 | 2024ML001-004 | Ivy, Jack, Kate, Leo         (4 students)")
    print("    Total: 12 enrollment rows\n")



# ─────────────────────────────────────────────────────────────────────────────
#  Step 4 — EXAM_USER: create EXAM_ELIGIBILITY and insert data
# ─────────────────────────────────────────────────────────────────────────────

def setup_exam_schema():
    """
    Connect as EXAM_USER and create + populate EXAM_ELIGIBILITY.

    UPDATED: Now tracks per-student eligibility (12 rows — one per student)
    instead of per-course (3 rows). The roll_no links each row back to a
    specific student in ENROLL_USER.ENROLLMENT, making students identifiable
    even if two students share the same name.
    """
    print("── Step 4: Setting up EXAM_USER schema ──")
    conn   = oracledb.connect(
        user=EXAM_DB_USER, password=EXAM_DB_PASSWORD, dsn=EXAM_DB_DSN
    )
    cursor = conn.cursor()

    # Drop old table to apply schema changes
    try:
        cursor.execute("DROP TABLE exam_eligibility")
        print("  ℹ  Dropped old EXAM_ELIGIBILITY table to apply schema changes (adding roll_no).")
    except oracledb.DatabaseError as e:
        if e.args[0].code == 942:
            pass
        else:
            raise

    cursor.execute("""
        CREATE TABLE exam_eligibility (
            eligibility_id     NUMBER        PRIMARY KEY,
            roll_no            VARCHAR2(20)  UNIQUE NOT NULL,
            course_id          VARCHAR2(10)  NOT NULL,
            is_eligible        CHAR(1)       DEFAULT 'Y',   -- Y or N
            min_attendance_pct NUMBER(5,2)   DEFAULT 75,
            fee_cleared        CHAR(1)       DEFAULT 'N'    -- Y or N
        )
    """)
    print("  ✓ Created table: EXAM_USER.EXAM_ELIGIBILITY (per-student, with roll_no)")

    rows = [
        # (eligibility_id, roll_no,       course_id, eligible, attendance%, fee_cleared)
        # ── C001: Python Basics ───────────────────────────────────────────────
        (1,  "2024CS001", "C001", "Y", 80, "Y"),  # Alice   — good standing
        (2,  "2024CS002", "C001", "Y", 75, "N"),  # Bob     — fee not cleared
        (3,  "2024CS003", "C001", "Y", 90, "Y"),  # Charlie — good standing
        (4,  "2024CS004", "C001", "N", 60, "N"),  # Diana   — low attendance, not eligible

        # ── C002: Data Science Advanced ───────────────────────────────────────
        (5,  "2024DS001", "C002", "Y", 85, "Y"),  # Eve     — good standing
        (6,  "2024DS002", "C002", "Y", 78, "N"),  # Frank   — fee not cleared
        (7,  "2024DS003", "C002", "Y", 92, "Y"),  # Grace   — good standing
        (8,  "2024DS004", "C002", "N", 65, "N"),  # Henry   — low attendance

        # ── C003: Machine Learning Intro ──────────────────────────────────────
        (9,  "2024ML001", "C003", "Y", 88, "Y"),  # Ivy     — good standing
        (10, "2024ML002", "C003", "Y", 76, "N"),  # Jack    — fee not cleared
        (11, "2024ML003", "C003", "N", 55, "N"),  # Kate    — very low attendance
        (12, "2024ML004", "C003", "Y", 70, "N"),  # Leo     — borderline attendance
    ]

    for row in rows:
        cursor.execute(
            "INSERT INTO exam_eligibility "
            "(eligibility_id, roll_no, course_id, is_eligible, min_attendance_pct, fee_cleared) "
            "VALUES (:1, :2, :3, :4, :5, :6)",
            row
        )

    conn.commit()
    conn.close()
    print("  ✓ Sample data inserted into EXAM_USER.EXAM_ELIGIBILITY (12 rows)")
    print("    C001 | Alice(Y,80%,Y) Bob(Y,75%,N) Charlie(Y,90%,Y) Diana(N,60%,N)")
    print("    C002 | Eve(Y,85%,Y) Frank(Y,78%,N) Grace(Y,92%,Y) Henry(N,65%,N)")
    print("    C003 | Ivy(Y,88%,Y) Jack(Y,76%,N) Kate(N,55%,N) Leo(Y,70%,N)\n")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AI Validation Agent — Multi-Schema DB Setup")
    print("=" * 55)

    create_users()          # Step 1: SYSTEM creates 3 users
    setup_catalog_schema()  # Step 2: CATALOG_USER creates its table
    setup_enroll_schema()   # Step 3: ENROLL_USER creates its table (+ roll_no)
    setup_exam_schema()     # Step 4: EXAM_USER creates its table (per-student)

    print("=" * 55)
    print("✅ All schemas ready!")
    print()
    print("Schema summary:")
    print("  ENROLLMENT:       12 students, roll_no as unique identifier")
    print("  EXAM_ELIGIBILITY: 12 per-student rows, linked by roll_no")
    print("=" * 55)
