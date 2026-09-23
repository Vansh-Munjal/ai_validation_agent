"""
compiler.py — Plain-English Rule Compiler
------------------------------------------
Reads plain English rules from rules.txt and uses the Groq LLM to translate
each rule into a structured JSON entry in rulebook.json.

The non-technical user ONLY needs to:
  1. Edit rules.txt in plain English
  2. Run:  python compiler.py

The compiled rulebook.json is then used by the validation agent as-is.
No Python code changes are ever needed to add or update rules.

Architecture:
    rules.txt  →  compiler.py (LLM)  →  rulebook.json  →  web app (unchanged)
"""

import json
import os
import re
import time

from langchain_groq import ChatGroq
from config import GROQ_API_KEY


# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR          = os.path.dirname(__file__)
RULES_TXT_PATH    = os.path.join(BASE_DIR, "rules.txt")
RULEBOOK_JSON_PATH = os.path.join(BASE_DIR, "rulebook.json")


# ─── System prompt: tells the LLM how to translate a rule ────────────────────

SYSTEM_PROMPT = """You are a rule compiler for a university data validation system.
You translate plain English business rules into structured JSON for a validation engine.

The validation engine has access to THREE Oracle database tables:

  1. catalog     — CATALOG_USER.COURSE_CATALOG
                   columns: course_id (text), course_name (text), fee (number)

  2. enrollment  — ENROLL_USER.ENROLLMENT
                   columns: enrollment_id (number), course_id (text),
                            student_name (text), fee (number)

  3. exam        — EXAM_USER.EXAM_ELIGIBILITY
                   columns: eligibility_id (number), course_id (text),
                            is_eligible (text: 'Y'/'N'),
                            min_attendance_pct (number),
                            fee_cleared (text: 'Y'/'N')

IMPORTANT — Auto-correct field names:
  The user is non-technical and may use informal terms or make spelling mistakes.
  Always map informal terms to the exact field names below:

  ┌─────────────────────────────────────────────────────────────┐
  │ Informal / Misspelled Term        → Correct Field Name      │
  ├─────────────────────────────────────────────────────────────┤
  │ fee cleared, cleared, paid fees   → exam.fee_cleared        │
  │ eligible, eligable, eligibility   → exam.is_eligible        │
  │ attendance, attendence, attend%   → exam.min_attendance_pct │
  │ enrollment fee, student fee       → enrollment.fee          │
  │ catalog fee, course fee           → catalog.fee             │
  │ course name, subject name         → catalog.course_name     │
  │ student name, studnet name        → enrollment.student_name │
  └─────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════
  STEP 1 — CHOOSE THE TYPE (follow this decision tree exactly)
═══════════════════════════════════════════════════════════════

Use "arithmetic" when ALL of the following are true:
  ✓ The rule compares fields from ONE student's row (catalog, enrollment, or exam)
  ✓ The comparison uses math: ==, !=, <, >, <=, >=, %, //, **, and, or, not
  ✓ No counting, no aggregation, no joining across multiple rows
  Example: "enrollment fee must be exactly 1700 more than catalog fee"

Use "english" when ANY of the following are true:
  ✓ The rule says "if [course name contains X] then ... else ..."
  ✓ The rule needs reading and reasoning about text content
  ✓ The rule has conditional branching based on the course name or text fields
  ✓ The rule is ambiguous, qualitative, or hard to express as SQL or math
  IMPORTANT: ANY rule that says "if the course name contains..." MUST be "english".
  The EXAM_ELIGIBILITY table does NOT have a course_name column.
  You cannot check course_name in a SQL query against that table alone.
  Example: "if the course name contains Basics, check attendance; otherwise check fee"

Use "sql" when the rule needs:
  ✓ COUNT, SUM, AVG, MAX, MIN across multiple rows
  ✓ Checking how many rows exist for a course
  ✓ Comparing values ACROSS rows (not just within one row)
  ✓ Duplicate detection (same value appearing in more than one row)
  ✓ Cross-table JOINs between schemas
  ✓ Set operations (checking matching/missing IDs between tables)

═══════════════════════════════════════════════════════════════
  STEP 2 — BUILD THE CONDITION
═══════════════════════════════════════════════════════════════

For "arithmetic":
  Use Python expressions with dot-notation: catalog.fee, enrollment.fee, exam.fee_cleared
  Join multiple conditions with `and`.
  Example: "enrollment.fee - catalog.fee == 1700"

For "english":
  Write a plain English instruction starting with "Check whether..."
  Include ALL conditions from the rule. The LLM will evaluate it against real data.
  Example: "Check whether the course name contains 'Basics' or 'Intro' and attendance
            is >= 75, or the exam fee is cleared and the student is marked eligible."

For "sql":
  Write a valid Oracle SQL SELECT statement following ALL rules below.

═══════════════════════════════════════════════════════════════
  CRITICAL ORACLE SQL RULES — READ EVERY ONE BEFORE WRITING SQL
═══════════════════════════════════════════════════════════════

RULE A — ONE ROW CONTRACT (most important):
  The query MUST return EXACTLY ONE row with ONE numeric column.
  Value 1 → rule PASSES.   Value 0 → rule FAILS.
  Always wrap in: SELECT CASE WHEN <condition> THEN 1 ELSE 0 END FROM ...
  VIOLATION: Using GROUP BY without WHERE course_id = :course_id returns MULTIPLE rows
             (one per course) and CRASHES the validation engine.
  FIX: Always filter with WHERE course_id = :course_id when using GROUP BY,
       OR use a subquery / FROM DUAL pattern instead of GROUP BY.

RULE B — ORACLE USES MINUS NOT EXCEPT:
  Oracle SQL uses MINUS for set subtraction, not EXCEPT.
  WRONG:  SELECT course_id FROM A EXCEPT SELECT course_id FROM B
  CORRECT: SELECT course_id FROM A MINUS SELECT course_id FROM B

RULE C — DO NOT USE EXISTS IN SELECT CLAUSE:
  In Oracle, EXISTS can only appear in WHERE or HAVING — never in SELECT or CASE WHEN.
  WRONG:  SELECT CASE WHEN ... AND EXISTS (SELECT 1 FROM ...) THEN 1 ELSE 0 END FROM ...
  CORRECT: Replace EXISTS with a scalar subquery count:
           SELECT CASE WHEN ... AND (SELECT COUNT(*) FROM ...) > 0 THEN 1 ELSE 0 END FROM ...

RULE D — DO NOT MIX AGGREGATES WITH SCALAR SUBQUERIES IN CASE WHEN (ORA-00937):
  You cannot write: SELECT CASE WHEN MAX(...) = X AND (SELECT COUNT(*) FROM ...) > 0 ...
  This causes ORA-00937: not a single-group group function.
  FIX: Wrap all aggregates and scalar subqueries in a derived table:
  SELECT CASE WHEN col1 = X AND col2 > 0 THEN 1 ELSE 0 END
  FROM (SELECT MAX(...) AS col1, (SELECT COUNT(*) FROM ...) AS col2
        FROM ... WHERE course_id = :course_id)

RULE E — DUPLICATE DETECTION (same value in multiple rows):
  WRONG: COUNT(DISTINCT student_name) >= 2  ← counts unique names (OPPOSITE of duplicate)
  CORRECT: Use GROUP BY + HAVING to find names appearing more than once:
  SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END
  FROM (SELECT student_name FROM ENROLL_USER.ENROLLMENT
        WHERE course_id = :course_id
        GROUP BY student_name HAVING COUNT(*) >= 2)

RULE F — COUNTING ROWS ACROSS TWO TABLES (use FROM DUAL):
  To compare counts from two separate tables, use scalar subqueries with FROM DUAL:
  SELECT CASE WHEN
    (SELECT COUNT(*) FROM ENROLL_USER.ENROLLMENT WHERE course_id = :course_id)
    =
    (SELECT COUNT(*) FROM EXAM_USER.EXAM_ELIGIBILITY WHERE course_id = :course_id)
  THEN 1 ELSE 0 END FROM DUAL

RULE G — CONDITIONAL RULES (if course X then check Y, else check Z):
  Rules that say "if the course name is Basics/Intro do X, else do Y" MUST be type "english".
  Do NOT write SQL for these — the exam table has no course_name column.
  A conditional SQL JOIN on course_name is complex and fragile. Use "english" instead.

RULE H — SET MATCHING BETWEEN TABLES (use MINUS, check for 0 unmatched):
  SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END
  FROM (
    SELECT course_id FROM ENROLL_USER.ENROLLMENT
    MINUS
    SELECT course_id FROM EXAM_USER.EXAM_ELIGIBILITY
    UNION ALL
    SELECT course_id FROM EXAM_USER.EXAM_ELIGIBILITY
    MINUS
    SELECT course_id FROM ENROLL_USER.ENROLLMENT
  )

═══════════════════════════════════════════════════════════════
  SQL EXAMPLES (reference these patterns)
═══════════════════════════════════════════════════════════════

• Count rows in one table for a course:
  SELECT CASE WHEN COUNT(*) = 4 THEN 1 ELSE 0 END
  FROM ENROLL_USER.ENROLLMENT WHERE course_id = :course_id

• At least 2 students share the same name (duplicate detection):
  SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END
  FROM (SELECT student_name FROM ENROLL_USER.ENROLLMENT
        WHERE course_id = :course_id
        GROUP BY student_name HAVING COUNT(*) >= 2)

• Enrollment count equals exam count for this course (FROM DUAL pattern):
  SELECT CASE WHEN
    (SELECT COUNT(*) FROM ENROLL_USER.ENROLLMENT WHERE course_id = :course_id)
    = (SELECT COUNT(*) FROM EXAM_USER.EXAM_ELIGIBILITY WHERE course_id = :course_id)
  THEN 1 ELSE 0 END FROM DUAL

• No unmatched course_ids between enrollment and exam (MINUS pattern):
  SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END
  FROM (SELECT course_id FROM ENROLL_USER.ENROLLMENT
        MINUS SELECT course_id FROM EXAM_USER.EXAM_ELIGIBILITY
        UNION ALL
        SELECT course_id FROM EXAM_USER.EXAM_ELIGIBILITY
        MINUS SELECT course_id FROM ENROLL_USER.ENROLLMENT)

• All rows in exam must pass a condition for this course:
  SELECT CASE WHEN COUNT(*) = COUNT(CASE WHEN fee_cleared = 'Y' AND is_eligible = 'Y' THEN 1 END)
  THEN 1 ELSE 0 END
  FROM EXAM_USER.EXAM_ELIGIBILITY WHERE course_id = :course_id

• One-to-one mapping check within one table:
  SELECT CASE WHEN COUNT(DISTINCT course_id) = COUNT(*) AND COUNT(DISTINCT course_name) = COUNT(*)
  THEN 1 ELSE 0 END FROM CATALOG_USER.COURSE_CATALOG

• Only the maximum fee row may exceed a threshold (avoid GROUP BY — use subquery for MAX):
  SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END
  FROM ENROLL_USER.ENROLLMENT e
  JOIN CATALOG_USER.COURSE_CATALOG c ON e.course_id = c.course_id
  WHERE c.course_id = :course_id
    AND e.fee > c.fee
    AND e.fee < (SELECT MAX(fee) FROM ENROLL_USER.ENROLLMENT WHERE course_id = :course_id)

• Complex aggregation with scalar subquery (derived table pattern to avoid ORA-00937):
  SELECT CASE WHEN col1 IS NULL OR (col1 = col2 + 1700 AND col3 > 0) THEN 1 ELSE 0 END
  FROM (SELECT MAX(CASE WHEN e.fee > c.fee THEN e.fee END) AS col1,
               MAX(c.fee) AS col2,
               (SELECT COUNT(*) FROM EXAM_USER.EXAM_ELIGIBILITY
                WHERE course_id = :course_id AND fee_cleared = 'Y' AND is_eligible = 'Y') AS col3
        FROM CATALOG_USER.COURSE_CATALOG c
        JOIN ENROLL_USER.ENROLLMENT e ON c.course_id = e.course_id
        WHERE c.course_id = :course_id)

═══════════════════════════════════════════════════════════════
  MANDATORY SELF-CHECK BEFORE OUTPUTTING JSON
═══════════════════════════════════════════════════════════════

Before writing the JSON, ask yourself:
  1. If the rule says "if course name contains X... else...", have I set type = "english"?
  2. If type = "sql", does my query return EXACTLY ONE ROW?
  3. If I used GROUP BY, did I filter with WHERE course_id = :course_id?
  4. Did I use MINUS (not EXCEPT)?
  5. Did I avoid EXISTS in the SELECT/CASE WHEN clause?
  6. If I used both MAX() and a scalar subquery, did I wrap them in a derived table?
  7. For duplicate detection, did I use GROUP BY + HAVING (not COUNT DISTINCT)?

Output ONLY the raw JSON object with fields: rule_id (omit this), type, description,
condition, severity, on_failure, data_sources.
No markdown, no code fences, no extra explanation."""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_llm() -> ChatGroq:
    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_key_here":
        raise ValueError(
            "GROQ_API_KEY is not set in your .env file.\n"
            "Get a free key at: https://console.groq.com/keys"
        )
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=GROQ_API_KEY,
        temperature=0,
        max_retries=2,
    )


