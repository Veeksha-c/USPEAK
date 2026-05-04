from dotenv import load_dotenv
import os

# ── MUST be first — before any local imports that call os.getenv ──
env_path = os.path.join(os.path.dirname(__file__), '.env')
print(f"Loading .env from: {env_path}")
load_dotenv(env_path)

# ── NOW safe to import local modules ──────────────────────
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from groq import Groq
import tempfile
import shutil
import smtplib
import subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
import re

from feedback import analyze_speech_full
from auth import router as auth_router, get_db
from reminders import router as reminders_router
from sessions import router as sessions_router

# ── GROQ CLIENT ───────────────────────────────────────────
api_key = os.getenv("GROQ_API_KEY")
print(f"GROQ_API_KEY loaded: {api_key[:10]}..." if api_key else "GROQ_API_KEY not found")
client = Groq(api_key=api_key)

# ── APP ───────────────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(reminders_router)
app.include_router(sessions_router)

# ── EMAIL SENDER ──────────────────────────────────────────

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
                    <a href="https://uspeak-six.vercel.app/pages/vibe.html" 
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


# ── SCHEDULER ─────────────────────────────────────────────

async def reminder_job():
    db = get_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_time = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")

    cursor = db.reminders.find({"is_active": True})
    reminders = await cursor.to_list(length=1000)

    for reminder in reminders:
        scheduled_time = reminder.get("time")
        last_sent = reminder.get("last_sent_date")
        email = reminder.get("email")

        if not scheduled_time or not email:
            continue

        if current_time >= scheduled_time and last_sent != today:
            try:
                print(f"📧 Sending reminder to {email} (scheduled: {scheduled_time})")
                send_email(email)
                await db.reminders.update_one(
                    {"_id": reminder["_id"]},
                    {"$set": {"last_sent_date": today}}
                )
            except Exception as e:
                print(f"❌ Failed to send reminder to {email}: {e}")


scheduler = AsyncIOScheduler()
# ✅ NEW
scheduler.add_job(reminder_job, "interval", minutes=1, misfire_grace_time=60)

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


class ProjectQuestionsRequest(BaseModel):
    transcript: str

@app.post("/generate-project-questions")
def generate_project_questions(data: ProjectQuestionsRequest):
    if not data.transcript or len(data.transcript.strip()) < 20:
        return {"questions": [
            "Can you tell me more about the problem your project solves?",
            "What was the biggest technical challenge you faced?",
            "How would you improve this project given more time?"
        ]}

    prompt = f"""
You are an experienced technical interviewer conducting an HR + technical round.

A candidate just explained their project. Based on their explanation, generate 4 sharp, 
specific follow-up questions an interviewer would ask.

Rules:
- Questions must be grounded in what the candidate actually said
- Mix of technical depth questions AND soft/impact questions
- Each question should be one clear sentence
- No numbering, no preamble — just the questions, one per line
- Don't repeat what the candidate already answered well

Candidate's explanation:
\"\"\"{data.transcript}\"\"\"

Generate 4 interview questions now:
"""

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a sharp technical interviewer. Generate concise, targeted interview questions based on what a candidate said."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.6,
        max_tokens=200,
    )

    raw = completion.choices[0].message.content.strip()
    questions = [q.strip().lstrip("-•123456789. ") for q in raw.splitlines() if q.strip()]
    questions = [q for q in questions if len(q) > 10][:5]

    if not questions:
        questions = [
            "What problem does your project solve, and who is the target user?",
            "Walk me through the most technically challenging part of this project.",
            "How does your system handle failure or edge cases?",
            "What would you change about your architecture if you started over?"
        ]

    return {"questions": questions}


class QAAnswer(BaseModel):
    question: str
    transcript: str
    skipped: bool = False

class ProjectAnalysisRequest(BaseModel):
    project_transcript: str
    project_body_score: float = 5.0
    qa_answers: List[QAAnswer] = []
    qa_body_score: float = 5.0

