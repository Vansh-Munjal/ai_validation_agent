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

  1. catalog  — fields: course_id (text), course_name (text), fee (number)
  2. enrollment — fields: enrollment_id (number), course_id (text), student_name (text), fee (number)
  3. exam — fields: eligibility_id (number), course_id (text), is_eligible (text: 'Y'/'N'),
                    min_attendance_pct (number: the student's actual attendance %),
                    fee_cleared (text: 'Y'/'N')

Your job: given a plain English rule, output ONLY a valid JSON object with these exact fields:

{
  "type": "arithmetic" or "english",
  "description": "<one clean sentence summarising the rule>",
  "condition": "<see rules below>",
  "severity": "HIGH" or "MEDIUM" or "LOW",
  "on_failure": "<one sentence telling what to fix>",
  "data_sources": ["catalog", "enrollment", "exam"]  -- only include tables actually referenced
}

Rules for choosing type:
  - "arithmetic" : the rule can be fully expressed as a Python boolean expression
                   using only numbers, math operators (+, -, *, /, //, %, **),
                   and comparisons (==, !=, <, >, <=, >=, and, or, not, in)
  - "english"    : the rule requires qualitative reasoning or checking the text
                   content of a field (e.g. checking if a course name contains a word)

For "arithmetic" — condition must be a single Python expression string using
  catalog.field, enrollment.field, exam.field  dot-notation.
  Example: "(enrollment.fee - catalog.fee) == 1500 and enrollment.fee % 500 == 0"

For "english" — condition must be a plain English instruction (starting with "Check...")
  that the LLM will read and evaluate against the actual data.
  Example: "Check whether the student has paid the exam fee and is marked as eligible."

Output ONLY the raw JSON object. No markdown, no code fences, no extra explanation."""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_llm() -> ChatGroq:
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
    parts = re.split(r"\[R(\d+)\]", content)
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
