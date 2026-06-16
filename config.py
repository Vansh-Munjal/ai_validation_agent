"""
config.py — Central configuration loader
-----------------------------------------
Reads credentials for ALL THREE Oracle schemas from .env.

Architecture:
    CATALOG_USER  →  owns COURSE_CATALOG table
    ENROLL_USER   →  owns ENROLLMENT table
    EXAM_USER     →  owns EXAM_ELIGIBILITY table

The SYSTEM (admin) credentials are used only by setup_db.py
to create the three users. The app itself never uses SYSTEM.
"""

import os
from dotenv import load_dotenv

# Load all KEY=VALUE pairs from .env into environment variables
load_dotenv()

# ── Google Gemini LLM key (backup) ──────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# ── Groq API key (primary — 14,400 req/day free) ─────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ── LangSmith — observability & tracing ──────────────────────────────────────
LANGCHAIN_TRACING_V2  = os.getenv("LANGCHAIN_TRACING_V2",  "false")
LANGCHAIN_API_KEY     = os.getenv("LANGCHAIN_API_KEY",     "")
LANGCHAIN_PROJECT     = os.getenv("LANGCHAIN_PROJECT",     "ai-validation-agent")
LANGCHAIN_ENDPOINT    = os.getenv("LANGCHAIN_ENDPOINT",    "https://api.smith.langchain.com")

# Set as OS env vars — LangChain reads these automatically to enable tracing
if LANGCHAIN_API_KEY:
    os.environ["LANGCHAIN_TRACING_V2"] = LANGCHAIN_TRACING_V2
    os.environ["LANGCHAIN_API_KEY"]    = LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_PROJECT"]    = LANGCHAIN_PROJECT
    os.environ["LANGCHAIN_ENDPOINT"]   = LANGCHAIN_ENDPOINT


# ── SYSTEM / DBA — only for setup_db.py ─────────────────────────────────────
DB_USER     = os.getenv("DB_USER",     "system")
DB_PASSWORD = os.getenv("DB_PASSWORD", "oracle")
DB_DSN      = os.getenv("DB_DSN",      "localhost:1521/FREEPDB1")

# ── CATALOG_USER schema ──────────────────────────────────────────────────────
CATALOG_DB_USER     = os.getenv("CATALOG_DB_USER",     "catalog_user")
CATALOG_DB_PASSWORD = os.getenv("CATALOG_DB_PASSWORD", "Catalog1234")
CATALOG_DB_DSN      = os.getenv("CATALOG_DB_DSN",      "localhost:1521/FREEPDB1")

# ── ENROLL_USER schema ───────────────────────────────────────────────────────
ENROLL_DB_USER     = os.getenv("ENROLL_DB_USER",     "enroll_user")
ENROLL_DB_PASSWORD = os.getenv("ENROLL_DB_PASSWORD", "Enroll1234")
ENROLL_DB_DSN      = os.getenv("ENROLL_DB_DSN",      "localhost:1521/FREEPDB1")

# ── EXAM_USER schema ─────────────────────────────────────────────────────────
EXAM_DB_USER     = os.getenv("EXAM_DB_USER",     "exam_user")
EXAM_DB_PASSWORD = os.getenv("EXAM_DB_PASSWORD", "Exam1234")
EXAM_DB_DSN      = os.getenv("EXAM_DB_DSN",      "localhost:1521/FREEPDB1")
