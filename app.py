"""
app.py — Flask web application entry point
-------------------------------------------
Natural language query interface:
  - User types plain English (e.g. "validate Python Basics")
  - LLM parses the query → extracts course_ids and rule_ids
  - Validation runs for all matched courses
  - Results shown per course
"""

# pyrefly: ignore [missing-import]
import os, json, time
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from agent import run_validation_agent, parse_natural_query, _get_llm
from reporter import generate_report
from db import get_course_catalog, get_enrollment, get_exam_eligibility
from compiler import parse_rules_txt, compile_rule, RULES_TXT_PATH, RULEBOOK_JSON_PATH

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-prod")


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        query  = request.form.get("query", "").strip()
        error  = None
        parsed = None
        all_results = []

        if not query:
            error = "Please enter a query."
        else:
            try:
                llm    = _get_llm()
                parsed = parse_natural_query(query, llm)

                course_ids = parsed["course_ids"]
                rule_ids   = parsed["rule_ids"] if parsed["rule_ids"] != "all" else None

                if not course_ids:
                    error = "No matching courses found. Try 'validate all courses' or mention a course name."
                else:
                    for cid in course_ids:
                        catalog    = get_course_catalog(cid)
                        enrollment = get_enrollment(cid)
                        exam       = get_exam_eligibility(cid)

                        if catalog is None:
                            continue

                        results = run_validation_agent(
                            cid,
                            rule_ids=rule_ids,
                            student_name=parsed.get("student_name", "all"),
                            roll_no=parsed.get("roll_no")
                        )

                        per_student = results.get("per_student", [])
                        sql_results = results.get("sql_results", [])

                        sname = parsed.get("student_name", "all")
                        if sname != "all" and not per_student:
                            continue

                        all_rule_results = (
                            [r for s in per_student for r in s["results"]] + sql_results
                        )

                        report = generate_report(
                            cid, catalog, enrollment, exam, all_rule_results
                        )

                        all_results.append({
                            "course_id":   cid,
                            "catalog":     catalog,
                            "enrollment":  enrollment,
                            "exam":        exam,
                            "per_student": per_student,
                            "sql_results": sql_results,
                            "report":      report,
                        })

                    if not all_results:
                        rno   = parsed.get("roll_no")
                        sname = parsed.get("student_name", "all")
                        if rno:
                            error = f"Roll number '{rno}' was not found in any matched course."
                        elif sname != "all":
                            error = f"Student '{sname}' was not found in any of the matched courses."
                        else:
                            error = "No courses matched your query."

            except Exception as e:
                error = f"Error: {str(e)}"

        # Store results in session then redirect → GET (PRG pattern)
        # This prevents re-running validation on browser refresh
        session["last_query"]   = query
        session["last_parsed"]  = parsed
        session["last_results"] = all_results
        session["last_error"]   = error
        return redirect(url_for("index"))

    # GET — read last results from session (cleared after render)
    query       = session.pop("last_query",   None)
    parsed      = session.pop("last_parsed",  None)
    all_results = session.pop("last_results", [])
    error       = session.pop("last_error",   None)

    return render_template(
        "index.html",
        query=query,
        parsed=parsed,
        all_results=all_results,
        error=error,
    )


# ─── Rules Editor routes ─────────────────────────────────────────────────────

@app.route("/rules", methods=["GET"])
def rules_editor():
    """Render the Rules Editor page."""
    try:
        with open(RULES_TXT_PATH, "r") as f:
            rules_content = f.read()
    except FileNotFoundError:
        rules_content = ""

    # Detect if rules.txt is newer than rulebook.json (uncompiled changes)
    needs_compile = False
    try:
        rules_mtime   = os.path.getmtime(RULES_TXT_PATH)
        rulebook_mtime = os.path.getmtime(RULEBOOK_JSON_PATH)
        needs_compile = rules_mtime > rulebook_mtime
    except OSError:
        needs_compile = bool(rules_content.strip())

    return render_template(
        "rules_editor.html",
        rules_content=rules_content,
        needs_compile=needs_compile,
    )


@app.route("/rules/save", methods=["POST"])
def rules_save():
    """Save POSTed content to rules.txt."""
    try:
        data = request.get_json(force=True)
        content = data.get("content", "")
        with open(RULES_TXT_PATH, "w") as f:
            f.write(content)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/rules/compile", methods=["POST"])
def rules_compile():
    """Compile rules.txt → rulebook.json and return a JSON log."""
    try:
        llm = _get_llm()
        rules_input = parse_rules_txt(RULES_TXT_PATH)

        if not rules_input:
            return jsonify({"ok": False, "error": "No rules found in rules.txt.", "log": [], "compiled": 0, "errors": 0})

        log = []
        compiled_rules = []
        errors = 0

        for i, rule in enumerate(rules_input):
            try:
                result = compile_rule(llm, rule["rule_id"], rule["text"])
                log.append({
                    "rule_id":     result["rule_id"],
                    "status":      "ok",
                    "type":        result.get("type", "arithmetic"),
                    "description": result.get("description", ""),
                    "condition":   result.get("condition", ""),
                    "severity":    result.get("severity", "MEDIUM"),
                })
                compiled_rules.append(result)
            except Exception as e:
                errors += 1
                log.append({
                    "rule_id": rule["rule_id"],
                    "status":  "error",
                    "error":   str(e),
                    "text":    rule["text"][:200],
                })

            if i < len(rules_input) - 1:
                time.sleep(2)   # respect Groq rate limits

        # Write rulebook only if at least one rule compiled
        if compiled_rules:
            rulebook = {"rules": compiled_rules}
            with open(RULEBOOK_JSON_PATH, "w") as f:
                json.dump(rulebook, f, indent=2)

        return jsonify({
            "ok":       errors == 0,
            "compiled": len(compiled_rules),
            "errors":   errors,
            "log":      log,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "log": [], "compiled": 0, "errors": 1}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)
