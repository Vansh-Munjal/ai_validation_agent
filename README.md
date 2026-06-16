# 🛡 AI Course Validation Agent

An intelligent validation agent that checks course data consistency across **3 independent Oracle database schemas** using a **dynamic JSON rulebook**, **Python eval() engine**, and **Groq LLM** for natural language explanations.

---

## 🏗️ Architecture

```
Browser (Dashboard UI)
    ↓
Flask (app.py)          ← HTTP server, ties everything together
    ↓
db.py                   ← fetches data from 3 Oracle schemas
    ↓
agent.py                ← validates rules using Python eval() + Groq LLM
    ↓
reporter.py             ← formats health score + recommendations
    ↓
index.html              ← Jinja2 dashboard with actual vs expected values
```

---

## ✨ Features

- **Dynamic JSON Rulebook** — add/change rules without touching Python code (hot-reload)
- **Python `eval()` Engine** — 100% accurate math and logic evaluation
- **Groq LLM Explanations** — one-sentence natural language explanation per rule
- **Actual vs Expected** — shows exact current value and required value on any FAIL
- **3 Oracle Schemas** — `CATALOG_USER`, `ENROLL_USER`, `EXAM_USER` queried independently
- **Health Score Dashboard** — HEALTHY / WARNING / CRITICAL with animated ring
- **Quick Course Select** — sidebar buttons for instant C001 / C002 / C003 testing

---

## 🗄️ Database Schema

| Schema | Table | Key Fields |
|--------|-------|-----------|
| `CATALOG_USER` | `COURSE_CATALOG` | course_id, course_name, fee |
| `ENROLL_USER` | `ENROLLMENT` | enrollment_id, course_id, student_name, fee |
| `EXAM_USER` | `EXAM_ELIGIBILITY` | course_id, is_eligible, min_attendance_pct, fee_cleared |

---

## 📋 Rulebook Example

```json
{
  "rule_id": "R1",
  "description": "Catalog fee must be exactly Rs.500 less than enrollment fee",
  "condition": "catalog.fee == enrollment.fee - 500",
  "severity": "HIGH",
  "on_failure": "Update catalog fee accordingly",
  "data_sources": ["catalog", "enrollment"]
}
```

Any valid Python expression works in `condition`:
- `catalog.fee == enrollment.fee - 500` → exact math check
- `exam.fee_cleared == 'Y'` → string comparison
- `enrollment.fee <= catalog.fee * 1.10` → percentage check
- `exam.fee_cleared == 'Y' and exam.min_attendance_pct >= 75` → compound logic

---

## 🚀 Setup

### 1. Prerequisites
- Python 3.10+
- Oracle Free 23c/26ai running in Docker
- Groq API key (free at [console.groq.com](https://console.groq.com/keys))

### 2. Install dependencies
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure `.env`
```env
GROQ_API_KEY=your_groq_key_here

DB_USER=system
DB_PASSWORD=your_oracle_system_password
DB_DSN=localhost:1521/FREEPDB1

CATALOG_DB_USER=catalog_user
CATALOG_DB_PASSWORD=Catalog1234
CATALOG_DB_DSN=localhost:1521/FREEPDB1

ENROLL_DB_USER=enroll_user
ENROLL_DB_PASSWORD=Enroll1234
ENROLL_DB_DSN=localhost:1521/FREEPDB1

EXAM_DB_USER=exam_user
EXAM_DB_PASSWORD=Exam1234
EXAM_DB_DSN=localhost:1521/FREEPDB1
```

### 4. Start Oracle Docker
```bash
docker start oracle-free
```

### 5. Initialize Database (run once)
```bash
python setup_db.py
```

### 6. Run the app
```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## 📊 Expected Results

| Course | Student | Expected Score |
|--------|---------|---------------|
| C001 | Alice | 100% — HEALTHY |
| C002 | Bob | 60% — WARNING (R1, R5 fail) |
| C003 | Charlie | 60% — WARNING (R2, R4 fail) |

---

## 🗂️ File Overview

| File | Purpose |
|------|---------|
| `config.py` | Reads `.env`, exposes all credentials |
| `db.py` | All Oracle SQL queries (3 schemas) |
| `agent.py` | Validation brain: eval() + LLM |
| `reporter.py` | Health score and report generation |
| `app.py` | Flask HTTP server |
| `rulebook.json` | Business rules (JSON, no code needed) |
| `setup_db.py` | One-time Oracle DB initialization |
| `templates/index.html` | Dashboard UI |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | Flask |
| Database | Oracle Free 23c/26ai (Docker) |
| DB Driver | python-oracledb (thin mode) |
| LLM | Groq — Llama 3.1 8B Instant |
| LLM Framework | LangChain (langchain-groq) |
| Rule Evaluation | Python `eval()` with sandboxed namespace |
| Frontend | Vanilla HTML/CSS/JS + Jinja2 |

---

## 💡 Design Decisions

- **Python eval() for math** — LLMs make arithmetic errors; Python never does
- **LLM only for explanation** — minimizes API calls, avoids rate limits
- **JSON rulebook** — business users can modify rules without code changes
- **3 separate schemas** — simulates real enterprise architecture (isolated systems)
- **`__builtins__: {}`** — security: blocks file access inside eval conditions
