"""
validator.py — The core rule evaluation engine (Phase 2)
---------------------------------------------------------
Evaluates rules from rulebook.json using python_eval() from tools.py.
All three Oracle schemas are available inside conditions:
    catalog.field, enrollment.field, exam.field

Adding new rules to rulebook.json requires ZERO Python code changes.
"""

import json
import os
from tools import python_eval

RULEBOOK_PATH = os.path.join(os.path.dirname(__file__), "rulebook.json")


def load_rules():
    """Read and parse rulebook.json."""
    with open(RULEBOOK_PATH, "r") as f:
        data = json.load(f)
    return data["rules"]


def evaluate_rules(catalog_data, enrollment_data, exam_data=None):
    """
    Evaluate ALL rules from rulebook.json against data from all 3 schemas.

    Returns a list of result dicts with rule_id, description, status, etc.
    """
    rules   = load_rules()
    results = []

    for rule in rules:
        condition_str = rule["condition"]

        try:
            passed = python_eval(condition_str, catalog_data, enrollment_data, exam_data)
            status = "PASS" if passed else "FAIL"
        except AttributeError as e:
            status = f"ERROR: missing data — {e}"
        except Exception as e:
            status = f"ERROR: {e}"

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
