from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from pypdf import PdfReader
import io
import json
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- CONFIGURATION ---
# 🔒 SECURITY FIX: Get key from Environment Variable
API_KEY = os.getenv("SAMBANOVA_API_KEY")

# 🛑 SAFETY CHECK: If no key is found, stop immediately.
if not API_KEY:
    raise ValueError("CRITICAL ERROR: SAMBANOVA_API_KEY is missing. Please add it to your .env file or Render Environment Variables.")

BASE_URL = "https://api.sambanova.ai/v1"
MODEL_NAME = "Meta-Llama-3.1-8B-Instruct" 

# Initialize the Router
router = APIRouter()

# Initialize AI Client with Timeout
client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=120.0)

# --- MODELS ---
class AnalysisResponse(BaseModel):
    score: int
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    tips: list[str]
    missing_keywords: list[str]

# --- HELPER FUNCTION ---
def extract_text_from_pdf(file_bytes):
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid PDF file")

# --- API ENDPOINT ---
@router.post("/analyze-resume", response_model=AnalysisResponse)
async def analyze_resume(file: UploadFile = File(...)):
    # 1. Validate File
    if file.content_type not in ["application/pdf", "application/x-pdf"]:
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # 2. Read Content
    content = await file.read()
    resume_text = extract_text_from_pdf(content)

    # 3. System Prompt
    system_prompt = """
    You are a Senior Technical Recruiter. Analyze the resume for a modern Full Stack Developer role.
    
    CRITICAL: Return ONLY a raw JSON object. Do not write markdown code blocks.
    
    JSON Structure:
    {
        "score": (integer 0-100 based on ATS best practices),
        "summary": "A 2-sentence professional summary of the candidate.",
        "strengths": ["strength1", "strength2", "strength3"],
        "weaknesses": ["weakness1", "weakness2"],
        "tips": ["specific actionable tip 1", "specific actionable tip 2"],
        "missing_keywords": ["keyword1", "keyword2", "keyword3"] 
    }
    """
    
    # 4. Call AI (SambaNova)
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this resume:\n{resume_text[:6000]}"},
            ],
            temperature=0.1,
            stream=False
        )
        
        raw_content = response.choices[0].message.content
        
        # ✅ BUG FIX: Correctly remove Markdown code blocks
        cleaned_json = raw_content.replace("``````", "").strip()
        
        return json.loads(cleaned_json)

    except Exception as e:
        print(f"AI Error: {e}")
        raise HTTPException(status_code=500, detail=f"AI Analysis failed: {str(e)}")
