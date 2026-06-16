"""
validator.py — The core rule evaluation engine (Phase 2)
---------------------------------------------------------
Phase 2 change: evaluate_rules() now accepts data from all THREE schemas:
    catalog_data    → from CATALOG_USER.COURSE_CATALOG
    enrollment_data → from ENROLL_USER.ENROLLMENT
    exam_data       → from EXAM_USER.EXAM_ELIGIBILITY

All three are made available inside eval() so any rule in rulebook.json
can reference catalog.field, enrollment.field, or exam.field freely.

KEY DESIGN GOAL: Adding new rules to rulebook.json still requires
ZERO Python code changes — just add a new entry to the JSON.
"""

import json
import os


# Path to the rulebook — sits in the same directory as this file
RULEBOOK_PATH = os.path.join(os.path.dirname(__file__), "rulebook.json")


def load_rules():
    """
    Read and parse rulebook.json.

    Returns a list of rule dicts. Each rule has:
        rule_id      : unique ID (R1, R2, ...)
        description  : human-readable explanation
        condition    : Python expression string (evaluated dynamically)
        severity     : HIGH / MEDIUM / LOW
        on_failure   : what action to take if rule fails
        data_sources : which schemas the rule touches (documentation only)
    """
    with open(RULEBOOK_PATH, "r") as f:
        data = json.load(f)
    return data["rules"]


def evaluate_rules(catalog_data, enrollment_data, exam_data=None):
    """
    Evaluate ALL rules from rulebook.json against data from all 3 schemas.

    Parameters:
        catalog_data    (dict): Row from CATALOG_USER.COURSE_CATALOG
                                e.g. { "course_id": "C001", "fee": 5000 }

        enrollment_data (dict): Row from ENROLL_USER.ENROLLMENT
                                e.g. { "enrollment_id": 1, "fee": 6000 }

        exam_data       (dict or None): Row from EXAM_USER.EXAM_ELIGIBILITY
                                e.g. { "fee_cleared": "Y", "min_attendance_pct": 75 }
                                If None, rules using `exam.*` will return ERROR.

    Returns:
        List of result dicts, one per rule:
        [
            {
                "rule_id":     "R1",
                "description": "...",
                "status":      "PASS" or "FAIL" or "ERROR: ...",
                "severity":    "HIGH",
                "on_failure":  "...",
                "condition":   "catalog.fee == enrollment.fee",
                "sources":     ["catalog", "enrollment"]
            },
            ...
        ]

    How eval() works here:
    ----------------------
    The condition string from JSON is evaluated with a context dict:
        {
            "catalog":    SimpleNamespace(fee=5000, ...),
            "enrollment": SimpleNamespace(fee=6000, ...),
            "exam":       SimpleNamespace(fee_cleared='Y', ...)
        }
    So "catalog.fee == enrollment.fee" becomes 5000 == 6000 → False → FAIL.
    Adding a new rule to the JSON instantly works — no Python change needed.
    """

    from types import SimpleNamespace

    rules   = load_rules()
    results = []

    # Wrap each dict in SimpleNamespace to allow dot notation in eval()
    # e.g.  catalog.fee  instead of  catalog["fee"]
    catalog    = SimpleNamespace(**catalog_data)
    enrollment = SimpleNamespace(**enrollment_data)

    # exam_data is optional — if missing, use an empty object so eval()
    # produces a clear AttributeError instead of a confusing NameError
    exam = SimpleNamespace(**(exam_data or {}))

    # Build the evaluation context — all three schemas available
    eval_context = {
        "catalog":    catalog,
        "enrollment": enrollment,
        "exam":       exam,
    }

    for rule in rules:
        condition_str = rule["condition"]  # e.g. "catalog.fee == enrollment.fee"

        try:
            # Dynamically execute the condition string as Python code
            # __builtins__ is disabled to prevent dangerous operations in eval
            passed = eval(condition_str, {"__builtins__": {}}, eval_context)
            status = "PASS" if passed else "FAIL"
        except AttributeError as e:
            # This happens when exam_data was None but rule uses exam.*
            status = f"ERROR: missing data — {str(e)}"
        except Exception as e:
            status = f"ERROR: {str(e)}"

        results.append({
            "rule_id":     rule["rule_id"],
            "description": rule["description"],
            "status":      status,
            "severity":    rule["severity"],
            "on_failure":  rule["on_failure"],
            "condition":   condition_str,
            "sources":     rule.get("data_sources", []),
        })

    return results
