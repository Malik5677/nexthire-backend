import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import openai
import os
from dotenv import load_dotenv
from openai import OpenAI   # NEW

load_dotenv()

router = APIRouter()

# ------------ TEXT MODEL (SAMBA NOVA) -------------
client = openai.OpenAI(
    api_key=os.getenv("SAMBANOVA_API_KEY"),
    base_url=os.getenv("SAMBANOVA_BASE_URL")
)

MODEL = "Meta-Llama-3.1-8B-Instruct"

# ------------ OPENAI VOICE CLIENT -------------
voice_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ------------ GENERATE REALISTIC MALE VOICE -------------
def generate_openai_voice(text: str):
    """
    Generates realistic MALE AI audio using OpenAI TTS.
    Voice options: alloy, bold, deep, verse, morpho
    """
    audio = voice_client.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="alloy",      # MALE VOICE
        input=text,
        format="wav"
    )
    return audio.read()  # WAV bytes


# ------------ SEND AUDIO TO FRONTEND -------------
async def send_audio(ws: WebSocket, text: str):
    audio_bytes = generate_openai_voice(text)
    await ws.send_bytes(audio_bytes)


# ------------ SEND TEXT + AUDIO -------------
async def send_text(ws: WebSocket, text: str):
    # send text to frontend
    await ws.send_json({"type": "text_response", "content": text})

    # send audio (realistic male voice)
    try:
        await send_audio(ws, text)
    except Exception as e:
        print("Audio Send Error:", e)


# =============================================================
#                     WEBSOCKET INTERVIEW LOOP
# =============================================================
active = {}

@router.websocket("/ws/interview")
async def interview_socket(ws: WebSocket):
    await ws.accept()
    sid = str(id(ws))
    active[sid] = {"history": []}

    print("Client connected", sid)

    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")
            text = data.get("text", "")

            # ---------------------- START SESSION ----------------------
            if action == "start":
                opening = (
                    "Hi! I am Christopher, your AI hiring manager. "
                    "Let’s begin. Tell me a bit about yourself."
                )

                active[sid]["history"].append(
                    {"role": "assistant", "content": opening}
                )

                await send_text(ws, opening)

            # ---------------------- USER ANSWER ------------------------
            elif action == "answer":
                active[sid]["history"].append(
                    {"role": "user", "content": text}
                )

                completion = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": "You are a friendly HR interviewer."},
                        *active[sid]["history"]
                    ],
                    max_tokens=150,
                    temperature=0.7
                )

                reply = completion.choices[0].message.content
                active[sid]["history"].append(
                    {"role": "assistant", "content": reply}
                )

                await send_text(ws, reply)

            # ---------------------- END SESSION ------------------------
            elif action == "end":
                transcript = "\n".join(
                    f"{m['role'].upper()}: {m['content']}"
                    for m in active[sid]["history"]
                )

                report_prompt = (
                    "Generate JSON interview evaluation for this transcript:\n"
                    f"{transcript}\n"
                    "JSON keys: score, confidence_level, feedback, improvements, spoken_summary"
                )

                report_resp = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "system", "content": report_prompt}],
                    response_format={"type": "json_object"}
                )

                report_json = report_resp.choices[0].message.content
                report_obj = json.loads(report_json)

                # speak summary using OpenAI realistic male voice
                await send_text(ws, report_obj["spoken_summary"])

                await ws.send_json({
                    "type": "report",
                    "content": report_json
                })

    except WebSocketDisconnect:
        print("Client disconnected", sid)
    except Exception as e:
        print("WS ERROR:", e)
