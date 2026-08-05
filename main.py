import json
import os
import secrets
import time
import hmac
import hashlib
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from filelock import FileLock

DB_FILE = "db.json"
LOCK_FILE = "db.json.lock"
SERVER_SECRET_KEY = "FF_ESPORTS_SUPER_SECRET_KEY_998877"

app = FastAPI(title="Esports MiniApp Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAIN_ADMIN_ID = 8908999062

# --- DB HELPERS ---

def get_db() -> Dict[str, Any]:
    if not os.path.exists(DB_FILE):
        default_db = {
            "users": {},
            "tournaments": {},
            "announcements": [],
            "creators": {},
            "banned_users": [],
            "ad_unlocks": {}
        }
        save_db(default_db)
        return default_db

    lock = FileLock(LOCK_FILE, timeout=5)
    with lock:
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        if "users" not in data or not isinstance(data["users"], dict): data["users"] = {}
        if "tournaments" not in data or not isinstance(data["tournaments"], dict): data["tournaments"] = {}
        if "announcements" not in data or not isinstance(data["announcements"], list): data["announcements"] = []
        if "creators" not in data or not isinstance(data["creators"], dict): data["creators"] = {}
        if "banned_users" not in data or not isinstance(data["banned_users"], list): data["banned_users"] = []
        if "ad_unlocks" not in data or not isinstance(data["ad_unlocks"], dict): data["ad_unlocks"] = {}

        return data

def save_db(data: Dict[str, Any]):
    lock = FileLock(LOCK_FILE, timeout=5)
    with lock:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_role(db: Dict[str, Any], tg_id: int) -> str:
    if tg_id == MAIN_ADMIN_ID:
        return "MAIN_ADMIN"
    if str(tg_id) in db.get("creators", {}):
        return "CREATOR"
    return "USER"

def generate_signed_gateway_token(tg_id: int, role: str, squad_code: str) -> str:
    exp = int(time.time()) + 3600  # 1 hour expiration
    payload = f"{tg_id}:{role}:{squad_code}:{exp}"
    signature = hmac.new(SERVER_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

# --- AUTOMATIC TOURNAMENT CLEANUP TASK ---
async def auto_cleanup_tournaments():
    while True:
        try:
            db = get_db()
            now = time.time()
            modified = False

            for t_id, t in list(db.get("tournaments", {}).items()):
                start_ts = t.get("start_timestamp")
                if not start_ts and t.get("start_time"):
                    try:
                        dt = datetime.fromisoformat(t["start_time"].replace("Z", "+00:00"))
                        start_ts = dt.timestamp()
                    except Exception:
                        start_ts = None

                # 7 mins grace + 5 mins = 12 mins (720 seconds)
                if start_ts and (now - start_ts) > 720:
                    room_id = t.get("room_id", "")
                    room_pass = t.get("room_password", "")
                    if not room_id or not room_pass or room_id == "PROTECTED":
                        print(f"🗑 Auto-deleting tournament {t_id} due to missing room credentials!")
                        del db["tournaments"][t_id]
                        modified = True

            if modified:
                save_db(db)

        except Exception as e:
            print("Auto-cleanup task error:", e)

        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_cleanup_tournaments())

# --- SCHEMAS ---

class AuthInitRequest(BaseModel):
    telegram_id: int
    username: Optional[str] = ""
    first_name: Optional[str] = "Player"
    photo_url: Optional[str] = ""

class PlayerMember(BaseModel):
    nickname: str  # Mandatory FF Style Name
    ff_id: Optional[str] = ""  # Optional FF UID

class RegisterFullSquadRequest(BaseModel):
    tournament_id: str
    squad_name: str
    members: List[PlayerMember]  # Up to 4 players submitted together by leader

class JoinSquadRequest(BaseModel):
    squad_code: str
    nickname: str  # Mandatory FF Style Name
    ff_id: Optional[str] = ""  # Optional FF UID

class UploadRoomRequest(BaseModel):
    tournament_id: str
    room_id: str
    room_password: str
    new_start_time: Optional[str] = None

class CreateTournamentRequest(BaseModel):
    title: str
    code: str
    prize: str
    task_type: Optional[str] = "LINK"  # "MONETAG_AD" or "LINK"
    task_description: Optional[str] = ""
    task_link: Optional[str] = ""
    rules: Optional[str] = ""
    start_time: str

class CreatorProfileRequest(BaseModel):
    telegram_id: int
    squad_name: str
    description: Optional[str] = ""
    player_roles: Optional[str] = ""
    youtube: Optional[str] = ""
    facebook: Optional[str] = ""
    tiktok: Optional[str] = ""

class AnnouncementRequest(BaseModel):
    text: str
    image_url: Optional[str] = ""

class CreatorAddRequest(BaseModel):
    telegram_id: int
    squad_name: str

class BanUserRequest(BaseModel):
    telegram_id: int

class OfflineSyncPayload(BaseModel):
    registrations: List[RegisterFullSquadRequest]

# --- API ENDPOINTS ---

@app.post("/api/auth/init")
def auth_init(req: AuthInitRequest):
    db = get_db()
    str_tg_id = str(req.telegram_id)

    if req.telegram_id in db.get("banned_users", []):
        raise HTTPException(status_code=403, detail="আপনার অ্যাকাউন্টটি ব্লক করা হয়েছে।")

    if str_tg_id not in db["users"]:
        db["users"][str_tg_id] = {
            "telegram_id": req.telegram_id,
            "username": req.username or "",
            "first_name": req.first_name or "Player",
            "photo_url": req.photo_url or "",
            "joined_tournaments": []
        }
    else:
        db["users"][str_tg_id]["username"] = req.username or db["users"][str_tg_id].get("username", "")
        db["users"][str_tg_id]["first_name"] = req.first_name or db["users"][str_tg_id].get("first_name", "Player")
        db["users"][str_tg_id]["photo_url"] = req.photo_url or db["users"][str_tg_id].get("photo_url", "")

    save_db(db)

    user = db["users"][str_tg_id]
    role = get_user_role(db, req.telegram_id)
    is_unlocked = str_tg_id in db.get("ad_unlocks", {})

    return {
        "status": "success",
        "user": user,
        "role": role,
        "is_unlocked": is_unlocked,
        "announcements": db.get("announcements", [])
    }

@app.get("/api/tournaments")
def get_tournaments(x_tg_id: str = Header(None)):
    db = get_db()
    tournaments_list = []
    
    for t_id, t in db.get("tournaments", {}).items():
        t_data = dict(t)
        t_data["id"] = t_id
        
        squads = t.get("squads", {})
        has_access = False
        if x_tg_id:
            if str(t.get("creator_id")) == str(x_tg_id) or str(x_tg_id) == str(MAIN_ADMIN_ID):
                has_access = True
            else:
                for sq in squads.values():
                    for member in sq.get("members", []):
                        if str(member.get("tg_id")) == str(x_tg_id):
                            has_access = True
                            break

        if not has_access:
            t_data["room_id"] = "PROTECTED"
            t_data["room_password"] = "PROTECTED"
            
        t_data["has_credentials"] = bool(t.get("room_id") and t.get("room_password"))
        t_data["total_joined_squads"] = len(squads)
        tournaments_list.append(t_data)

    return {"tournaments": tournaments_list}

@app.post("/api/tournaments/register-leader")
def register_leader(req: RegisterFullSquadRequest, x_tg_id: str = Header(None)):
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Telegram ID Missing")
        
    db = get_db()
    t_id = req.tournament_id
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="Tournament Not Found")

    t = db["tournaments"][t_id]
    squads = t.get("squads", {})

    if len(squads) >= 12:
        raise HTTPException(status_code=400, detail="টুর্নামেন্ট ফুল হয়ে গেছে (১২/১২ স্কোয়াড)!")

    # Check: Ensure user is not already in a squad for this tournament
    for sq in squads.values():
        for member in sq.get("members", []):
            if str(member.get("tg_id")) == str(x_tg_id):
                raise HTTPException(
                    status_code=400, 
                    detail="আপনি ইতিমধ্যে এই টুর্নামেন্টের একটি স্কোয়াডে যুক্ত আছেন!"
                )

    if not req.members or len(req.members) == 0:
        raise HTTPException(status_code=400, detail="কমপক্ষে লিডারের তথ্য দেওয়া বাধ্যতামূলক!")

    # Validate mandatory Free Fire Style Name
    for idx, m in enumerate(req.members):
        if not m.nickname or not m.nickname.strip():
            raise HTTPException(status_code=400, detail=f"প্লেয়ার {idx+1} এর ফ্রি ফায়ার স্টাইল নাম প্রদান করা আবশ্যক!")

    squad_code = secrets.token_hex(3).upper()
    
    formatted_members = []
    for idx, m in enumerate(req.members):
        formatted_members.append({
            "tg_id": int(x_tg_id) if idx == 0 else 0, # Leader has actual TG ID
            "nickname": m.nickname.strip(),
            "ff_id": (m.ff_id or "N/A").strip(),
            "role": "LEADER" if idx == 0 else f"PLAYER_{idx+1}"
        })

    new_squad = {
        "squad_name": req.squad_name,
        "squad_code": squad_code,
        "leader_tg_id": int(x_tg_id),
        "members": formatted_members
    }

    if "squads" not in t: t["squads"] = {}
    t["squads"][squad_code] = new_squad

    if x_tg_id in db["users"]:
        if "joined_tournaments" not in db["users"][x_tg_id]:
            db["users"][x_tg_id]["joined_tournaments"] = []
        db["users"][x_tg_id]["joined_tournaments"].append(t_id)

    save_db(db)

    auth_token = generate_signed_gateway_token(int(x_tg_id), "leader", squad_code)
    task_link = t.get("task_link") or "https://google.com"

    return {
        "status": "success",
        "squad_code": squad_code,
        "task_type": t.get("task_type", "LINK"),
        "task_link": task_link,
        "auth_token": auth_token
    }

