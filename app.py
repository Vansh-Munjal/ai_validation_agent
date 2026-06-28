"""
app.py — Flask web application entry point
-------------------------------------------
Natural language query interface:
  - User types plain English (e.g. "validate Python Basics")
  - LLM parses the query → extracts course_ids and rule_ids
  - Validation runs for all matched courses
  - Results shown per course
"""

from flask import Flask, render_template, request
from agent import run_validation_agent, parse_natural_query, _get_llm
from reporter import generate_report
from db import get_course_catalog, get_enrollment, get_exam_eligibility

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    query      = None    # Raw user query string
    all_results = []     # List of {course_id, catalog, enrollment, exam, results, report}
    error      = None
    parsed     = None    # {course_ids, rule_ids} from NL parser

    if request.method == "POST":
        query = request.form.get("query", "").strip()

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
                            continue   # skip courses not found in catalog

                        results = run_validation_agent(
                            cid,
                            rule_ids=rule_ids,
                            student_name=parsed.get("student_name", "all"),
                            roll_no=parsed.get("roll_no")
                        )

                        # results is now {per_student: [...], sql_results: [...]}.
                        # per_student is [] when the requested student is not in this course.
                        per_student = results.get("per_student", [])
                        sql_results = results.get("sql_results", [])

                        # Skip this course if the student wasn't enrolled here
                        sname = parsed.get("student_name", "all")
                        if sname != "all" and not per_student:
                            continue  # student not in this course — don't show it

                        # Build a flat results list for report generation
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

    return render_template(
        "index.html",
        query=query,
        parsed=parsed,
        all_results=all_results,
        error=error,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
