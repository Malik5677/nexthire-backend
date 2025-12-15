# ================================================================
# PART 1 — DB + AUTH + UTILITIES (ENGLISH HR VERSION)
# ================================================================

import os
import json

import random
import string
import sqlite3
import bcrypt
import requests
from datetime import datetime, timedelta
from hr_routes import HR
from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv
from resume_routes import router as resume_router



load_dotenv()

# ================================================================
# FASTAPI SETUP
# ================================================================

app = FastAPI(title="NextHire HR AI Backend", version="3.0")
app.include_router(HR)
app.include_router(resume_router)
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================
# EMAIL CONFIG (Gmail SMTP)
# ================================================================

conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=587,
    MAIL_SERVER="smtp.gmail.com",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
)

# ================================================================
# PASSWORD HASHING
# ================================================================

def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str):
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except:
        return False

# ================================================================
# JWT CONFIG
# ================================================================

try:
    import jwt as pyjwt
    JWT_BACKEND = "pyjwt"
except:
    from jose import jwt as pyjwt
    JWT_BACKEND = "jose"

SECRET_KEY = os.getenv("SECRET_KEY", "DEFAULT_SECRET_KEY")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MIN = 5000

def create_token(data: dict):
    data = data.copy()
    data["exp"] = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MIN)
    return pyjwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

# ================================================================
# CAPTCHA VERIFY
# ================================================================

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET")

def verify_captcha_token(token: str):
    if not token:
        raise HTTPException(400, "Captcha missing")

    try:
        res = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": RECAPTCHA_SECRET, "response": token},
        ).json()
        if not res.get("success"):
            raise HTTPException(400, "Captcha failed")
        return True
    except:
        raise HTTPException(500, "Captcha server issue")

# ================================================================
# DATABASE INIT
# ================================================================

DB_NAME = "users.db"

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE,
            phone TEXT,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            email TEXT NOT NULL,
            purpose TEXT NOT NULL,
            code TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY (email, purpose)
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ================================================================
# REQUEST MODELS
# ================================================================

class OTPSendRequest(BaseModel):
    email: EmailStr
    purpose: str = "signup"

class OTPVerify(BaseModel):
    email: EmailStr
    otp: str
    password: str
    role: str = "candidate"

class UserLogin(BaseModel):
    login_identifier: str
    password: str

router = APIRouter()

# ================================================================
# ROOT TEST ENDPOINT
# ================================================================

@router.get("/")
def root():
    return {"message": "NextHire API (English Version) is running"}

# ================================================================
# SEND OTP
# ================================================================

@router.post("/send-otp")
async def send_otp(payload: OTPSendRequest):
    email = payload.email.lower().strip()
    otp = "".join(random.choices(string.digits, k=6))
    expires = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM otps WHERE email=? AND purpose=?", (email, payload.purpose))
        cur.execute(
            "INSERT INTO otps(email, purpose, code, expires_at) VALUES (?, ?, ?, ?)",
            (email, payload.purpose, otp, expires),
        )
        conn.commit()

    msg = MessageSchema(
        subject="Your NextHire OTP",
        recipients=[email],
        body=f"Your OTP is {otp}",
        subtype=MessageType.plain,
    )

    try:
        await FastMail(conf).send_message(msg)
    except Exception as e:
        print("EMAIL ERROR:", e)

    return {"message": "OTP sent successfully"}

# ================================================================
# VERIFY SIGNUP
# ================================================================

