# ================================================================
# interview.py — FULL AI MOCK INTERVIEW ENGINE (FINAL)
# ================================================================

import os, json, uuid, sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
from dotenv import load_dotenv

from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

load_dotenv()

# ------------------------------------------------
# CONFIG
# ------------------------------------------------
DB = "mock_interview.db"
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# ------------------------------------------------
# AI CLIENT
# ------------------------------------------------
try:
    from openai import OpenAI
    client = OpenAI(
        base_url="https://api.sambanova.ai/v1",
        api_key=SAMBANOVA_API_KEY
    )
except:
    client = None

MODEL = "Meta-Llama-3.1-8B-Instruct"

# ------------------------------------------------
# DB SETUP
# ------------------------------------------------
db = sqlite3.connect(DB, check_same_thread=False)
cur = db.cursor()

cur.executescript("""
CREATE TABLE IF NOT EXISTS mock_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  difficulty TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS mock_answers (
  session_id TEXT,
  skill TEXT,
  question TEXT,
  answer TEXT,
  score INTEGER
);

CREATE TABLE IF NOT EXISTS mock_reports (
  session_id TEXT PRIMARY KEY,
  final_score INTEGER,
  skill_scores TEXT,
  summary TEXT,
  strengths TEXT,
  improvements TEXT
);
""")
db.commit()

# ------------------------------------------------
# ROUTER
# ------------------------------------------------
router = APIRouter(tags=["mock-interview"])

# ------------------------------------------------
# MODELS
# ------------------------------------------------
class StartReq(BaseModel):
    skills: List[str]

class AnswerReq(BaseModel):
    session_id: str
    skill: str
    question: str
    answer: Optional[str] = ""

# ------------------------------------------------
# HELPERS
# ------------------------------------------------
def ai_generate_question(skill, difficulty):
    if not client:
        return f"Explain {skill} ({difficulty} level)."

    prompt = f"Ask one {difficulty} interview question about {skill}."
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return r.choices[0].message.content.strip()

def ai_evaluate(q, a):
    if not client:
        return 6

    prompt = f"""
Evaluate answer.
Question: {q}
Answer: {a}
Return only score (1-10).
"""
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    return int("".join(filter(str.isdigit, r.choices[0].message.content)) or 5)

def next_difficulty(score, current):
    order = ["easy", "medium", "hard"]
    idx = order.index(current)
    if score >= 8 and idx < 2:
        return order[idx+1]
    if score <= 4 and idx > 0:
        return order[idx-1]
    return current

# ------------------------------------------------
# START MOCK
# ------------------------------------------------
@router.post("/mock/start")
def start_mock(req: StartReq, user_id: str):
    session_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO mock_sessions VALUES (?,?,?,?)",
        (session_id, user_id, "medium", datetime.utcnow().isoformat())
    )
    db.commit()

    return {"session_id": session_id}

# ------------------------------------------------
# ANSWER (TEXT OR VOICE)
# ------------------------------------------------
@router.post("/mock/answer")
def submit_answer(req: AnswerReq):
    score = ai_evaluate(req.question, req.answer)

    cur.execute(
        "INSERT INTO mock_answers VALUES (?,?,?,?,?)",
        (req.session_id, req.skill, req.question, req.answer, score)
    )

    cur.execute(
        "SELECT difficulty FROM mock_sessions WHERE id=?",
        (req.session_id,)
    )
    diff = cur.fetchone()[0]
    new_diff = next_difficulty(score, diff)

    cur.execute(
        "UPDATE mock_sessions SET difficulty=? WHERE id=?",
        (new_diff, req.session_id)
    )
    db.commit()

    return {"score": score, "next_difficulty": new_diff}

# ------------------------------------------------
# FINAL REPORT
# ------------------------------------------------
@router.get("/mock/report/{session_id}")
def final_report(session_id: str):
    rows = cur.execute(
        "SELECT skill, score FROM mock_answers WHERE session_id=?",
        (session_id,)
    ).fetchall()

    skill_scores = {}
    for s, sc in rows:
        skill_scores.setdefault(s, []).append(sc)

    skill_avg = {k: sum(v)*10//len(v) for k,v in skill_scores.items()}
    final_score = sum(skill_avg.values()) // len(skill_avg)

    report = {
        "final_score": final_score,
        "skill_scores": skill_avg,
        "summary": "Strong technical foundation.",
        "strengths": list(skill_avg.keys()),
        "improvements": ["Answer depth", "Examples"]
    }

    cur.execute(
        "INSERT OR REPLACE INTO mock_reports VALUES (?,?,?,?,?,?)",
        (session_id, final_score, json.dumps(skill_avg),
         report["summary"],
         json.dumps(report["strengths"]),
         json.dumps(report["improvements"]))
    )
    db.commit()

    return report

# ------------------------------------------------
# PDF REPORT
# ------------------------------------------------
@router.get("/mock/report/{session_id}/pdf")
def pdf_report(session_id: str):
    r = cur.execute(
        "SELECT final_score, skill_scores FROM mock_reports WHERE session_id=?",
        (session_id,)
    ).fetchone()

    if not r:
        raise HTTPException(404, "Report not found")

    file = f"/tmp/report-{session_id}.pdf"
    doc = SimpleDocTemplate(file)
    styles = getSampleStyleSheet()

    content = [
        Paragraph(f"Final Score: {r[0]}", styles["Heading1"]),
        Paragraph(f"Skill Scores: {r[1]}", styles["Normal"]),
    ]

    doc.build(content)
    return {"pdf_path": file}
