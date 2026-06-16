"""
app.py — Flask web application entry point (Phase 2.5)
-------------------------------------------------------
Fetches data from all THREE Oracle schemas, evaluates all rules
from rulebook.json, and generates a human-readable validation report.

    CATALOG_USER  → get_course_catalog()
    ENROLL_USER   → get_enrollment()
    EXAM_USER     → get_exam_eligibility()
    reporter.py   → generate_report()   ← NEW in Phase 2.5
"""

from flask import Flask, render_template, request
from db import get_course_catalog, get_enrollment, get_exam_eligibility
from agent import run_validation_agent    # Phase 3 — LangChain Groq agent
from reporter import generate_report   # Phase 2.5 — report generator

# Create the Flask application instance
app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Home route — serves the main validation page.

    GET  → render an empty form
    POST → fetch from all 3 schemas, evaluate all rules, render results
    """

    results    = None   # List of rule evaluation dicts
    report     = None   # Human-readable report dict (Phase 2.5)
    course_id  = None   # Submitted course ID
    catalog    = None   # Data from CATALOG_USER.COURSE_CATALOG
    enrollment = None   # Data from ENROLL_USER.ENROLLMENT
    exam       = None   # Data from EXAM_USER.EXAM_ELIGIBILITY
    error      = None   # Error message if something goes wrong

    if request.method == "POST":
        # Step 1: Read course_id from the HTML form
        course_id = request.form.get("course_id", "").strip().upper()

        if not course_id:
            error = "Please enter a Course ID."
        else:
            # Step 2: Fetch data from all THREE Oracle schemas independently
            catalog    = get_course_catalog(course_id)    # CATALOG_USER
            enrollment = get_enrollment(course_id)         # ENROLL_USER
            exam       = get_exam_eligibility(course_id)   # EXAM_USER ← NEW

            if catalog is None:
                error = f"Course '{course_id}' not found in CATALOG_USER.COURSE_CATALOG."
            elif enrollment is None:
                error = f"Course '{course_id}' not found in ENROLL_USER.ENROLLMENT."
            else:
                # Step 3: Run the LangChain agent — reads rulebook, fetches Oracle data,
                # evaluates each rule using Groq LLM, returns results
                results = run_validation_agent(course_id)

                # Step 4: Generate human-readable report from results
                report = generate_report(course_id, catalog, enrollment, exam, results)

    return render_template(
        "index.html",
        course_id=course_id,
        catalog=catalog,
        enrollment=enrollment,
        exam=exam,
        results=results,
        report=report,
        error=error
    )


# Run the Flask development server
if __name__ == "__main__":
    app.run(debug=True, port=5000)