def parse_rules_txt(path: str) -> list[dict]:
    """
    Read rules.txt and return a list of {rule_id, text} dicts.

    Format expected:
        [R1]
        Plain English description of rule 1...

        [R2]
        Plain English description of rule 2...
    """
    with open(path, "r") as f:
        content = f.read()

    # Split on [R<n>] markers
    parts = re.split(r"\[R(\d+)\]\s*", content)
    # parts = ['preamble', '1', 'text1', '2', 'text2', ...]

    rules = []
    i = 1
    while i < len(parts) - 1:
        rule_num = parts[i].strip()
        rule_text = parts[i + 1].strip()
        # Strip comment lines (starting with #)
        cleaned_lines = [
            line for line in rule_text.splitlines()
            if not line.strip().startswith("#") and line.strip()
        ]
        rule_text = "\n".join(cleaned_lines)
        if rule_text:
            rules.append({"rule_id": f"R{rule_num}", "text": rule_text})
        i += 2

    return rules


def _extract_json(text: str) -> dict:
    """
    Extract and parse the first valid JSON object from a string.
    Handles cases where the LLM wraps the JSON in markdown or adds extra text.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?", "", text, flags=re.MULTILINE).strip().rstrip("`").strip()

    # Try parsing the whole string first (clean response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first {...} block using regex
    match = re.search(r"\{[\s\S]+?\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Find the largest {...} block (greedy) — handles nested JSON
    match = re.search(r"\{[\s\S]+\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON found in LLM response", text, 0)


def compile_rule(llm: ChatGroq, rule_id: str, rule_text: str) -> dict:
    """
    Send one plain English rule to the LLM.
    Returns a structured dict ready to be written into rulebook.json.
    Retries up to 3 times on JSON parse failures.
    """
    prompt = f"{SYSTEM_PROMPT}\n\nRule to compile:\n{rule_text}"

    for attempt in range(3):
        try:
            resp = llm.invoke(prompt)
            content = str(resp.content).strip()
            parsed = _extract_json(content)

            # Build final rule dict (canonical key order)
            rule = {
                "rule_id":     rule_id,
                "type":        parsed["type"],
                "description": parsed["description"],
                "condition":   parsed["condition"],
                "severity":    parsed.get("severity", "HIGH"),
                "on_failure":  parsed.get("on_failure", ""),
                "data_sources": parsed.get("data_sources", []),
            }
            return rule

        except (json.JSONDecodeError, KeyError) as e:
            print(f"   ⚠  Parse error (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(3)

    raise ValueError(
        f"Failed to compile {rule_id} after 3 attempts. "
        f"Check rules.txt and try again."
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  AI Validation Agent — Rule Compiler")
    print("=" * 55)

    llm = _get_llm()

    print(f"\n📄 Reading:  {RULES_TXT_PATH}")
    rules_input = parse_rules_txt(RULES_TXT_PATH)

    if not rules_input:
        print("⚠  No rules found in rules.txt. Add rules using [R1], [R2] blocks.")
        return

    print(f"✓  Found {len(rules_input)} rule(s) to compile.\n")

    compiled = []
    for i, rule in enumerate(rules_input):
        print(f"── Compiling {rule['rule_id']} ...")
        print(f"   Input: {rule['text'][:100].replace(chr(10), ' ')}...")

        result = compile_rule(llm, rule["rule_id"], rule["text"])

        print(f"   Type:      {result['type']}")
        print(f"   Condition: {result['condition'][:90]}...")
        print(f"   Severity:  {result['severity']}")
        print(f"   ✅ Compiled successfully\n")

        compiled.append(result)

        # Small delay between rules to respect Groq rate limits
        if i < len(rules_input) - 1:
            time.sleep(2)

    # Write the compiled rulebook
    rulebook = {"rules": compiled}
    with open(RULEBOOK_JSON_PATH, "w") as f:
        json.dump(rulebook, f, indent=2)

    print("=" * 55)
    print(f"✅ rulebook.json updated with {len(compiled)} rule(s)!")
    print(f"   Output: {RULEBOOK_JSON_PATH}")
    print(f"\n   The web app will use the new rules immediately.")
    print(f"   No other files need to be changed.")
    print("=" * 55)


if __name__ == "__main__":
    main()