@app.post("/api/tournaments/join-squad")
def join_squad(req: JoinSquadRequest, x_tg_id: str = Header(None)):
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Telegram ID Missing")

    if not req.nickname or not req.nickname.strip():
        raise HTTPException(status_code=400, detail="ফ্রি ফায়ার স্টাইল নাম প্রদান করা আবশ্যক!")

    db = get_db()
    found_squad = None
    target_tournament = None

    for t_id, t in db.get("tournaments", {}).items():
        if req.squad_code in t.get("squads", {}):
            found_squad = t["squads"][req.squad_code]
            target_tournament = t

            for sq in t.get("squads", {}).values():
                for member in sq.get("members", []):
                    if str(member.get("tg_id")) == str(x_tg_id):
                        raise HTTPException(
                            status_code=400, 
                            detail="আপনি ইতিমধ্যে এই টুর্নামেন্টের একটি স্কোয়াডে জয়েন করে আছেন!"
                        )
            break

    if not found_squad:
        raise HTTPException(status_code=404, detail="ইনভালিন্ড স্কোয়াড কোড!")

    if len(found_squad["members"]) >= 4:
        raise HTTPException(status_code=400, detail="এই স্কোয়াডে ইতিমধ্যে ৪ জন প্লেয়ার পূর্ণ হয়ে গেছে!")

    found_squad["members"].append({
        "tg_id": int(x_tg_id),
        "nickname": req.nickname.strip(),
        "ff_id": (req.ff_id or "N/A").strip(),
        "role": "MEMBER"
    })

    save_db(db)
    
    auth_token = generate_signed_gateway_token(int(x_tg_id), "member", req.squad_code)
    task_link = target_tournament.get("task_link") or "https://google.com"

    return {
        "status": "success",
        "task_type": target_tournament.get("task_type", "LINK"),
        "task_link": task_link,
        "auth_token": auth_token
    }

