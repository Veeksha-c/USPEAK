# sessions.py
# Drop this file next to main.py, reminders.py, auth.py
# Then in main.py add:
#   from sessions import router as sessions_router
#   app.include_router(sessions_router)

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from jose import JWTError, jwt   # ← matches auth.py (python-jose)
import os

from auth import get_db  # same pattern as reminders.py

router = APIRouter(prefix="/sessions", tags=["sessions"])

# ← exact same env var name as auth.py
JWT_SECRET    = os.getenv("JWT_SECRET", "changeme_use_a_long_random_string")
JWT_ALGORITHM = "HS256"


# ── AUTH HELPER ──────────────────────────────────────────────────────────────

def get_current_user_email(authorization: Optional[str] = Header(None)) -> str:
    """
    Extracts user email from the Bearer JWT token in the Authorization header.
    Uses python-jose + JWT_SECRET, exactly like auth.py.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        # auth.py puts email in "email" field; "sub" holds user_id — so use "email"
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Token has no email")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── MODELS ───────────────────────────────────────────────────────────────────

class SessionPayload(BaseModel):
    avgScore: float
    date: str           # "YYYY-MM-DD"
    vibe: Optional[str] = None
    scores: Optional[dict] = None   # individual rubric scores if you want


# ── ROUTES ───────────────────────────────────────────────────────────────────

@router.post("")
async def save_session(
    payload: SessionPayload,
    email: str = Depends(get_current_user_email)
):
    """
    Save one session for the logged-in user.
    Called from vibe.html / results page after analysis.
    """
    db = get_db()
    doc = {
        "email":    email,
        "avgScore": payload.avgScore,
        "date":     payload.date,
        "vibe":     payload.vibe,
        "scores":   payload.scores,
        "createdAt": datetime.utcnow()
    }
    await db.sessions.insert_one(doc)
    return {"status": "saved"}


@router.get("")
async def get_sessions(
    email: str = Depends(get_current_user_email)
):
    """
    Return all sessions for the logged-in user, sorted oldest → newest.
    receipts.html calls this on load.
    """
    db = get_db()
    cursor = db.sessions.find(
        {"email": email},
        {"_id": 0, "email": 0}          # don't send _id or email to frontend
    ).sort("createdAt", 1)

    sessions = await cursor.to_list(length=500)
    return {"sessions": sessions}