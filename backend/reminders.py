from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
from bson import ObjectId
from auth import get_current_user, get_db

router = APIRouter(prefix="/reminders", tags=["reminders"])

# ── SCHEMAS ───────────────────────────────────────────────

class ReminderCreate(BaseModel):
    email: str
    time: str          # "HH:MM" (24-hour)
    session_length: int = 2   # minutes: 2, 5, or 10
    repeat: str = "daily"     # "daily" | "none"
    is_active: bool = True

class ReminderUpdate(BaseModel):
    email: str | None = None
    time: str | None = None
    session_length: int | None = None
    repeat: str | None = None
    is_active: bool | None = None

# ── HELPERS ───────────────────────────────────────────────

def reminder_to_dict(r) -> dict:
    return {
        "id":             str(r["_id"]),
        "user_id":        str(r["user_id"]),
        "email":          r.get("email", ""),
        "time":           r.get("time", ""),
        "session_length": r.get("session_length", 2),
        "repeat":         r.get("repeat", "daily"),
        "is_active":      r.get("is_active", True),
        "last_sent_date": r.get("last_sent_date", None),
        "created_at":     r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at":     r["updated_at"].isoformat() if r.get("updated_at") else None,
    }

# ── ROUTES ────────────────────────────────────────────────

@router.post("/")
async def create_or_update_reminder(
    data: ReminderCreate,
    current_user=Depends(get_current_user)
):
    """
    Upsert: each user has one reminder doc.
    If one already exists, update it. Otherwise create fresh.
    """
    db = get_db()
    user_id = current_user["_id"]

    existing = await db.reminders.find_one({"user_id": user_id})

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if existing:
        # Update existing reminder
        await db.reminders.update_one(
            {"user_id": user_id},
            {"$set": {
                "email":          data.email.lower().strip(),
                "time":           data.time,
                "session_length": data.session_length,
                "repeat":         data.repeat,
                "is_active":      data.is_active,
                "updated_at":     now,
            }}
        )
        updated = await db.reminders.find_one({"user_id": user_id})
        return {"status": "updated", "reminder": reminder_to_dict(updated)}
    else:
        # Create new reminder
        doc = {
            "user_id":        user_id,
            "email":          data.email.lower().strip(),
            "time":           data.time,
            "session_length": data.session_length,
            "repeat":         data.repeat,
            "is_active":      data.is_active,
            "last_sent_date": None,   # tracks deduplication
            "created_at":     now,
            "updated_at":     now,
        }
        result = await db.reminders.insert_one(doc)
        created = await db.reminders.find_one({"_id": result.inserted_id})
        return {"status": "created", "reminder": reminder_to_dict(created)}


@router.get("/")
async def get_my_reminder(current_user=Depends(get_current_user)):
    """Get current user's reminder settings."""
    db = get_db()
    reminder = await db.reminders.find_one({"user_id": current_user["_id"]})
    if not reminder:
        return {"reminder": None}
    return {"reminder": reminder_to_dict(reminder)}


@router.patch("/{reminder_id}")
async def update_reminder(
    reminder_id: str,
    data: ReminderUpdate,
    current_user=Depends(get_current_user)
):
    """Partially update a reminder (e.g. toggle is_active, change time)."""
    db = get_db()

    reminder = await db.reminders.find_one({
        "_id":     ObjectId(reminder_id),
        "user_id": current_user["_id"]
    })
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.utcnow()

    await db.reminders.update_one(
        {"_id": ObjectId(reminder_id)},
        {"$set": updates}
    )

    updated = await db.reminders.find_one({"_id": ObjectId(reminder_id)})
    return {"status": "updated", "reminder": reminder_to_dict(updated)}


@router.delete("/{reminder_id}")
async def delete_reminder(
    reminder_id: str,
    current_user=Depends(get_current_user)
):
    """Delete a reminder."""
    db = get_db()

    reminder = await db.reminders.find_one({
        "_id":     ObjectId(reminder_id),
        "user_id": current_user["_id"]
    })
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")

    await db.reminders.delete_one({"_id": ObjectId(reminder_id)})
    return {"status": "deleted", "id": reminder_id}


@router.patch("/{reminder_id}/toggle")
async def toggle_reminder(
    reminder_id: str,
    current_user=Depends(get_current_user)
):
    """Quick toggle is_active on/off."""
    db = get_db()

    reminder = await db.reminders.find_one({
        "_id":     ObjectId(reminder_id),
        "user_id": current_user["_id"]
    })
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")

    new_state = not reminder.get("is_active", True)

    await db.reminders.update_one(
        {"_id": ObjectId(reminder_id)},
        {"$set": {"is_active": new_state, "updated_at": datetime.utcnow()}}
    )

    return {"status": "toggled", "is_active": new_state}