@app.post("/api/sync-offline")
def sync_offline_registrations(payload: OfflineSyncPayload, x_tg_id: str = Header(None)):
    """ Offline Fallback Sync Endpoint """
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Telegram ID Missing")

    synced_count = 0
    for reg in payload.registrations:
        try:
            register_leader(reg, x_tg_id=x_tg_id)
            synced_count += 1
        except Exception:
            pass

    return {"status": "success", "synced_count": synced_count}

@app.get("/api/user/my-squads")
def get_my_squads(x_tg_id: str = Header(None)):
    if not x_tg_id:
        return {"squads": []}

    db = get_db()
    my_squads = []

    for t_id, t in db.get("tournaments", {}).items():
        for sq_code, sq in t.get("squads", {}).items():
            for m in sq.get("members", []):
                if str(m.get("tg_id")) == str(x_tg_id):
                    my_squads.append({
                        "tournament_id": t_id,
                        "tournament_title": t.get("title", "Match"),
                        "squad_name": sq.get("squad_name"),
                        "squad_code": sq_code,
                        "members": sq.get("members", []),
                        "room_id": t.get("room_id") if t.get("room_id") != "PROTECTED" else "",
                        "room_password": t.get("room_password") if t.get("room_password") != "PROTECTED" else "",
                        "is_cancelled": t.get("is_cancelled", False),
                        "cancel_message": t.get("cancel_message", "")
                    })

    return {"squads": my_squads}

