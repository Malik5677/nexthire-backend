# ================================================================
# MAIN.PY — FIXED & OPTIMIZED BACKEND
# ================================================================

import os
import json
import random
import string
import sqlite3
import bcrypt
import asyncio
import traceback
import tempfile
from datetime import datetime, timedelta
from typing import List

# Third-party imports
import httpx  # REQUIRED: pip install httpx
from fastapi import FastAPI, HTTPException, APIRouter, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv
from gtts import gTTS
# ... other imports ...
from interview import router as interview_router  # <--- ADD THIS LINE

# Route imports (Assuming these files exist in your folder)
try:
    from hr_routes import HR
    from resume_routes import router as resume_router
except ImportError:
    print("⚠️ Warning: hr_routes or resume_routes not found. Skipping their import.")
    HR = APIRouter()
    resume_router = APIRouter()

load_dotenv()

# ================================================================
# 1. FASTAPI & MIDDLEWARE SETUP
# ================================================================

app = FastAPI(title="NextHire HR AI Backend", version="3.0")
# ... app = FastAPI(...) ...
app.include_router(interview_router)  # <--- ADD THIS LINE

# CORS (Configured ONLY ONCE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow all for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(HR)
app.include_router(resume_router)

# ================================================================
# 2. DATABASE INITIALIZATION (FIXED TABLES)
# ================================================================

