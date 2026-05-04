from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timezone
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import httpx
import os

router = APIRouter(prefix="/auth", tags=["auth"])

# ── CONFIG ────────────────────────────────────────────────
# JWT_SECRET is fine at module level — it has a hardcoded fallback
JWT_SECRET      = os.getenv("JWT_SECRET", "changeme_use_a_long_random_string")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_DAYS = 7

# !! DO NOT read GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET here at module level !!
# main.py imports this file before load_dotenv() finishes in some reload scenarios.
# Instead, read them lazily inside the route using os.getenv() at call time.

GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

DB_NAME = "uspeak-db"

# ── DB ────────────────────────────────────────────────────
_client = None

def get_db():
    global _client
    if _client is None:
        mongo_uri = os.getenv("MONGO_URI")
        print(f"🔌 Connecting to: {mongo_uri[:40] if mongo_uri else 'NOT FOUND'}")
        if not mongo_uri:
            raise Exception("MONGO_URI not set in environment!")
        _client = AsyncIOMotorClient(mongo_uri)
    return _client[DB_NAME]

# ── PASSWORD HASHING ──────────────────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)

# ── JWT ───────────────────────────────────────────────────
def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# ── AUTH DEPENDENCY ───────────────────────────────────────
security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    payload = decode_token(credentials.credentials)
    db = get_db()
    from bson import ObjectId
    user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ── SCHEMAS ───────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class GoogleCallbackRequest(BaseModel):
    code: str
    redirect_uri: str

# ── HELPERS ───────────────────────────────────────────────
def user_to_dict(user) -> dict:
    return {
        "id":    str(user["_id"]),
        "name":  user.get("name", ""),
        "email": user.get("email", ""),
    }

# ── ROUTES ────────────────────────────────────────────────

@router.post("/register")
async def register(data: RegisterRequest):
    db = get_db()

    existing = await db.users.find_one({"email": data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    user_doc = {
        "name":       data.name.strip(),
        "email":      data.email.lower().strip(),
        "password":   hash_password(data.password),
        "auth_type":  "email",
        "settings":   {"reminder_time": None, "session_length": 2},
        "created_at": datetime.utcnow()
    }

    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)
    print(f"✅ User saved to DB: {db.client.address} | DB: {db.name} | ID: {user_id}")
    token = create_token(user_id, data.email.lower())

    return {
        "token": token,
        "user": {"id": user_id, "name": data.name, "email": data.email.lower()}
    }


@router.post("/login")
async def login(data: LoginRequest):
    db = get_db()

    user = await db.users.find_one({"email": data.email.lower()})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.get("auth_type") == "google":
        raise HTTPException(status_code=400, detail="This account uses Google Sign-In")

    if not verify_password(data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(str(user["_id"]), user["email"])

    return {
        "token": token,
        "user": user_to_dict(user)
    }


@router.post("/google")
async def google_auth(data: GoogleCallbackRequest):
    """Exchange Google OAuth code for user info, create/login user"""

    # ── Read credentials lazily here, NOT at module level ──
    google_client_id     = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    # Debug print — remove once working
    print(f"GOOGLE_CLIENT_ID: {google_client_id}")
    print(f"GOOGLE_CLIENT_SECRET: {'SET' if google_client_secret else 'NOT FOUND'}")

    if not google_client_id or not google_client_secret:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured on the server. Check GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env"
        )

    db = get_db()

    async with httpx.AsyncClient() as client:
        token_res = await client.post(GOOGLE_TOKEN_URL, data={
            "code":          data.code,
            "client_id":     google_client_id,
            "client_secret": google_client_secret,
            "redirect_uri":  data.redirect_uri,
            "grant_type":    "authorization_code"
        })

    print("Google token response:", token_res.status_code, token_res.text)

    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {token_res.text}")

    tokens = token_res.json()
    access_token = tokens.get("access_token")

    async with httpx.AsyncClient() as client:
        info_res = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )

    if info_res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to get Google user info")

    guser = info_res.json()
    google_id = guser["id"]
    email     = guser["email"].lower()
    name      = guser.get("name", email.split("@")[0])
    picture   = guser.get("picture", "")

    user = await db.users.find_one({"$or": [{"google_id": google_id}, {"email": email}]})

    if user:
        if not user.get("google_id"):
            await db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"google_id": google_id, "picture": picture}}
            )
    else:
        result = await db.users.insert_one({
            "name":       name,
            "email":      email,
            "google_id":  google_id,
            "picture":    picture,
            "auth_type":  "google",
            "settings":   {"reminder_time": None, "session_length": 2},
            "created_at": datetime.utcnow()
        })
        user = await db.users.find_one({"_id": result.inserted_id})

    token = create_token(str(user["_id"]), email)

    return {
        "token": token,
        "user": user_to_dict(user)
    }


@router.get("/me")
async def get_me(current_user=Depends(get_current_user)):
    return user_to_dict(current_user)