@router.post("/verify-signup")
async def verify_signup(data: OTPVerify):
    email = data.email.lower().strip()

    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()

        cur.execute("SELECT code, expires_at FROM otps WHERE email=? AND purpose='signup'", (email,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(400, "OTP not found")

        db_otp, exp = row
        if datetime.utcnow() > datetime.fromisoformat(exp):
            raise HTTPException(400, "OTP expired")

        if db_otp != data.otp:
            raise HTTPException(400, "Invalid OTP")

        base = email.split("@")[0]
        username = base
        n = 1

        while True:
            cur.execute("SELECT id FROM users WHERE username=?", (username,))
            if not cur.fetchone():
                break
            username = f"{base}{n}"
            n += 1

        hashed_pw = hash_password(data.password)

        cur.execute(
            "INSERT INTO users(email, username, password, role) VALUES (?, ?, ?, ?)",
            (email, username, hashed_pw, data.role),
        )
        cur.execute("DELETE FROM otps WHERE email=? AND purpose='signup'", (email,))
        conn.commit()

    token = create_token({"sub": email, "role": data.role})

    return {
        "message": "Signup successful",
        "token": token,
        "email": email,
        "username": username
    }

# ================================================================
# LOGIN
# ================================================================

@router.post("/login")
def login(data: UserLogin):
    identifier = data.login_identifier.strip()

    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT email, username, phone, password, role
            FROM users
            WHERE email=? OR username=?
        """, (identifier, identifier))

        row = cur.fetchone()

        if not row:
            raise HTTPException(401, "User not found")

        email, username, phone, hashed_pw, role = row

        if not verify_password(data.password, hashed_pw):
            raise HTTPException(401, "Incorrect password")

    token = create_token({"sub": email, "role": role})

    return {
        "message": "Login successful",
        "token": token,
        "email": email,
        "username": username,
        "phone": phone or "",
        "role": role
    }

app.include_router(router)
# ================================================================
# PART 2 — SAMBANOVA AI ENGINE (ENGLISH HR INTERVIEW SYSTEM)
# ================================================================

import asyncio
import json
from typing import List
import requests
import os

# ================================================================
# SambaNova API Setup
# ================================================================

SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")
SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1/chat/completions"

if not SAMBANOVA_API_KEY:
    print("❌ ERROR: SAMBANOVA_API_KEY missing in .env")


# ================================================================
# SambaNova Chat Completion Caller
# ================================================================

def sn_generate(messages: List[dict], max_tokens=300, temperature=0.7):
    """
    Safely calls SambaNova Llama-3.1-8B and returns assistant text.
    Includes timeout, error fallback, safe extraction.
    """

    payload = {
        "model": "Meta-Llama-3.1-8B-Instruct",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {SAMBANOVA_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            SAMBANOVA_BASE_URL,
            headers=headers,
            json=payload,
            timeout=25
        )
        data = response.json()
        return data["choices"][0]["message"]["content"]

    except Exception as e:
        print("[SambaNova Error]", e)
        return "I couldn't process that. Please try again."


# ================================================================
# Role-Based Initial English Questions
# ================================================================

ROLE_QUESTIONS = {
    "software engineer": [
        "Can you explain your exact responsibilities in your last software project?",
        "Explain object-oriented programming in simple terms.",
        "Describe your debugging process step-by-step.",
        "What is the difference between REST and GraphQL?",
        "How do you collaborate with your development team?"
    ],
    "web developer": [
        "Which frontend frameworks have you used, and why?",
        "What is the difference between stateful and stateless components?",
        "How do you improve website performance?",
        "Explain responsive design principles.",
        "Describe the browser rendering pipeline."
    ],
    "data analyst": [
        "What is the difference between correlation and causation?",
        "How do you handle missing data?",
        "Which visualization tools have you used?",
        "Describe an end-to-end analytics project you completed.",
        "How do you choose KPIs for a business problem?"
    ],
    "general": [
        "Tell me about yourself.",
        "Why should we hire you?",
        "What are your strengths and weaknesses?",
        "Where do you see yourself in two years?",
        "Describe a challenging situation you handled recently."
    ]
}

def get_initial_question(role: str):
    r = role.lower().strip()
    return ROLE_QUESTIONS.get(r, ROLE_QUESTIONS["general"])[0]


# ================================================================
# Evaluate Candidate Answer (Tech + Grammar + Posture)
# ================================================================

async def evaluate_answer(question: str, answer: str, posture_label: str):
    """
    Uses SambaNova to evaluate the user's answer.
    Returns structured JSON with scores, feedback, improved answer.
    """

    prompt = f"""
You are a senior HR interviewer evaluating a candidate in English.

Return ONLY valid JSON in this format:

{{
  "technical_score": 0-10,
  "grammar_score": 0-10,
  "posture_score": 0-10,
  "grammar_mistakes": ["mistake1", "mistake2"],
  "feedback": "3-4 lines of HR-style feedback",
  "improved_answer": "A clearer, more polished version of their answer"
}}

Question: {question}
Candidate Answer: {answer}
Posture Label Detected: {posture_label}
"""

    raw = sn_generate(
        [{"role": "user", "content": prompt}],
        max_tokens=400
    )

    # Attempt safe JSON extraction
    try:
        json_data = raw[raw.index("{"): raw.rindex("}") + 1]
        return json.loads(json_data)

    except Exception:
        print("[EVALUATION JSON ERROR] RAW OUTPUT:", raw)
        return {
            "technical_score": 6,
            "grammar_score": 6,
            "posture_score": 6,
            "grammar_mistakes": [],
            "feedback": "Good attempt, but your explanation could be more detailed and structured.",
            "improved_answer": "Try explaining step-by-step and focusing on clarity."
        }


# ================================================================
# Generate Follow-Up English HR Question
# ================================================================

async def generate_next_question(role: str, prev_answer: str):
    """
    Creates a natural follow-up question based on the previous answer.
    Must be:
    - English
    - Professional HR tone
    - Only ONE question
    - No explanations or extra text
    """

    prompt = f"""
You are a professional English HR interviewer.

Candidate's previous answer:
{prev_answer}

Rules:
- Ask ONLY ONE follow-up question.
- Keep it relevant and specific.
- Keep it professional.
- Do NOT repeat earlier questions.
- Do NOT include explanations.

Return ONLY the question text.
"""

    q = sn_generate(
        [{"role": "user", "content": prompt}],
        max_tokens=80
    )

    return q.strip()


# ================================================================
# Final HR Report (Generated ONLY at end of interview)
# ================================================================

async def generate_report(history):
    """
    Generates a professional final HR report summarizing the interview.
    """

    prompt = f"""
You are a senior HR manager.

Here is the full interview history:
{json.dumps(history, indent=2)}

Create a FINAL REPORT summarizing performance.

Return ONLY JSON:

{{
  "score": 0-100,
  "confidence_level": "High" | "Medium" | "Low",
  "posture_summary": "Short posture summary",
  "grammar_summary": "Short grammar summary",
  "feedback": "3-4 lines summarizing overall performance",
  "improvements": ["tip1", "tip2", "tip3"],
  "spoken_summary": "A short final message the AI will speak aloud"
}}
"""

    raw = sn_generate(
        [{"role": "user", "content": prompt}],
        max_tokens=400
    )

    try:
        json_data = raw[raw.index("{"): raw.rindex("}") + 1]
        return json.loads(json_data)

    except Exception:
        print("[REPORT JSON ERROR] RAW:", raw)
        return {
            "score": 72,
            "confidence_level": "Medium",
            "posture_summary": "Good posture overall with minor inconsistencies.",
            "grammar_summary": "Grammar mostly correct with occasional mistakes.",
            "feedback": "Your answers were well-structured and clear. Some improvements in detail and precision can elevate your performance.",
            "improvements": [
                "Increase clarity in explanations",
                "Be more specific in technical responses",
                "Maintain consistent posture"
            ],
            "spoken_summary": "This concludes your interview. Your report has been generated."
        }
# ================================================================
# PART 3 — WEBSOCKET ENGINE + ENGLISH MP3 HR VOICE (FINAL VERSION)
# ================================================================

from fastapi import WebSocket, WebSocketDisconnect
from gtts import gTTS
import tempfile
import traceback
import json


# ================================================================
# English HR Voice — MP3 Generator
# ================================================================

def generate_voice_mp3(text: str):
    """
    Generate English MP3 voice using gTTS (male-ish AU accent).
    No pydub, no WAV, no ffmpeg.
    100% supported by browsers.
    """
    try:
        tts = gTTS(text=text, lang="en", tld="com.au")  # deeper tone
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tts.save(tmp.name)

        with open(tmp.name, "rb") as f:
            return f.read()

    except Exception as e:
        print("[VOICE ERROR]", e)
        return None


# ================================================================
# REAL-TIME INTERVIEW ENGINE (WebSocket)
# ================================================================

@app.websocket("/ws/interview")
async def interview_ws(ws: WebSocket):

    await ws.accept()
    print("🟢 WebSocket Connected")

    interview_state = "idle"
    role = "general"
    history = []
    last_question = None

    try:
        while True:

            # Receive WebSocket message
            raw = await ws.receive()

            if raw["type"] == "websocket.disconnect":
                print("🔴 WebSocket disconnected")
                break

            if not raw.get("text"):
                continue

            data = json.loads(raw["text"])
            action = data.get("action")

            # ============================================================
            # START INTERVIEW
            # ============================================================
            if action == "start":
                interview_state = "active"
                role = data.get("role", "general")

                greeting = (
                    "Hello! I am Christopher, your AI Technical HR interviewer. "
                    "Let's begin. Here is your first question."
                )

                # TEXT OUT
                await ws.send_text(json.dumps({
                    "type": "text_response",
                    "content": greeting
                }))

                # AUDIO OUT
                audio_greet = generate_voice_mp3(greeting)
                if audio_greet:
                    await ws.send_bytes(audio_greet)

                # FIRST QUESTION
                last_question = get_initial_question(role)

                await ws.send_text(json.dumps({
                    "type": "text_response",
                    "content": last_question
                }))

                q_audio = generate_voice_mp3(last_question)
                if q_audio:
                    await ws.send_bytes(q_audio)

                continue

            # ============================================================
            # PROCESS ANSWER
            # ============================================================
            if action == "answer" and interview_state == "active":

                user_ans = data.get("text", "")
                posture = data.get("visual_context", {}).get("posture", "Unknown")

                # Evaluate using Part 2 engine
                eval_data = await evaluate_answer(last_question, user_ans, posture)

                # Save history for final report
                history.append({
                    "question": last_question,
                    "answer": user_ans,
                    "scores": {
                        "technical": eval_data["technical_score"],
                        "grammar": eval_data["grammar_score"],
                        "posture": eval_data["posture_score"],
                    },
                    "improved_answer": eval_data["improved_answer"]
                })

                # SOFT FEEDBACK — NO SCORES
                feedback_text = eval_data["feedback"]

                await ws.send_text(json.dumps({
                    "type": "text_response",
                    "content": feedback_text
                }))

                fb_audio = generate_voice_mp3(feedback_text)
                if fb_audio:
                    await ws.send_bytes(fb_audio)

                # Generate next HR question
                next_q = await generate_next_question(role, user_ans)
                last_question = next_q

                await ws.send_text(json.dumps({
                    "type": "text_response",
                    "content": next_q
                }))

                nq_audio = generate_voice_mp3(next_q)
                if nq_audio:
                    await ws.send_bytes(nq_audio)

                continue

            # ============================================================
            # END INTERVIEW
            # ============================================================
            if action == "end":
                interview_state = "finished"

                # Generate final report
                report = await generate_report(history)

                summary = report["spoken_summary"]

                await ws.send_text(json.dumps({
                    "type": "text_response",
                    "content": summary
                }))

                s_audio = generate_voice_mp3(summary)
                if s_audio:
                    await ws.send_bytes(s_audio)

                # Send full JSON report
                await ws.send_text(json.dumps({
                    "type": "report",
                    "content": json.dumps(report)
                }))

                print("🟡 Interview Ended")
                break

    except WebSocketDisconnect:
        print("🔴 WebSocket Lost")

    except Exception as e:
        print("[WEBSOCKET ERROR]", e)
        traceback.print_exc()
# ================================================================
# PART 4 — HR DASHBOARD ENDPOINTS (CANDIDATE LIST + PROFILE + RESUME)
# ================================================================

from fastapi import UploadFile, File
from fastapi.responses import FileResponse

UPLOAD_FOLDER = "uploads/resumes"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ------------------------------------------------------------
# SAVE RESUME (Candidate uploads resume from frontend)
# ------------------------------------------------------------
@app.post("/upload-resume")
async def upload_resume(email: str, file: UploadFile = File(...)):
    filename = f"{email.replace('@','_')}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    with open(filepath, "wb") as f:
        f.write(await file.read())

    # Update DB
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("ALTER TABLE users ADD COLUMN resume TEXT") if True else None
    cur.execute("UPDATE users SET resume=? WHERE email=?", (filename, email))
    conn.commit()
    conn.close()

    return {"message": "Resume uploaded", "filename": filename}


# ------------------------------------------------------------
# GET RESUME FILE
# ------------------------------------------------------------
@app.get("/resume/{filename}")
def get_resume(filename: str):
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(filepath):
        raise HTTPException(404, "Resume not found")
    return FileResponse(filepath)


# ------------------------------------------------------------
# CANDIDATE LIST FOR HR DASHBOARD
# ------------------------------------------------------------
@app.get("/candidates")
def get_all_candidates():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT email, username, phone, role, resume
        FROM users
    """)
    users = cur.fetchall()

    # Fetch interview scores
    candidates = []
    for email, username, phone, role, resume in users:

        # Load reports saved during Interview Coach
        # (We need to add this table if not exists)
        try:
            cur.execute("""
                SELECT score, date FROM interviews WHERE user_email=?
                ORDER BY date DESC LIMIT 1
            """, (email,))
            row = cur.fetchone()
            score = row[0] if row else None
            last_date = row[1] if row else None

        except:
            score = None
            last_date = None

        candidates.append({
            "email": email,
            "name": username,
            "phone": phone,
            "role": role,
            "resume": resume,
            "score": score,
            "last_interview": last_date,
            "ready": True if score and score >= 75 else False
        })

    conn.close()

    # Sort best candidates first
    candidates.sort(key=lambda x: (x["score"] or 0), reverse=True)

    return {"candidates": candidates}


# ------------------------------------------------------------
# GET FULL CANDIDATE PROFILE
# ------------------------------------------------------------
@app.get("/candidate/{email}")
def get_candidate_profile(email: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT email, username, phone, role, resume
        FROM users WHERE email=?
    """, (email,))
    user = cur.fetchone()

    if not user:
        raise HTTPException(404, "Candidate not found")

    email, username, phone, role, resume = user

    # Fetch interview history
    cur.execute("""
        SELECT score, tips, posture_score, grammar_score, communication_score, date
        FROM interviews WHERE user_email=?
        ORDER BY date DESC
    """, (email,))
    reports = cur.fetchall()

    conn.close()

    return {
        "email": email,
        "name": username,
        "phone": phone,
        "role": role,
        "resume": resume,
        "reports": [
            {
                "score": r[0],
                "tips": r[1],
                "posture": r[2],
                "grammar": r[3],
                "communication": r[4],
                "date": r[5]
            }
            for r in reports
        ]
    }