@app.post("/analyze-project")
def analyze_project(data: ProjectAnalysisRequest):
    exp_prompt = f"""
You are an expert speech and communication coach evaluating a project explanation.

Analyze the transcript below using these EXACT rubrics:

FLUENCY (1–10): smoothness, flow, absence of awkward pauses
CLARITY (1–10): how clearly the project's purpose, tech, and impact are explained
CONFIDENCE (1–10): assertive tone, absence of hedging and self-doubt

Transcript:
\"\"\"{data.project_transcript}\"\"\"

Respond in THIS EXACT FORMAT only. No extra text:

PROS:
- [specific strength in the explanation]
- [another strength]
- [another strength]

CONS:
- [specific, actionable weakness]
- [another weakness]
- [another weakness]

SCORES:
fluency: X/10
clarity: X/10
confidence: X/10

IMPROVEMENT_TIP:
[One concrete tip for explaining projects better]
"""

    exp_completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a professional speech coach. Follow the exact format given. No extra commentary."},
            {"role": "user", "content": exp_prompt}
        ],
        temperature=0.4,
        max_tokens=500,
    )
    exp_raw = exp_completion.choices[0].message.content.strip()

    def parse_exp(text):
        pros, cons, scores, tip = [], [], {}, ""
        section = None
        for raw_line in text.splitlines():
            line = raw_line.strip().replace("**", "")
            if not line: continue
            upper = line.upper()
            if upper.startswith("PROS"): section = "pros"; continue
            if upper.startswith("CONS"): section = "cons"; continue
            if upper.startswith("SCORES"): section = "scores"; continue
            if upper.startswith("IMPROVEMENT_TIP") or upper.startswith("IMPROVEMENT TIP"): section = "tip"; continue
            if section in ["pros","cons"] and line.startswith("-"):
                pt = line.lstrip("-").strip()
                (pros if section=="pros" else cons).append(pt)
            elif section == "scores" and ":" in line:
                k, v = line.lstrip("-").strip().split(":", 1)
                nums = re.findall(r"\d+", v)
                scores[k.lower().strip()] = int(nums[0]) if nums else 5
            elif section == "tip" and line:
                tip += line + " "
        return pros, cons, scores, tip.strip()

    exp_pros, exp_cons, exp_scores, tip = parse_exp(exp_raw)

    qa_feedback_list = []
    qa_scores_agg = {"relevance": [], "depth": [], "confidence": []}
    non_skipped = [a for a in data.qa_answers if not a.skipped and a.transcript.strip()]

    if non_skipped:
        qa_block = "\n\n".join([f"Q: {a.question}\nA: {a.transcript}" for a in non_skipped])

        qa_prompt = f"""
You are evaluating a candidate's answers during a project Q&A interview round.

For EACH question-answer pair below, score and give one-sentence feedback.

Scoring rubrics (1–10 each):
- RELEVANCE: Does the answer directly address the question?
- DEPTH: Does the answer show technical or conceptual understanding?
- CONFIDENCE: Is the answer delivered assertively, without excessive hedging?

Q&A Pairs:
{qa_block}

Respond in THIS EXACT FORMAT for each Q&A pair. Repeat the block for each pair:

ANSWER_1:
relevance: X/10
depth: X/10
confidence: X/10
feedback: [one sentence of targeted feedback]

ANSWER_2:
relevance: X/10
depth: X/10
confidence: X/10
feedback: [one sentence of targeted feedback]

(continue for all answers)
"""

        qa_completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a precise technical interviewer evaluating Q&A answers. Follow the exact format."},
                {"role": "user", "content": qa_prompt}
            ],
            temperature=0.4,
            max_tokens=600,
        )
        qa_raw = qa_completion.choices[0].message.content.strip()

        blocks = re.split(r'ANSWER_\d+:', qa_raw)
        blocks = [b.strip() for b in blocks if b.strip()]

        for i, (answer_obj, block) in enumerate(zip(non_skipped, blocks)):
            r_score, d_score, c_score, feedback = 5, 5, 5, "No feedback generated."
            for line in block.splitlines():
                line = line.strip().replace("**","")
                if line.lower().startswith("relevance:"):
                    nums = re.findall(r"\d+", line.split(":",1)[1])
                    r_score = int(nums[0]) if nums else 5
                elif line.lower().startswith("depth:"):
                    nums = re.findall(r"\d+", line.split(":",1)[1])
                    d_score = int(nums[0]) if nums else 5
                elif line.lower().startswith("confidence:"):
                    nums = re.findall(r"\d+", line.split(":",1)[1])
                    c_score = int(nums[0]) if nums else 5
                elif line.lower().startswith("feedback:"):
                    feedback = line.split(":",1)[1].strip()

            qa_scores_agg["relevance"].append(r_score)
            qa_scores_agg["depth"].append(d_score)
            qa_scores_agg["confidence"].append(c_score)

            qa_feedback_list.append({
                "question": answer_obj.question,
                "feedback": feedback,
                "skipped": False,
                "scores": {"relevance": r_score, "depth": d_score, "confidence": c_score}
            })

    for a in data.qa_answers:
        if a.skipped:
            qa_feedback_list.append({"question": a.question, "feedback": "", "skipped": True})
            qa_scores_agg["relevance"].append(1)
            qa_scores_agg["depth"].append(1)
            qa_scores_agg["confidence"].append(1)

    def avg_list(lst): return round(sum(lst)/len(lst), 1) if lst else 5

    qa_scores = {
        "relevance":     avg_list(qa_scores_agg["relevance"]),
        "depth":         avg_list(qa_scores_agg["depth"]),
        "confidence":    avg_list(qa_scores_agg["confidence"]),
        "body_language": round(data.qa_body_score, 1)
    }

    explanation_scores = {
        "fluency":       exp_scores.get("fluency", 5),
        "clarity":       exp_scores.get("clarity", 5),
        "confidence":    exp_scores.get("confidence", 5),
        "body_language": round(data.project_body_score, 1)
    }

    all_scores = list(explanation_scores.values()) + list(qa_scores.values())
    overall = round(sum(all_scores) / len(all_scores), 1)
    knowledge_score = round((qa_scores["relevance"] + qa_scores["depth"]) / 2, 1)
    knowledge_gaps = []

    if non_skipped:
        gaps_prompt = f"""
You are evaluating how well a candidate knows their own project based on their Q&A answers.

Project explanation:
\"\"\"{data.project_transcript[:800]}\"\"\"

Their Q&A answers:
{chr(10).join([f"Q: {a.question}{chr(10)}A: {a.transcript}" for a in non_skipped])}

Based on the above, identify 2-3 specific knowledge gaps — things they clearly didn't know well,
couldn't explain properly, or avoided answering. Be concrete and actionable.

Rules:
- Each gap is ONE sentence max
- Focus on what they SHOULD know about their own project but didn't demonstrate
- If they answered everything well, output exactly: NONE
- No numbering, no preamble — just the gaps, one per line

Output the gaps now:
"""
        try:
            gaps_completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You identify knowledge gaps in project explanations. Be specific and brief."},
                    {"role": "user", "content": gaps_prompt}
                ],
                temperature=0.3,
                max_tokens=200,
            )
            gaps_raw = gaps_completion.choices[0].message.content.strip()
            if gaps_raw.upper() != "NONE":
                knowledge_gaps = [
                    g.strip().lstrip("-•123456789. ")
                    for g in gaps_raw.splitlines()
                    if g.strip() and len(g.strip()) > 10
                ][:3]
        except Exception as e:
            print(f"Knowledge gaps generation failed: {e}")
            knowledge_gaps = []

    return {
        "explanation_pros":   exp_pros or ["Project was explained clearly."],
        "explanation_cons":   exp_cons or ["More technical depth would strengthen the explanation."],
        "qa_feedback":        qa_feedback_list,
        "explanation_scores": explanation_scores,
        "qa_scores":          qa_scores,
        "overall_score":      overall,
        "improvement_tip":    tip or "Practice explaining your project in under 90 seconds with one concrete example.",
        "knowledge_score":    knowledge_score,
        "knowledge_gaps":     knowledge_gaps,
    }