# =====================================================
# resume_routes.py — AI RESUME ANALYZER (FIXED + SAFE)
# =====================================================

import os, json, sqlite3
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from dotenv import load_dotenv
from pypdf import PdfReader
from openai import OpenAI
import re

load_dotenv()
router = APIRouter(prefix="/resume", tags=["Resume Analyzer"])
DB_NAME = "users.db"

# -----------------------------------------------------
# AI CONFIG (SAMBANOVA)
# -----------------------------------------------------
AI_API_KEY = os.getenv("SAMBANOVA_API_KEY")

client = None
if AI_API_KEY:
    client = OpenAI(
        api_key=AI_API_KEY,
        base_url="https://api.sambanova.ai/v1"
    )

# -----------------------------------------------------
# DB INIT
# -----------------------------------------------------
def init_resume_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS resume_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            ats_score INTEGER,
            breakdown TEXT,
            reasons TEXT,
            analysis TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_resume_db()

# -----------------------------------------------------
# PDF TEXT EXTRACTION
# -----------------------------------------------------
def extract_text_from_pdf(file: UploadFile) -> str:
    try:
        file.file.seek(0)
        reader = PdfReader(file.file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception:
        raise HTTPException(400, "PDF must be text-based (not scanned)")

# -----------------------------------------------------
# PROMPT
# -----------------------------------------------------
def build_prompt(resume_text: str) -> str:
    return f"""
Return ONLY valid JSON. No markdown. No explanation.

{{
  "ats_score": number,
  "strengths": [string],
  "improvements": [string],
  "missing_keywords": [string],
  "suggested_bullets": [string],
  "skill_breakdown": {{
    "programming": number,
    "frontend": number,
    "backend": number,
    "databases": number,
    "cloud_devops": number,
    "soft_skills": number
  }},
  "low_score_reasons": [string]
}}

RESUME:
\"\"\"
{resume_text[:6000]}
\"\"\"
"""

# -----------------------------------------------------
# SAFE JSON PARSER
# -----------------------------------------------------
def extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise HTTPException(500, "AI did not return JSON")
        return json.loads(match.group())

# -----------------------------------------------------
# ANALYZE RESUME
# -----------------------------------------------------
@router.post("/analyze")
async def analyze_resume(
    file: UploadFile = File(...),
    email: str | None = None
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF allowed")

    resume_text = extract_text_from_pdf(file)
    if len(resume_text) < 150:
        raise HTTPException(400, "Resume text too short")

    if not client:
        raise HTTPException(503, "AI service not configured")

    try:
        response = client.chat.completions.create(
            model="Meta-Llama-3.1-8B-Instruct",
            messages=[{"role": "user", "content": build_prompt(resume_text)}],
            temperature=0.4,
            max_tokens=900,
            timeout=30
        )

        raw = response.choices[0].message.content
        analysis = extract_json(raw)
        ats_score = int(analysis.get("ats_score", 0))

        if email:
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO resume_analysis
                (email, ats_score, breakdown, reasons, analysis, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                email,
                ats_score,
                json.dumps(analysis.get("skill_breakdown", {})),
                json.dumps(analysis.get("low_score_reasons", [])),
                json.dumps(analysis),
                datetime.utcnow().isoformat()
            ))
            conn.commit()
            conn.close()

        return {
            "success": True,
            "analysis": {**analysis, "score": ats_score}
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Resume analysis failed: {e}")

# -----------------------------------------------------
# HISTORY
# -----------------------------------------------------
@router.get("/history/{email}")
def get_resume_history(email: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT ats_score, breakdown, reasons, created_at
        FROM resume_analysis
        WHERE email=?
        ORDER BY created_at DESC
    """, (email,))
    rows = cur.fetchall()
    conn.close()

    return {
        "email": email,
        "history": [
            {
                "ats_score": r[0],
                "skill_breakdown": json.loads(r[1]),
                "low_score_reasons": json.loads(r[2]),
                "date": r[3]
            } for r in rows
        ]
    }
