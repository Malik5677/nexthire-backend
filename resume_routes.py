# =====================================================
# resume_routes.py — FIXED CONNECTION & CONSISTENT SCORES
# =====================================================
import os, json, re
from fastapi import APIRouter, UploadFile, File, HTTPException
from pypdf import PdfReader
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# FIX 1: NO prefix. This ensures the URL is http://127.0.0.1:8000/analyze-resume
router = APIRouter(tags=["Resume Analyzer"])

# AI CONFIG
AI_API_KEY = os.getenv("SAMBANOVA_API_KEY")
client = None
if AI_API_KEY:
    try:
        client = OpenAI(api_key=AI_API_KEY, base_url="https://api.sambanova.ai/v1")
    except:
        pass

def extract_text(file):
    try:
        reader = PdfReader(file.file)
        text = "".join([p.extract_text() or "" for p in reader.pages])
        return text.strip()
    except:
        return ""

def parse_json_safely(text):
    """ Tries to find JSON in the AI response. """
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except:
        pass
    return None

# FIX 2: Route matches the frontend exactly ("/analyze-resume")
@router.post("/analyze-resume")
async def analyze_resume(file: UploadFile = File(...)):
    print(f"📥 Processing Resume: {file.filename}") # Debug print

    # 1. Read PDF
    text = extract_text(file)
    if not text:
        raise HTTPException(400, "PDF is empty or could not be read.")

    # 2. AI Processing
    analysis = None
    if client:
        try:
            prompt = f"""
            You are a strict ATS Resume Scanner. 
            Analyze this resume text and return ONLY valid JSON.
            
            JSON Format:
            {{
                "ats_score": <integer_0_to_100>,
                "strengths": ["<strength_1>", "<strength_2>"],
                "weaknesses": ["<weakness_1>", "<weakness_2>"],
                "missing_keywords": ["<keyword_1>", "<keyword_2>"],
                "summary": "<short_summary>"
            }}
            
            Resume Text:
            {text[:3500]}
            """
            
            response = client.chat.completions.create(
                model="Meta-Llama-3.1-8B-Instruct",
                messages=[
                    {"role": "system", "content": "You are a precise data extractor. Output JSON only."},
                    {"role": "user", "content": prompt}
                ],
                # FIX 3: Temperature 0.1 makes the AI consistent (Scores won't vary wildly)
                temperature=0.1,
                max_tokens=1000
            )
            
            raw_content = response.choices[0].message.content
            analysis = parse_json_safely(raw_content)
            
        except Exception as e:
            print(f"❌ AI Error: {e}")

    # 3. Fallback (If AI fails, don't crash)
    if not analysis:
        analysis = {
            "ats_score": 0,
            "strengths": ["Could not analyze resume"],
            "weaknesses": ["Please try again"],
            "missing_keywords": [],
            "summary": "Error processing with AI."
        }

    # 4. Return result
    return {
        "success": True,
        "analysis": analysis
    }