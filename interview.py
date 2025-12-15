# ================================================================
# interview.py — NEXTHIRE AI ENGINE (DIFFICULTY FIXED)
# Features: Adaptive Difficulty, Strict JSON, Auto-Recovery
# ================================================================

import os
import json
import uuid
import sqlite3
import logging
import re
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter
from pydantic import BaseModel
from dotenv import load_dotenv

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NextHireEngine")

# --- LOAD ENV ---
load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")
SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1"
MODEL_NAME = "Meta-Llama-3.1-8B-Instruct"

DB_NAME = "mock_interview.db"

# We use APIRouter so main.py can use this file
router = APIRouter()

# ==========================================
# DATABASE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        skills TEXT,
        experience TEXT,
        created_at TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS qa_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        question TEXT,
        user_answer TEXT,
        ai_feedback TEXT,
        score INTEGER
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ==========================================
# AI CLIENT
# ==========================================
client = None
try:
    from openai import OpenAI
    if SAMBANOVA_API_KEY:
        client = OpenAI(base_url=SAMBANOVA_BASE_URL, api_key=SAMBANOVA_API_KEY)
        logger.info(f"✅ Router Connected to SambaNova ({MODEL_NAME})")
    else:
        logger.warning("⚠️ SAMBANOVA_API_KEY missing. Using Fallback Mode.")
except ImportError:
    logger.error("❌ 'openai' library missing. Run: pip install openai")

# ==========================================
# UTILS (JSON CLEANING)
# ==========================================
def clean_json(text: str) -> Dict[str, Any]:
    """Extracts valid JSON from AI response, ignoring markdown or chatty text."""
    try:
        return json.loads(text)
    except:
        pass
    
    try:
        # Regex to find the first { ... } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except:
        pass
        
    # Return safe default if parsing fails
    return {
        "score": 5,
        "feedback": "Could not parse AI response. Keep going!",
        "improved_answer": "Try to be more specific next time.",
        "next_question": "Let's move to the next topic."
    }

def get_fallback_question(skills):
    topic = skills[0] if skills else "programming"
    return f"We are having trouble connecting to the AI. Let's stick to basics: What is {topic}?"

# ==========================================
# PROMPT LOGIC (DIFFICULTY TUNING)
# ==========================================
def get_difficulty_instruction(experience: str) -> str:
    """Returns strict instructions based on experience level."""
    exp = experience.lower()
    
    if "fresher" in exp or "0-1" in exp:
        return (
            "DIFFICULTY: BEGINNER / JUNIOR.\n"
            "Ask SIMPLE, FOUNDATIONAL questions (e.g., 'What is X?', 'Define Y').\n"
            "Do NOT ask about system design, architecture, or scaling.\n"
            "Focus on syntax, basic definitions, and core concepts."
        )
    elif "intermediate" in exp or "1-3" in exp:
        return (
            "DIFFICULTY: INTERMEDIATE.\n"
            "Ask about practical implementation, common libraries, and simple debugging.\n"
            "Avoid overly complex distributed system questions."
        )
    else:
        return (
            "DIFFICULTY: SENIOR / EXPERT.\n"
            "Ask about optimization, system design, scalability, and edge cases."
        )

# ==========================================
# ROUTES
# ==========================================
class StartReq(BaseModel):
    skills: List[str]
    experience: str

class AnswerReq(BaseModel):
    session_id: str
    question: str
    answer: str

@router.post("/mock/start")
def start_interview(req: StartReq):
    session_id = str(uuid.uuid4())
    skills_str = ", ".join(req.skills)

    # Log to DB
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", 
                 (session_id, skills_str, req.experience, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    if not client:
        return {"session_id": session_id, "first_question": get_fallback_question(req.skills)}

    # --- 1. GENERATE QUESTION ---
    difficulty_prompt = get_difficulty_instruction(req.experience)
    
    prompt = f"""
    You are a Technical Interviewer.
    Candidate Level: {req.experience}.
    Topics: {skills_str}.
    
    {difficulty_prompt}
    
    Task: Ask ONE introductory question suitable for this level.
    Constraint: Return ONLY the question text. No "Hello", no "Here is your question".
    """
    
    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=100
        )
        q = res.choices[0].message.content.strip()
        # Clean up any quotes AI might add
        q = q.replace('"', '').replace("'", "")
        return {"session_id": session_id, "first_question": q}
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return {"session_id": session_id, "first_question": get_fallback_question(req.skills)}

@router.post("/mock/answer")
def submit_answer(req: AnswerReq):
    # Retrieve session to know the experience level
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT experience, skills FROM sessions WHERE session_id=?", (req.session_id,)).fetchone()
    conn.close()
    
    experience = row["experience"] if row else "Fresher (0-1 Years)"
    skills = row["skills"] if row else "General"
    
    if not client:
        return clean_json("{}") # Returns fallback

    # --- 2. EVALUATE & NEXT QUESTION ---
    difficulty_prompt = get_difficulty_instruction(experience)

    prompt = f"""
    Role: Interview Grader.
    Context: {experience} Candidate. Topic: {skills}.
    Question: "{req.question}"
    Answer: "{req.answer}"
    
    {difficulty_prompt}
    
    Task: Evaluate the answer and return STRICT JSON:
    {{
        "score": (int 1-10, be encouraging for beginners),
        "feedback": (string, brief 1-2 sentences),
        "improved_answer": (string, simple explanation),
        "next_question": (string, ONE follow-up question at the SAME difficulty level)
    }}
    
    IMPORTANT: Output ONLY valid JSON.
    """
    
    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 # Low temp for valid JSON
        )
        data = clean_json(res.choices[0].message.content)
        
        # Save to DB
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO qa_history (session_id, question, user_answer, ai_feedback, score) VALUES (?, ?, ?, ?, ?)",
                     (req.session_id, req.question, req.answer, data.get("feedback"), data.get("score")))
        conn.commit()
        conn.close()
        
        return data

    except Exception as e:
        logger.error(f"AI Eval Error: {e}")
        return clean_json("{}") # Triggers fallback

@router.get("/mock/report/{session_id}")
def get_report(session_id: str):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT score FROM qa_history WHERE session_id=?", (session_id,)).fetchall()
    conn.close()
    
    if not rows:
        return {"final_score": 0, "tips": ["No answers recorded."]}
    
    scores = [r["score"] for r in rows]
    avg = round(sum(scores) / len(scores))
    
    return {
        "final_score": avg,
        "tips": ["Review core definitions.", "Practice STAR method.", "Clarify edge cases."],
        "history": []
    }