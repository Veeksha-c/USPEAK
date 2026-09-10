# uSpeak

uSpeak is a speaking-practice web application. It provides browser pages for recording and reviewing speaking exercises, plus a FastAPI backend for authentication, session and reminder data, speech transcription, AI feedback, and practice-topic generation.

## Project layout

- `pages/` — static browser pages.
- `backend/` — FastAPI application and Dockerfile.
- `backend/main.py` — API entry point.

## Requirements

- Python 3.11 or later
- A MongoDB connection string
- A Groq API key
- `ffmpeg` on the system path for video-to-audio conversion during transcription

## Backend setup

From the repository root:

```bash
python -m venv venv
venv\\Scripts\\activate
pip install -r backend/requirements.txt
```

Create `backend/.env` with the values needed for the features you use:

```env
GROQ_API_KEY=...
MONGO_URI=...
JWT_SECRET=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GMAIL_USER=...
GMAIL_APP_PASSWORD=...
RESEND_API_KEY=...
BREVO_API_KEY=...
```

Never commit this file or real credentials.

Start the API:

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Check that it is running:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Key API routes

- `GET /health` — service health check.
- `POST /generate-topic` — generates a practice topic from a `vibe` value.
- `POST /transcribe` — transcribes an uploaded recording.
- `POST /analyze` — returns speaking feedback.
- `POST /generate-project-questions` and `POST /analyze-project` — project-practice support.
- `/auth`, `/reminders`, and `/sessions` — authentication, reminders, and session routes.

`/generate-topic` uses Groq's `openai/gpt-oss-20b` model. The `GROQ_API_KEY` must belong to a Groq project that can access this model.

## Docker

Build from the backend directory:

```bash
cd backend
docker build -t uspeak-backend .
docker run --env-file .env -p 8000:8000 uspeak-backend
```

## Development notes

- The frontend currently calls the deployed backend URL from the static pages. Update those URLs if you want the browser UI to target a local API.
- `backend/settings.json`, `.env` files, and `utils.txt` are ignored because they may contain local or sensitive data.
