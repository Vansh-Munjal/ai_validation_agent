-- ============================================================
-- oracle_setup.sql
-- AI Course Validation Agent — Multi-Schema Reference Script
-- ============================================================
-- Run this manually in SQL*Plus or as a reference.
-- The Python setup_db.py does the same thing programmatically.
--
-- Connect as SYSTEM first:
--   docker exec -it oracle-free sqlplus 'system/"Vansh@1234"@FREEPDB1'
-- ============================================================


-- ─────────────────────────────────────────────────────────────
--  SECTION 1: Create the three Oracle users (as SYSTEM/DBA)
-- ─────────────────────────────────────────────────────────────

-- CATALOG_USER: owns the COURSE_CATALOG table
CREATE USER catalog_user IDENTIFIED BY "Catalog1234";
GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO catalog_user;

-- ENROLL_USER: owns the ENROLLMENT table
CREATE USER enroll_user IDENTIFIED BY "Enroll1234";
GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO enroll_user;

-- EXAM_USER: owns the EXAM_ELIGIBILITY table
CREATE USER exam_user IDENTIFIED BY "Exam1234";
GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO exam_user;


-- ─────────────────────────────────────────────────────────────
--  SECTION 2: CATALOG_USER schema
--  Connect as: catalog_user / Catalog1234 @ FREEPDB1
-- ─────────────────────────────────────────────────────────────

-- In SQL*Plus: CONNECT catalog_user/"Catalog1234"@FREEPDB1

CREATE TABLE course_catalog (
    course_id   VARCHAR2(10)  PRIMARY KEY,
    course_name VARCHAR2(100) NOT NULL,
    fee         NUMBER(10, 2) NOT NULL
);

-- C001 fee = 5000 (INTENTIONAL MISMATCH with enrollment fee of 6000)
-- C002 fee = 8000 (matches enrollment → PASS)
INSERT INTO course_catalog VALUES ('C001', 'Python Basics',          5000);
INSERT INTO course_catalog VALUES ('C002', 'Data Science Advanced',  8000);
INSERT INTO course_catalog VALUES ('C003', 'Machine Learning Intro', 12000);
COMMIT;

-- Verify:
-- SELECT * FROM course_catalog;


-- ─────────────────────────────────────────────────────────────
--  SECTION 3: ENROLL_USER schema
--  Connect as: enroll_user / Enroll1234 @ FREEPDB1
-- ─────────────────────────────────────────────────────────────

-- In SQL*Plus: CONNECT enroll_user/"Enroll1234"@FREEPDB1

-- NOTE: No FOREIGN KEY to course_catalog — these are independent systems!
CREATE TABLE enrollment (
    enrollment_id NUMBER        PRIMARY KEY,
    course_id     VARCHAR2(10)  NOT NULL,
    student_name  VARCHAR2(100) NOT NULL,
    fee           NUMBER(10, 2) NOT NULL
);

-- C001 fee = 6000 ← MISMATCH with catalog's 5000 → R1 will FAIL
-- C002 fee = 8000 ← same as catalog → R1 will PASS
INSERT INTO enrollment VALUES (1, 'C001', 'Alice',   6000);
INSERT INTO enrollment VALUES (2, 'C002', 'Bob',     8000);
INSERT INTO enrollment VALUES (3, 'C003', 'Charlie', 12000);
COMMIT;

-- Verify:
-- SELECT * FROM enrollment;


-- ─────────────────────────────────────────────────────────────
--  SECTION 4: EXAM_USER schema
--  Connect as: exam_user / Exam1234 @ FREEPDB1
-- ─────────────────────────────────────────────────────────────

-- In SQL*Plus: CONNECT exam_user/"Exam1234"@FREEPDB1

CREATE TABLE exam_eligibility (
    eligibility_id     NUMBER        PRIMARY KEY,
    course_id          VARCHAR2(10)  NOT NULL,
    is_eligible        CHAR(1)       DEFAULT 'Y',   -- Y=eligible, N=not
    min_attendance_pct NUMBER(5, 2)  DEFAULT 75,    -- required attendance %
    fee_cleared        CHAR(1)       DEFAULT 'N'    -- Y=fee paid, N=unpaid
);

INSERT INTO exam_eligibility VALUES (1, 'C001', 'Y', 75, 'N');  -- fee not cleared
INSERT INTO exam_eligibility VALUES (2, 'C002', 'Y', 80, 'Y');  -- all good
INSERT INTO exam_eligibility VALUES (3, 'C003', 'N', 60, 'N');  -- low attendance
COMMIT;

-- Verify:
-- SELECT * FROM exam_eligibility;


-- ─────────────────────────────────────────────────────────────
--  SECTION 5: Cross-schema access (optional — not used in Phase 1)
-- ─────────────────────────────────────────────────────────────
-- If you ever need ENROLL_USER to read from CATALOG_USER's table,
-- run this as SYSTEM:

-- GRANT SELECT ON catalog_user.course_catalog TO enroll_user;

-- Then ENROLL_USER can query:
-- SELECT * FROM catalog_user.course_catalog;

-- This is NOT needed in Phase 1 because each schema only reads its own table.
-- The Python app holds the three connections separately and joins them in memory.


-- ─────────────────────────────────────────────────────────────
--  Expected validation output
-- ─────────────────────────────────────────────────────────────
-- Course C001:
--   CATALOG_USER.course_catalog.fee  = 5000
--   ENROLL_USER.enrollment.fee       = 6000
--   Rule R1: catalog.fee == enrollment.fee → 5000 == 6000 → FALSE → FAIL ❌
--
-- Course C002:
--   CATALOG_USER.course_catalog.fee  = 8000
--   ENROLL_USER.enrollment.fee       = 8000
--   Rule R1: catalog.fee == enrollment.fee → 8000 == 8000 → TRUE → PASS ✅
