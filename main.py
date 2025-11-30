import sqlite3
import os  # <--- REQUIRED to read Render secrets
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import bcrypt
import random
import string
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
import jwt
from datetime import datetime, timedelta

# --- IMPORT THE RESUME ROUTER ---
# Make sure resume_routes.py is in the same folder
from resume_routes import router as resume_router 

app = FastAPI()

# --- SECURITY CONFIGURATION ---
SECRET_KEY = "hireiq_super_secret_key_change_this"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 3000

# --- CORS MIDDLEWARE ---
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://nexthire-app.netlify.app"  # Your Netlify URL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- REGISTER THE RESUME ROUTER ---
app.include_router(resume_router)

# --- EMAIL CONFIGURATION (UPDATED FOR BREVO) ---
# This uses Port 587 which works with Brevo/Sendinblue
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"), # Reads from Render Environment
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"), # Reads from Render Environment
    MAIL_FROM=os.getenv("MAIL_FROM"),         # Reads from Render Environment
    MAIL_PORT=587,                            # <--- BREVO USES 587
    MAIL_SERVER="smtp-relay.brevo.com",       # <--- BREVO SERVER
    MAIL_STARTTLS=True,                       # <--- TRUE for Brevo
    MAIL_SSL_TLS=False,                       # <--- FALSE for Brevo
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)

# --- DATABASE ---
DB_NAME = "users.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        # Create table with new columns if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE,
                phone TEXT,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)
        
        # Try to add columns if they are missing (for existing dbs)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN username TEXT UNIQUE")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        except:
            pass

        # OTP table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS otps (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL
            )
        """)
        conn.commit()

init_db()

# --- MODELS ---
class UserUpdate(BaseModel):
    original_email: str 
    new_username: str = None
    new_email: str = None
    new_phone: str = None

class OTPVerify(BaseModel):
    email: EmailStr
    otp: str
    password: str = ""
    role: str = "candidate"

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordVerify(BaseModel):
    email: EmailStr
    otp: str
    new_password: str

class UserLogin(BaseModel):
    login_identifier: str 
    password: str

# --- HELPERS ---
def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# --- API ENDPOINTS ---

@app.post("/send-otp")
async def send_otp(payload: ForgotPasswordRequest):
    email = payload.email
    otp_code = generate_otp()
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO otps (email, code) VALUES (?, ?)", (email, otp_code))
        conn.commit()

    message = MessageSchema(
        subject="NextHire - Verification Code",
        recipients=[email],
        body=f"Your NextHire verification code is: {otp_code}",
        subtype=MessageType.plain
    )
    
    fm = FastMail(conf)
    try:
        await fm.send_message(message)
        return {"message": "OTP sent successfully"}
    except Exception as e:
        print(f"Mail Error: {e}")
        # Raise 500 so frontend knows it failed
        raise HTTPException(status_code=500, detail="Failed to send email")

@app.post("/verify-signup")
def verify_signup(data: OTPVerify):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT code FROM otps WHERE email = ?", (data.email,))
        record = cursor.fetchone()
        
        if not record or record[0] != data.otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")
            
        hashed_pw = get_password_hash(data.password)
        default_username = data.email.split("@")[0]
        
        try:
            cursor.execute(
                "INSERT INTO users (email, username, password, role) VALUES (?, ?, ?, ?)",
                (data.email, default_username, hashed_pw, data.role)
            )
            cursor.execute("DELETE FROM otps WHERE email = ?", (data.email,))
            conn.commit()
            
            token = create_access_token(data={"sub": data.email, "role": data.role})
            return {
                "message": "User created", 
                "access_token": token, 
                "role": data.role,
                "username": default_username,
                "phone": ""
            }
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email or Username already taken")

@app.post("/login")
def login(user: UserLogin):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT email, username, phone, password, role 
            FROM users 
            WHERE email = ? OR username = ?
        """, (user.login_identifier, user.login_identifier))
        
        record = cursor.fetchone()
        
        if not record:
            raise HTTPException(status_code=400, detail="Invalid credentials")
        
        email, username, phone, stored_hash, role = record
        
        if not verify_password(user.password, stored_hash):
            raise HTTPException(status_code=400, detail="Invalid credentials")
            
        token = create_access_token(data={"sub": email, "role": role})
        return {
            "message": "Login successful", 
            "access_token": token, 
            "role": role, 
            "email": email,
            "username": username,
            "phone": phone or ""
        }

@app.post("/update-profile")
def update_profile(data: UserUpdate):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        if data.new_username:
            cursor.execute("SELECT id FROM users WHERE username = ? AND email != ?", (data.new_username, data.original_email))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="Username already taken")

        if data.new_email and data.new_email != data.original_email:
            cursor.execute("SELECT id FROM users WHERE email = ?", (data.new_email,))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="Email already registered")

        try:
            cursor.execute("""
                UPDATE users 
                SET username = ?, email = ?, phone = ? 
                WHERE email = ?
            """, (data.new_username, data.new_email, data.new_phone, data.original_email))
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="User not found")
                
            conn.commit()
            return {"message": "Profile updated successfully"}
        except sqlite3.IntegrityError:
             raise HTTPException(status_code=400, detail="Constraint error")

@app.post("/reset-password")
def reset_password(data: ResetPasswordVerify):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT code FROM otps WHERE email = ?", (data.email,))
        record = cursor.fetchone()
        if not record or record[0] != data.otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")
        
        cursor.execute("SELECT id FROM users WHERE email = ?", (data.email,))
        if not cursor.fetchone():
             raise HTTPException(status_code=404, detail="User not found")

        hashed_pw = get_password_hash(data.new_password)
        cursor.execute("UPDATE users SET password = ? WHERE email = ?", (hashed_pw, data.email))
        cursor.execute("DELETE FROM otps WHERE email = ?", (data.email,))
        conn.commit()
        return {"message": "Password reset successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