@app.delete("/api/tournaments/squad/{sq_code}")
def delete_squad(sq_code: str, x_tg_id: str = Header(None)):
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Telegram ID Missing")

    db = get_db()
    removed = False

    for t_id, t in db.get("tournaments", {}).items():
        if sq_code in t.get("squads", {}):
            sq = t["squads"][sq_code]
            if str(sq.get("leader_tg_id")) == str(x_tg_id) or str(x_tg_id) == str(MAIN_ADMIN_ID):
                del t["squads"][sq_code]
                removed = True
                break
            else:
                sq["members"] = [m for m in sq["members"] if str(m.get("tg_id")) != str(x_tg_id)]
                removed = True
                break

    if removed:
        save_db(db)
        return {"status": "success"}

    raise HTTPException(status_code=404, detail="Squad Not Found")

@app.delete("/api/tournaments/{t_id}")
def delete_tournament(t_id: str, x_tg_id: str = Header(None)):
    db = get_db()
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="Not Found")

    t = db["tournaments"][t_id]
    if str(t.get("creator_id")) != str(x_tg_id) and str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Permission Denied")

    del db["tournaments"][t_id]
    save_db(db)
    return {"status": "success"}

@app.post("/api/tournaments/upload-room")
def upload_room(req: UploadRoomRequest, x_tg_id: str = Header(None)):
    db = get_db()
    t_id = req.tournament_id
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="Tournament Not Found")

    t = db["tournaments"][t_id]
    if str(t.get("creator_id")) != str(x_tg_id) and str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Permission Denied")

    t["room_id"] = req.room_id
    t["room_password"] = req.room_password
    if req.new_start_time:
        t["start_time"] = req.new_start_time

    save_db(db)
    return {"status": "success"}

@app.post("/api/tournaments/create")
def create_tournament(req: CreateTournamentRequest, x_tg_id: str = Header(None)):
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Telegram ID Missing")

    db = get_db()
    role = get_user_role(db, int(x_tg_id))
    if role not in ["CREATOR", "MAIN_ADMIN"]:
        raise HTTPException(status_code=403, detail="আপনার টুর্নামেন্ট তৈরি করার অনুমতি নেই!")

    # Role specific task enforcement
    task_type = req.task_type or "LINK"
    if role == "CREATOR" and task_type == "MONETAG_AD":
        task_type = "LINK" # Sub admin defaults to website link

    t_id = "tourn_" + secrets.token_hex(4)

    start_ts = None
    try:
        dt = datetime.fromisoformat(req.start_time.replace("Z", "+00:00"))
        start_ts = dt.timestamp()
    except Exception:
        start_ts = time.time()

    db["tournaments"][t_id] = {
        "creator_id": int(x_tg_id),
        "title": req.title,
        "code": req.code,
        "prize": req.prize,
        "task_type": task_type,
        "task_description": req.task_description or "",
        "task_link": req.task_link or "",
        "rules": req.rules or "",
        "start_time": req.start_time,
        "start_timestamp": start_ts,
        "squads": {},
        "room_id": "",
        "room_password": "",
        "is_cancelled": False,
        "cancel_message": ""
    }

    save_db(db)
    return {"status": "success", "tournament_id": t_id}

