from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv
import os
import tempfile
import shutil
from fastapi import UploadFile, File
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.asyncio import AsyncIOScheduler   # ← switched to Async
from datetime import datetime
from feedback import analyze_speech_full

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), '.env')
print(f"Loading .env from: {env_path}")
load_dotenv(env_path)

from auth import router as auth_router, get_db
from reminders import router as reminders_router             # ← NEW

api_key = os.getenv("GROQ_API_KEY")
print(f"GROQ_API_KEY loaded: {api_key[:10]}..." if api_key else "GROQ_API_KEY not found")

client = Groq(api_key=api_key)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(reminders_router)                         # ← NEW

# ── EMAIL SENDER ─────────────────────────────────────────

def send_email(to_email: str):
    sender = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"uSpeak App <{sender}>"
    msg["To"] = to_email
    msg["Subject"] = "Your daily speaking session is waiting 🎙️"

    html = """
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background-color:#0a0a0a; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0a; padding: 40px 20px;">
    <tr>
      <td align="center">
        <table width="480" cellpadding="0" cellspacing="0" style="background-color:#111315; border-radius:16px; overflow:hidden;">
          <tr><td style="background-color:#c0392b; height:4px;"></td></tr>
          <tr>
            <td style="padding: 36px 40px 0px 40px;">
              <p style="margin:0; font-size:22px; font-weight:700; color:#ffffff; letter-spacing:1px;">
                <span style="color:#c0392b;">U</span>SPEAK
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding: 28px 40px 12px 40px;">
              <h1 style="margin:0 0 16px 0; font-size:26px; font-weight:700; color:#ffffff; line-height:1.3;">
                Time to find your voice. 🎙️
              </h1>
              <p style="margin:0 0 12px 0; font-size:15px; color:#a0a0a0; line-height:1.7;">
                You set this reminder because you made a commitment — to speak more clearly, more confidently, every single day.
              </p>
              <p style="margin:0 0 24px 0; font-size:15px; color:#a0a0a0; line-height:1.7;">
                Today's session doesn't have to be perfect. It just has to happen.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background-color:#1a1d20; border-left: 3px solid #c0392b; border-radius:0 8px 8px 0; padding:16px 20px;">
                    <p style="margin:0; font-size:14px; color:#e0e0e0; line-height:1.6; font-style:italic;">
                      "The human voice is the most perfect instrument of all."
                    </p>
                    <p style="margin:6px 0 0 0; font-size:12px; color:#666;">— Arvo Pärt</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding: 28px 40px;">
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background-color:#c0392b; border-radius:8px;">
                    <a href="http://127.0.0.1:5500/pages/vibe.html"
                       style="display:inline-block; padding:14px 32px; font-size:15px; font-weight:600; color:#ffffff; text-decoration:none; letter-spacing:0.5px;">
                      Start Today's Session →
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding: 0 40px;">
              <hr style="border:none; border-top:1px solid #1f2224; margin:0;">
            </td>
          </tr>
          <tr>
            <td style="padding: 24px 40px 36px 40px;">
              <p style="margin:0; font-size:12px; color:#444; line-height:1.6;">
                You're receiving this because you set a daily reminder on uSpeak.<br>
                To change your reminder time, visit the settings page in the app.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, to_email, msg.as_string())


# ── SCHEDULER (reads from MongoDB) ───────────────────────

async def reminder_job():
    """
    Runs every minute. Scans all active reminders in MongoDB.
    Sends email if current time >= scheduled time and not already sent today.
    Works for every user independently.
    """
    db = get_db()
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")

    # Fetch all active reminders
    cursor = db.reminders.find({"is_active": True})
    reminders = await cursor.to_list(length=1000)

    for reminder in reminders:
        scheduled_time = reminder.get("time")
        last_sent = reminder.get("last_sent_date")
        email = reminder.get("email")

        if not scheduled_time or not email:
            continue

        # Send if: current time has passed scheduled time AND not yet sent today
        if current_time >= scheduled_time and last_sent != today:
            try:
                print(f"📧 Sending reminder to {email} (scheduled: {scheduled_time})")
                send_email(email)

                # Mark as sent for today so we don't double-send
                await db.reminders.update_one(
                    {"_id": reminder["_id"]},
                    {"$set": {"last_sent_date": today}}
                )
            except Exception as e:
                print(f"❌ Failed to send reminder to {email}: {e}")


scheduler = AsyncIOScheduler()
scheduler.add_job(reminder_job, "interval", minutes=1)

@app.on_event("startup")
async def startup():
    scheduler.start()
    print("✅ Reminder scheduler started (MongoDB-backed)")

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()


# ── ROUTES ────────────────────────────────────────────────

class VibeRequest(BaseModel):
    vibe: str


@app.post("/generate-topic")
def generate_topic(data: VibeRequest):
    vibe = data.vibe

    vibe_prompts = {
        "personal": (
            "Generate a personal reflection speaking topic for a beginner speaker. "
            "The topic must be based on a universal everyday experience anyone can relate to — "
            "like a habit, a memory, a small moment that changed their thinking, or a personal value. "
            "It should invite the speaker to share an opinion or feeling, not just describe facts. "
            "Example style: 'Talk about a habit you wish you had started earlier — and why it matters to you.'"
        ),
        "motivation": (
            "Generate a motivational speaking topic for a beginner speaker. "
            "It should be grounded and real — not vague inspiration. "
            "Focus on small, specific actions or mindset shifts rather than big abstract goals. "
            "Example style: 'Talk about one small decision that changed the direction of your day — or your life.'"
        ),
        "tech": (
            "Generate a technology opinion topic for a beginner speaker. "
            "Pick something they use daily — social media, smartphones, AI tools, online learning — "
            "and ask them to take a clear stance: is it helping or hurting us? "
            "Example style: 'Do you think social media makes us more lonely or more connected? Make your case.'"
        ),
        "entertainment": (
            "Generate an entertainment speaking topic for a beginner speaker. "
            "It should be about something they likely watch, listen to, or enjoy. "
            "Example style: 'Talk about a movie or show that changed how you see the world — and why it stuck with you.'"
        ),
        "travel": (
            "Generate a travel or experience speaking topic for a beginner speaker. "
            "It doesn't have to involve actual travel — exploring a new food or place in their city works too. "
            "Example style: 'Describe a place you visited that surprised you — what did you expect vs what you found?'"
        ),
        "surprise": (
            "Generate a fun, unexpected speaking topic that a beginner speaker would find easy and enjoyable. "
            "Make it slightly unusual but totally approachable — a hypothetical scenario or playful what-if. "
            "Example style: 'If you could add one subject to your school curriculum, what would it be and why?'"
        ),
    }

    base_instruction = vibe_prompts.get(vibe.lower(), "Generate a fun speaking topic for a beginner.")

    prompt = f"""