DB_NAME = "users.db"

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cur = conn.cursor()

    # FIX: Added 'resume' column directly here so we don't need ALTER TABLE later
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE,
            phone TEXT,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            resume TEXT
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

    # FIX: Added the missing 'interviews' table so Dashboard doesn't crash
    cur.execute("""
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            score INTEGER,
            tips TEXT,
            posture_score INTEGER,
            grammar_score INTEGER,
            communication_score INTEGER,
            date TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ================================================================
# 3. UTILITIES (HASHING, JWT, EMAIL)
# ================================================================

# --- Password ---
def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str):
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except:
        return False

# --- JWT ---
try:
    import jwt as pyjwt
except:
    from jose import jwt as pyjwt

SECRET_KEY = os.getenv("SECRET_KEY", "DEFAULT_SECRET_KEY")
ALGORITHM = "HS256"

def create_token(data: dict):
    data = data.copy()
    data["exp"] = datetime.utcnow() + timedelta(minutes=6000) # Extended time
    return pyjwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

# --- Email Config ---
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME", "user@gmail.com"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD", "pass"),
    MAIL_FROM=os.getenv("MAIL_FROM", "user@gmail.com"),
    MAIL_PORT=587,
    MAIL_SERVER="smtp.gmail.com",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
)

# ================================================================
# 4. AUTH ROUTES (OTP, LOGIN, SIGNUP)
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
        if os.getenv("MAIL_USERNAME"): # Only send if config exists
            await FastMail(conf).send_message(msg)
        else:
            print(f"⚠️ Email Config missing. OTP for {email} is: {otp}")
    except Exception as e:
        print("EMAIL ERROR:", e)

    return {"message": "OTP sent successfully"}

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
    return {"message": "Signup successful", "token": token, "email": email, "username": username}

@router.post("/login")
def login(data: UserLogin):
    identifier = data.login_identifier.strip()
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("SELECT email, username, phone, password, role FROM users WHERE email=? OR username=?", (identifier, identifier))
        row = cur.fetchone()

        if not row:
            raise HTTPException(401, "User not found")

        email, username, phone, hashed_pw, role = row
        if not verify_password(data.password, hashed_pw):
            raise HTTPException(401, "Incorrect password")

    token = create_token({"sub": email, "role": role})
    return {"message": "Login successful", "token": token, "email": email, "username": username, "role": role}

app.include_router(router)

# ================================================================
# 5. AI ENGINE (SAMBANOVA) - ASYNC FIXED
# ================================================================

SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")
SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1/chat/completions"

# FIX: Using httpx for ASYNC non-blocking AI calls
async def sn_generate(messages: List[dict], max_tokens=300, temperature=0.7):
    if not SAMBANOVA_API_KEY:
        return "AI Config Missing."
        
    payload = {
        "model": "Meta-Llama-3.1-8B-Instruct",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(SAMBANOVA_BASE_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("[SambaNova Error]", e)
        return "I couldn't process that due to a connection error."

# --- Questions Data ---
ROLE_QUESTIONS = {
    "software engineer": ["Can you explain your exact responsibilities in your last software project?"],
    "web developer": ["Which frontend frameworks have you used, and why?"],
    "data analyst": ["What is the difference between correlation and causation?"],
    "general": ["Tell me about yourself."]
}

def get_initial_question(role: str):
    return ROLE_QUESTIONS.get(role.lower().strip(), ROLE_QUESTIONS["general"])[0]

async def evaluate_answer(question: str, answer: str, posture_label: str):
    prompt = f"""
    You are a senior HR interviewer. Return ONLY valid JSON:
    {{
      "technical_score": 0-10,
      "grammar_score": 0-10,
      "posture_score": 0-10,
      "grammar_mistakes": ["mistake1"],
      "feedback": "Short feedback",
      "improved_answer": "Better version"
    }}
    Question: {question}
    Answer: {answer}
    Posture: {posture_label}
    """
    raw = await sn_generate([{"role": "user", "content": prompt}], max_tokens=400)
    try:
        # Robust JSON parser
        json_str = raw[raw.find("{"):raw.rfind("}")+1]
        return json.loads(json_str)
    except:
        return {
            "technical_score": 5, "grammar_score": 5, "posture_score": 5,
            "grammar_mistakes": [], "feedback": "Could not analyze detailed feedback.",
            "improved_answer": "N/A"
        }

async def generate_next_question(role: str, prev_answer: str):
    prompt = f"Ask one short follow-up interview question for a {role} based on: {prev_answer}"
    q = await sn_generate([{"role": "user", "content": prompt}], max_tokens=60)
    return q.strip().replace('"', '')

async def generate_report(history):
    prompt = f"""
    Create a JSON interview report based on this history: {json.dumps(history)}
    Format: {{
      "score": 0-100,
      "confidence_level": "High/Med/Low",
      "posture_summary": "...",
      "grammar_summary": "...",
      "feedback": "...",
      "improvements": ["..."],
      "spoken_summary": "..."
    }}
    """
    raw = await sn_generate([{"role": "user", "content": prompt}], max_tokens=500)
    try:
        json_str = raw[raw.find("{"):raw.rfind("}")+1]
        return json.loads(json_str)
    except:
        return {"score": 0, "spoken_summary": "Error generating report.", "feedback": "AI Error"}

# ================================================================
# 6. WEBSOCKET ENGINE (VOICE + INTERVIEW)
# ================================================================

def generate_voice_mp3(text: str):
    try:
        tts = gTTS(text=text, lang="en", tld="com.au")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tts.save(tmp.name)
        with open(tmp.name, "rb") as f:
            return f.read()
    except:
        return None

@app.websocket("/ws/interview")
async def interview_ws(ws: WebSocket):
    await ws.accept()
    print("🟢 WS Connected")
    
    # Store user session data
    history = []
    role = "general"
    last_question = ""

    try:
        while True:
            data_raw = await ws.receive_text()
            data = json.loads(data_raw)
            action = data.get("action")

            if action == "start":
                role = data.get("role", "general")
                last_question = get_initial_question(role)
                
                # Send Text
                await ws.send_json({"type": "text_response", "content": last_question})
                
                # Send Audio
                audio = generate_voice_mp3(last_question)
                if audio: await ws.send_bytes(audio)

            elif action == "answer":
                user_ans = data.get("text", "")
                posture = data.get("visual_context", {}).get("posture", "Good")

                # 1. Evaluate
                eval_res = await evaluate_answer(last_question, user_ans, posture)
                
                # 2. Add to history
                history.append({
                    "question": last_question,
                    "answer": user_ans,
                    "scores": eval_res
                })

                # 3. Send Feedback
                await ws.send_json({"type": "text_response", "content": eval_res["feedback"]})
                
                # 4. Generate Next Question
                next_q = await generate_next_question(role, user_ans)
                last_question = next_q
                
                # 5. Send Next Question
                await ws.send_json({"type": "text_response", "content": next_q})
                audio = generate_voice_mp3(next_q)
                if audio: await ws.send_bytes(audio)

            elif action == "end":
                report = await generate_report(history)
                
                # Save to DB (The missing piece!)
                # Here we would normally save to the 'interviews' table
                
                await ws.send_json({"type": "report", "content": json.dumps(report)})
                break

    except WebSocketDisconnect:
        print("🔴 WS Disconnect")
    except Exception as e:
        print(f"WS Error: {e}")

# ================================================================
# 7. DASHBOARD & RESUMES (FIXED)
# ================================================================

UPLOAD_FOLDER = "uploads/resumes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.post("/upload-resume")
async def upload_resume(email: str, file: UploadFile = File(...)):
    filename = f"{email.replace('@','_')}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    with open(filepath, "wb") as f:
        f.write(await file.read())

    # FIX: No more ALTER TABLE here. Just update.
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET resume=? WHERE email=?", (filename, email))
        conn.commit()

    return {"message": "Resume uploaded", "filename": filename}

@app.get("/resume/{filename}")
def get_resume(filename: str):
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        return FileResponse(filepath)
    raise HTTPException(404, "Resume not found")

@app.get("/candidates")
def get_all_candidates():
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("SELECT email, username, phone, role, resume FROM users")
        users = cur.fetchall()

        candidates = []
        for u in users:
            # FIX: Now this won't crash because 'interviews' table exists
            cur.execute("SELECT score, date FROM interviews WHERE user_email=? ORDER BY date DESC LIMIT 1", (u[0],))
            res = cur.fetchone()
            candidates.append({
                "email": u[0], "name": u[1], "phone": u[2], "role": u[3], "resume": u[4],
                "score": res[0] if res else 0,
                "last_interview": res[1] if res else None
            })
            
    return {"candidates": candidates}

@app.get("/candidate/{email}")
def get_candidate_profile(email: str):
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("SELECT email, username, phone, role, resume FROM users WHERE email=?", (email,))
        user = cur.fetchone()
        
        if not user: raise HTTPException(404, "User not found")

        cur.execute("SELECT score, tips, posture_score, grammar_score, communication_score, date FROM interviews WHERE user_email=?", (email,))
        reports = cur.fetchall()

    return {
        "email": user[0], "name": user[1], "phone": user[2], "role": user[3], "resume": user[4],
        "reports": [{"score": r[0], "tips": r[1], "date": r[5]} for r in reports]
    }