@app.post("/api/creator/profile")
def save_creator_profile(req: CreatorProfileRequest, x_tg_id: str = Header(None)):
    db = get_db()
    str_id = str(req.telegram_id)
    db["creators"][str_id] = req.dict()
    save_db(db)
    return {"status": "success"}

@app.get("/api/hosts/{creator_id}")
def get_host_profile(creator_id: int):
    db = get_db()
    host = db.get("creators", {}).get(str(creator_id))
    if not host:
        return {
            "squad_name": "Official Host Squad",
            "description": "No description provided.",
            "player_roles": "N/A"
        }
    return host

@app.post("/api/user/unlock-ad")
def unlock_ad(x_tg_id: str = Header(None)):
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Telegram ID Missing")

    db = get_db()
    db["ad_unlocks"][str(x_tg_id)] = True
    save_db(db)
    return {"status": "success"}

# --- ADMIN ENDPOINTS ---

@app.get("/api/admin/dashboard")
def admin_dashboard(x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    return {
        "total_users": len(db.get("users", {})),
        "active_tournaments": len(db.get("tournaments", {})),
        "total_ad_views": len(db.get("ad_unlocks", {})),
        "users": list(db.get("users", {}).values()),
        "banned_users": db.get("banned_users", []),
        "announcements": db.get("announcements", []),
        "creators": list(db.get("creators", {}).values())
    }

@app.post("/api/admin/announcement/add")
def add_announcement(req: AnnouncementRequest, x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    ann_id = "ann_" + secrets.token_hex(3)
    db["announcements"].append({
        "id": ann_id,
        "text": req.text,
        "image_url": req.image_url
    })
    save_db(db)
    return {"status": "success"}

@app.delete("/api/admin/announcement/{ann_id}")
def delete_announcement(ann_id: str, x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    db["announcements"] = [a for a in db.get("announcements", []) if a.get("id") != ann_id]
    save_db(db)
    return {"status": "success"}

@app.post("/api/admin/creators/save")
def add_creator(req: CreatorAddRequest, x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    db["creators"][str(req.telegram_id)] = {
        "telegram_id": req.telegram_id,
        "squad_name": req.squad_name
    }
    save_db(db)
    return {"status": "success"}

@app.delete("/api/admin/creators/{creator_id}")
def remove_creator(creator_id: int, x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    if str(creator_id) in db.get("creators", {}):
        del db["creators"][str(creator_id)]
        save_db(db)

    return {"status": "success"}

@app.post("/api/admin/users/ban")
def ban_user(req: BanUserRequest, x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    if req.telegram_id not in db["banned_users"]:
        db["banned_users"].append(req.telegram_id)
        save_db(db)

    return {"status": "success"}

@app.post("/api/admin/users/unban")
def unban_user(req: BanUserRequest, x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    db = get_db()
    if req.telegram_id in db["banned_users"]:
        db["banned_users"].remove(req.telegram_id)
        save_db(db)

    return {"status": "success"}

@app.post("/api/admin/import-data")
def import_database(json_data: Dict[str, Any] = Body(...), x_tg_id: str = Header(None)):
    if str(x_tg_id) != str(MAIN_ADMIN_ID):
        raise HTTPException(status_code=403, detail="Access Denied")

    save_db(json_data)
    return {"status": "success", "message": "ডাটাবেজ সফলভাবে রিস্টোর/ইমপোর্ট করা হয়েছে!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