You are a speech coach helping beginner speakers practice.

Your job is to generate ONE speaking topic based on this instruction:
{base_instruction}

Requirements:
- Single clear question or prompt
- Answerable from personal experience (no research needed)
- 1-2 sentences maximum
- Ends in a way that invites opinion or story
- No numbering, no explanation, no preamble — just the topic itself

Generate the topic now:
"""

    completion = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "You generate speaking practice topics for beginner speakers."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.9,
        max_tokens=80,
    )

    topic = completion.choices[0].message.content.strip()
    return {"topic": topic}


import subprocess

@app.post("/transcribe")
async def transcribe_video(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video:
        shutil.copyfileobj(file.file, temp_video)
        video_path = temp_video.name

    audio_path = video_path.replace(".mp4", ".mp3")
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-q:a", "0", "-map", "a",
        audio_path, "-y"
    ], check=True)

    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            response_format="text"
        )

    os.unlink(video_path)
    os.unlink(audio_path)

    transcript = transcription
    print(f"DEBUG: Transcript is: {transcript}")
    return {"transcript": transcript}


class AnalysisRequest(BaseModel):
    transcript: str
    body_language_score: float = 5.0

@app.post("/analyze")
def analyze_speech(data: AnalysisRequest):
    transcript = data.transcript
    body_language_score = data.body_language_score

    if not transcript or len(transcript.strip()) < 10:
        return {"error": "Transcript too short"}

    result = analyze_speech_full(transcript, client, data.body_language_score)
    return result