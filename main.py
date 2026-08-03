
import os
import time
import json
import uuid
import hmac
import hashlib
import asyncio
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import filelock

DATA_FILE = os.getenv("DATA_FILE", "data.json")
LOCK_FILE = f"{DATA_FILE}.lock"
BACKUP_FILE = f"{DATA_FILE}.bak"

MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "8908999062"))
SERVER_SECRET_KEY = os.getenv("SERVER_SECRET_KEY", "FF_ESPORTS_SUPER_SECRET_KEY_998877")

lock = filelock.FileLock(LOCK_FILE, timeout=10)

app = FastAPI(title="Free Fire Esports Engine", version="8.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def generate_signed_token(tg_id: int, role: str, squad_code: str) -> str:
    exp = int(time.time()) + 300
    payload = f"{tg_id}:{role}:{squad_code}:{exp}"
    signature = hmac.new(SERVER_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def get_default_db() -> Dict[str, Any]:
    return {
        "users": {},
        "creators": {},
        "tournaments": {},
        "banned_users": [],
        "announcements": [],
        "ad_views_count": 0
    }

def read_db() -> Dict[str, Any]:
    with lock:
        if not os.path.exists(DATA_FILE):
            default_data = get_default_db()
            write_db_atomic_internal(default_data)
            return default_data
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key in ["users", "creators", "tournaments", "banned_users", "announcements"]:
                    if key not in data:
                        data[key] = {} if key not in ["banned_users", "announcements"] else []
                if "ad_views_count" not in data:
                    data["ad_views_count"] = 0
                return data
        except Exception:
            if os.path.exists(BACKUP_FILE):
                try:
                    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return get_default_db()

def write_db_atomic_internal(data: Dict[str, Any]):
    if os.path.exists(DATA_FILE):
        try:
            os.replace(DATA_FILE, BACKUP_FILE)
        except Exception:
            pass
    temp_file = f"{DATA_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, DATA_FILE)

def write_db_atomic(data: Dict[str, Any]):
    with lock:
        write_db_atomic_internal(data)

def cleanup_deleted_tournament_members(db: Dict[str, Any], t_id: str):
    """গ্লিচ প্রতিরোধ: টুর্নামেন্ট ডিলিট হলে প্লেয়ারদের জয়েন স্ট্যাটাস ক্লিয়ার করা"""
    if t_id in db.get("tournaments", {}):
        t = db["tournaments"][t_id]
        for sq in t.get("squads", {}).values():
            for m in sq.get("members", []):
                tg_str = str(m.get("tg_id"))
                if tg_str in db.get("users", {}):
                    db["users"][tg_str]["active_tournament_id"] = None

@app.on_event("startup")
async def startup_lifecycle():
    asyncio.create_task(tournament_auto_scheduler())

async def tournament_auto_scheduler():
    """৭ মিনিট রুম আইডি নোটিশ ও ১৭ মিনিট অটো-ডিলিট ব্যাকগ্রাউন্ড টাস্ক"""
    while True:
        await asyncio.sleep(20)
        try:
            db = read_db()
            now = int(time.time())
            updated = False
            
            for t_id, t in list(db.get("tournaments", {}).items()):
                start_ts = t.get("start_timestamp", 0)
                has_creds = bool(t.get("room_id") and t.get("room_password"))
                
                # রুম আইডি ছাড়া ৭ মিনিট পেরিয়ে গেলে নোটিশ সেট করা
                if not has_creds and start_ts > 0 and now >= (start_ts + 420):
                    if not t.get("is_cancelled"):
                        t["is_cancelled"] = True
                        t["cancel_message"] = "দুঃখজনক! কোনো কারণে এই টুর্নামেন্টটি খেলা হবে না। আমরা আন্তরিকভাবে দুঃখিত, অন্য একটি টুর্নামেন্টে স্কোয়াড রেজিস্ট্রেশন করুন।"
                        updated = True
                
                # ৭ মিনিট নোটিশের ৫ মিনিট পর (১২ মিনিট) ক্যানসেল করা টুর্নামেন্ট অটো ডিলিট
                if not has_creds and start_ts > 0 and now >= (start_ts + 720):
                    cleanup_deleted_tournament_members(db, t_id)
                    del db["tournaments"][t_id]
                    updated = True
                
                # রুম আইডি দেওয়া অবস্থায় খেলা শুরু হলে ১৭ মিনিট পর অটো ডিলিট
                if t.get("status") == "STARTED" and t.get("started_at", 0) > 0 and now >= (t["started_at"] + 1020):
                    cleanup_deleted_tournament_members(db, t_id)
                    del db["tournaments"][t_id]
                    updated = True

            if updated:
                write_db_atomic(db)
        except Exception as e:
            pass

# Schemas
class UserAuth(BaseModel):
    telegram_id: int
    username: Optional[str] = ""
    first_name: str
    photo_url: Optional[str] = ""

class UserVerify(BaseModel):
    ff_uid: str
    whatsapp_number: str

class CreatorProfile(BaseModel):
    telegram_id: int
    squad_name: str = "Host Squad"
    description: str = ""
    logo: str = ""
    player_roles: str = ""
    facebook: str = ""
    instagram: str = ""
    tiktok: str = ""
    youtube: str = ""
    status: str = "ACTIVE"

class TournamentCreate(BaseModel):
    title: str
    code: str
    mode: str = "Squad"
    max_slots: int = 12
    prize: str
    task_description: str
    task_link: str
    rules: str
    start_time: str
    start_timestamp: Optional[int] = 0

class RoomUpload(BaseModel):
    tournament_id: str
    room_id: str
    room_password: str
    new_start_time: Optional[str] = None

class LeaderRegistration(BaseModel):
    tournament_id: str
    squad_name: str
    p1_ff_id: str
    p1_nickname: str

class JoinSquadByCode(BaseModel):
    squad_code: str
    ff_id: str
    nickname: str

class AdminAnnouncementCreate(BaseModel):
    text: str
    image_url: Optional[str] = ""

def get_role(tg_id: int, db: Dict[str, Any]) -> str:
    if tg_id == MAIN_ADMIN_ID:
        return "MAIN_ADMIN"
    tg_str = str(tg_id)
    if tg_str in db["creators"] and db["creators"][tg_str].get("status", "ACTIVE") == "ACTIVE":
        return "CREATOR"
    return "USER"

def user_has_active_tournament(tg_id: int, db: Dict[str, Any]) -> bool:
    """নিশ্চিত করে ইউজার কোনো টুর্নামেন্টে ইতিমধ্যে সক্রিয় রয়েছে কিনা"""
    for t_id, t in db["tournaments"].items():
        for sq in t.get("squads", {}).values():
            for m in sq.get("members", []):
                if m.get("tg_id") == tg_id:
                    return True
    return False

# Routes
@app.post("/api/auth/init")
async def init_user(auth_data: UserAuth):
    db = read_db()
    tg_id_str = str(auth_data.telegram_id)
    
    if auth_data.telegram_id in db.get("banned_users", []) and auth_data.telegram_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="আপনি এই প্ল্যাটফর্ম থেকে নিষিদ্ধ (Banned) হয়েছেন।")
    
    now = int(time.time())
    if tg_id_str not in db["users"]:
        db["users"][tg_id_str] = {
            "telegram_id": auth_data.telegram_id,
            "username": auth_data.username,
            "first_name": auth_data.first_name,
            "photo_url": auth_data.photo_url,
            "joined_date": now,
            "unlock_until": 0,
            "ff_uid": "",
            "whatsapp": "",
            "is_verified": False,
            "active_tournament_id": None
        }
    else:
        db["users"][tg_id_str]["username"] = auth_data.username
        db["users"][tg_id_str]["first_name"] = auth_data.first_name
        if auth_data.photo_url:
            db["users"][tg_id_str]["photo_url"] = auth_data.photo_url

    write_db_atomic(db)
    return {
        "status": "success",
        "user": db["users"][tg_id_str],
        "role": get_role(auth_data.telegram_id, db),
        "is_unlocked": db["users"][tg_id_str].get("unlock_until", 0) > now,
        "announcements": db.get("announcements", [])
    }

@app.post("/api/user/verify")
async def verify_user_profile(v_data: UserVerify, x_tg_id: int = Header(...)):
    db = read_db()
    tg_id_str = str(x_tg_id)
    if tg_id_str not in db["users"]:
        raise HTTPException(status_code=404, detail="ইউজার পাওয়া যায়নি")
    
    db["users"][tg_id_str]["ff_uid"] = v_data.ff_uid
    db["users"][tg_id_str]["whatsapp"] = v_data.whatsapp_number
    db["users"][tg_id_str]["is_verified"] = True
    write_db_atomic(db)
    return {"status": "success", "user": db["users"][tg_id_str]}

@app.post("/api/user/unlock-ad")
async def unlock_ad(x_tg_id: int = Header(...)):
    db = read_db()
    tg_id_str = str(x_tg_id)
    if tg_id_str not in db["users"]:
        raise HTTPException(status_code=404, detail="ইউজার পাওয়া যায়নি")
    
    db["users"][tg_id_str]["unlock_until"] = int(time.time()) + (24 * 3600)
    db["ad_views_count"] = db.get("ad_views_count", 0) + 1
    write_db_atomic(db)
    return {"status": "success", "unlock_until": db["users"][tg_id_str]["unlock_until"]}

@app.get("/api/tournaments")
async def get_tournaments(x_tg_id: Optional[int] = Header(None)):
    db = read_db()
    tournaments_list = []
    
    for t in db["tournaments"].values():
        t_copy = json.loads(json.dumps(t))
        
        # এক্সেস ফিল্টারিং: রুম আইডি এবং পাসওয়ার্ড কেবল যুক্ত প্লেয়ার বা এডমিন দেখতে পাবে
        is_participant = False
        if x_tg_id:
            for sq in t_copy.get("squads", {}).values():
                for m in sq.get("members", []):
                    if m.get("tg_id") == x_tg_id:
                        is_participant = True
                        break
        
        is_creator_or_admin = (x_tg_id == MAIN_ADMIN_ID or (x_tg_id and t_copy.get("creator_id") == x_tg_id))
        
        if not (is_participant or is_creator_or_admin):
            t_copy["room_id"] = "PROTECTED"
            t_copy["room_password"] = "PROTECTED"
            t_copy["has_credentials"] = bool(t.get("room_id") and t.get("room_password"))
        else:
            t_copy["has_credentials"] = bool(t.get("room_id") and t.get("room_password"))
            
        tournaments_list.append(t_copy)
        
    return {"tournaments": tournaments_list}

@app.post("/api/tournaments/create")
async def create_tournament(t_data: TournamentCreate, x_tg_id: int = Header(...)):
    db = read_db()
    role = get_role(x_tg_id, db)
    if role not in ["CREATOR", "MAIN_ADMIN"]:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই।")

    t_id = f"TOR_{int(time.time())}"
    creator_info = db["creators"].get(str(x_tg_id), {})
    host_name = creator_info.get("squad_name", "Official Host Squad")
    
    start_ts = t_data.start_timestamp if t_data.start_timestamp and t_data.start_timestamp > 0 else int(time.time()) + 1800
    
    db["tournaments"][t_id] = {
        "id": t_id,
        "title": t_data.title,
        "code": t_data.code,
        "mode": "Squad (4 Players)",
        "max_slots": 12,
        "prize": t_data.prize,
        "task_description": t_data.task_description,
        "task_link": t_data.task_link,
        "rules": t_data.rules,
        "start_time": t_data.start_time,
        "start_timestamp": start_ts,
        "creator_id": x_tg_id,
        "creator_name": host_name,
        "status": "UPCOMING",
        "room_id": "",
        "room_password": "",
        "is_cancelled": False,
        "cancel_message": "",
        "total_joined_squads": 0,
        "squads": {}
    }
    write_db_atomic(db)
    return {"status": "success", "tournament_id": t_id}

@app.post("/api/tournaments/upload-room")
async def upload_room_credentials(req: RoomUpload, x_tg_id: int = Header(...)):
    db = read_db()
    t_id = req.tournament_id
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="টুর্নামেন্ট খুঁজে পাওয়া যায়নি")
        
    t = db["tournaments"][t_id]
    if x_tg_id != MAIN_ADMIN_ID and t["creator_id"] != x_tg_id:
        raise HTTPException(status_code=403, detail="অনুমতি নেই")

    t["room_id"] = req.room_id
    t["room_password"] = req.room_password
    t["status"] = "STARTED"
    t["started_at"] = int(time.time())
    t["is_cancelled"] = False
    t["cancel_message"] = ""
    
    if req.new_start_time:
        t["start_time"] = req.new_start_time

    write_db_atomic(db)
    return {"status": "success", "message": "রুম আইডি ও পাসওয়ার্ড সফলভাবে আপলোড হয়েছে! টুর্নামেন্ট এখন রানিং।"}

@app.post("/api/tournaments/register-leader")
async def register_leader(reg: LeaderRegistration, x_tg_id: int = Header(...)):
    db = read_db()
    if user_has_active_tournament(x_tg_id, db):
        raise HTTPException(status_code=400, detail="আপনি ইতিমধ্যে একটি টুর্নামেন্টে যুক্ত আছেন!")

    t_id = reg.tournament_id
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="টুর্নামেন্টটি খুঁজে পাওয়া যায়নি।")
    
    t = db["tournaments"][t_id]
    if t["total_joined_squads"] >= 12:
        raise HTTPException(status_code=400, detail="টুর্নামেন্টের মোট ১২টি স্কোয়াড স্লট ইতিমধ্যে পূর্ণ হয়ে গেছে!")

    squad_code = f"SQ-{uuid.uuid4().hex[:6].upper()}"
    t["squads"][squad_code] = {
        "squad_code": squad_code,
        "squad_name": reg.squad_name,
        "leader_tg_id": x_tg_id,
        "tournament_id": t_id,
        "tournament_title": t["title"],
        "members": [{"tg_id": x_tg_id, "nickname": reg.p1_nickname, "ff_id": reg.p1_ff_id, "role": "Leader"}]
    }
    t["total_joined_squads"] += 1
    
    tg_str = str(x_tg_id)
    if tg_str in db["users"]:
        db["users"][tg_str]["active_tournament_id"] = t_id

    write_db_atomic(db)
    
    auth_token = generate_signed_token(x_tg_id, "leader", squad_code)
    return {
        "status": "success", 
        "squad_code": squad_code, 
        "task_link": t["task_link"],
        "auth_token": auth_token
    }

@app.post("/api/tournaments/join-squad")
async def join_squad(req: JoinSquadByCode, x_tg_id: int = Header(...)):
    db = read_db()
    if user_has_active_tournament(x_tg_id, db):
        raise HTTPException(status_code=400, detail="আপনি ইতিমধ্যে একটি টুর্নামেন্টে যুক্ত আছেন!")

    target_squad = None
    target_tournament = None
    for t_id, t in db["tournaments"].items():
        if req.squad_code in t["squads"]:
            target_squad = t["squads"][req.squad_code]
            target_tournament = t
            break

    if not target_squad:
        raise HTTPException(status_code=404, detail="ভুল স্কোয়াড কোড! কোডটি পুনরায় চেক করুন।")

    if len(target_squad["members"]) >= 4:
        raise HTTPException(status_code=400, detail="এই স্কোয়াডে ইতিমধ্যে ৪ জন প্লেয়ার পূর্ণ হয়ে গেছে!")

    target_squad["members"].append({
        "tg_id": x_tg_id, "nickname": req.nickname, "ff_id": req.ff_id, "role": "Member"
    })

    tg_str = str(x_tg_id)
    if tg_str in db["users"]:
        db["users"][tg_str]["active_tournament_id"] = target_tournament["id"]

    write_db_atomic(db)
    
    auth_token = generate_signed_token(x_tg_id, "member", req.squad_code)
    return {
        "status": "success", 
        "message": "স্কোয়াডে জয়েন সফল হয়েছে!", 
        "task_link": target_tournament["task_link"],
        "auth_token": auth_token
    }

@app.get("/api/user/my-squads")
async def get_my_squads(x_tg_id: int = Header(...)):
    db = read_db()
    user_squads = []
    for t_id, t in db["tournaments"].items():
        for sq_code, sq in t.get("squads", {}).items():
            is_in_squad = any(m.get("tg_id") == x_tg_id for m in sq.get("members", []))
            if is_in_squad:
                sq_data = json.loads(json.dumps(sq))
                sq_data["room_id"] = t.get("room_id", "")
                sq_data["room_password"] = t.get("room_password", "")
                sq_data["tournament_title"] = t.get("title", "")
                sq_data["start_time"] = t.get("start_time", "")
                sq_data["is_cancelled"] = t.get("is_cancelled", False)
                sq_data["cancel_message"] = t.get("cancel_message", "")
                user_squads.append(sq_data)
    return {"squads": user_squads}

@app.delete("/api/tournaments/squad/{squad_code}")
async def delete_squad(squad_code: str, x_tg_id: int = Header(...)):
    db = read_db()
    found = False
    for t_id, t in db["tournaments"].items():
        if squad_code in t.get("squads", {}):
            sq = t["squads"][squad_code]
            if sq.get("leader_tg_id") == x_tg_id or x_tg_id == MAIN_ADMIN_ID:
                for m in sq.get("members", []):
                    tg_str = str(m.get("tg_id"))
                    if tg_str in db["users"]:
                        db["users"][tg_str]["active_tournament_id"] = None
                t["total_joined_squads"] -= 1
                if t["total_joined_squads"] < 0: t["total_joined_squads"] = 0
                del t["squads"][squad_code]
                found = True
                break
    if found:
        write_db_atomic(db)
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Squad not found or unauthorized")

@app.delete("/api/tournaments/{tournament_id}")
async def delete_tournament(tournament_id: str, x_tg_id: int = Header(...)):
    db = read_db()
    if tournament_id in db["tournaments"]:
        t = db["tournaments"][tournament_id]
        if x_tg_id == MAIN_ADMIN_ID or t["creator_id"] == x_tg_id:
            cleanup_deleted_tournament_members(db, tournament_id)
            del db["tournaments"][tournament_id]
            write_db_atomic(db)
            return {"status": "success"}
    raise HTTPException(status_code=403, detail="অনুমতি নেই")

@app.get("/api/hosts/{creator_id}")
async def get_host(creator_id: int):
    db = read_db()
    return db["creators"].get(str(creator_id), {})

@app.post("/api/creator/profile")
async def update_creator_profile(prof: CreatorProfile, x_tg_id: int = Header(...)):
    db = read_db()
    role = get_role(x_tg_id, db)
    if role not in ["CREATOR", "MAIN_ADMIN"]:
        raise HTTPException(status_code=403, detail="অনুমতি নেই")
    
    tg_str = str(x_tg_id)
    if tg_str not in db["creators"]:
        db["creators"][tg_str] = prof.dict()
    else:
        db["creators"][tg_str].update(prof.dict(exclude_unset=True))
    write_db_atomic(db)
    return {"status": "success"}

# Admin APIs
@app.get("/api/admin/dashboard")
async def admin_dashboard(x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    db = read_db()
    return {
        "total_users": len(db["users"]),
        "active_tournaments": len(db["tournaments"]),
        "total_ad_views": db.get("ad_views_count", 0),
        "users": list(db["users"].values()),
        "creators": list(db["creators"].values()),
        "banned_users": db.get("banned_users", []),
        "announcements": db.get("announcements", [])
    }

@app.post("/api/admin/import-data")
async def import_and_merge_data(imported_data: dict, x_tg_id: int = Header(...)):
    """সুপার এডমিন নিখুঁত JSON ব্যাকআপ আপলোড / ডাটা মার্জ সিস্টেম"""
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    
    db = read_db()
    
    # ইউজার মার্জ (বিদ্যমান ডাটা বজায় থাকবে, নতুন তথ্য যুক্ত হবে)
    imported_users = imported_data.get("users", {})
    if isinstance(imported_users, dict):
        for u_id, u_info in imported_users.items():
            if u_id not in db["users"]:
                db["users"][u_id] = u_info
            else:
                for k, v in u_info.items():
                    if v and not db["users"][u_id].get(k):
                        db["users"][u_id][k] = v

    # সাব-এডমিন মার্জ
    imported_creators = imported_data.get("creators", {})
    if isinstance(imported_creators, dict):
        for c_id, c_info in imported_creators.items():
            if c_id not in db["creators"]:
                db["creators"][c_id] = c_info

    # টুর্নামেন্ট মার্জ
    imported_tournaments = imported_data.get("tournaments", {})
    if isinstance(imported_tournaments, dict):
        for t_id, t_info in imported_tournaments.items():
            if t_id not in db["tournaments"]:
                db["tournaments"][t_id] = t_info

    write_db_atomic(db)
    return {"status": "success", "message": "ডাটাবেজ ব্যাকআপ সফলভাবে মার্জ এবং সংরক্ষণ করা হয়েছে!"}

@app.post("/api/admin/announcement/add")
async def add_announcement(ann: AdminAnnouncementCreate, x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    db = read_db()
    ann_id = f"ANN_{int(time.time()*1000)}"
    new_ann = {
        "id": ann_id,
        "text": ann.text,
        "image_url": ann.image_url or "",
        "created_at": int(time.time())
    }
    db.setdefault("announcements", []).append(new_ann)
    write_db_atomic(db)
    return {"status": "success", "announcement": new_ann}

@app.delete("/api/admin/announcement/{ann_id}")
async def delete_announcement(ann_id: str, x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    db = read_db()
    db["announcements"] = [a for a in db.get("announcements", []) if a.get("id") != ann_id]
    write_db_atomic(db)
    return {"status": "success"}

@app.post("/api/admin/creators/save")
async def save_creator(creator: CreatorProfile, x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    db = read_db()
    db["creators"][str(creator.telegram_id)] = creator.dict()
    write_db_atomic(db)
    return {"status": "success"}

@app.delete("/api/admin/creators/{creator_id}")
async def delete_creator(creator_id: int, x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    db = read_db()
    tg_str = str(creator_id)
    if tg_str in db["creators"]:
        del db["creators"][tg_str]
        write_db_atomic(db)
    return {"status": "success"}

@app.post("/api/admin/users/ban")
async def ban_user(req: Dict[str, int], x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    target_id = req.get("telegram_id")
    if target_id == MAIN_ADMIN_ID:
        raise HTTPException(status_code=400, detail="মেইন এডমিনকে ব্লক বা ব্যান করা যাবে না!")
        
    db = read_db()
    if target_id and target_id not in db["banned_users"]:
        db["banned_users"].append(target_id)
        write_db_atomic(db)
    return {"status": "success"}

@app.post("/api/admin/users/unban")
async def unban_user(req: Dict[str, int], x_tg_id: int = Header(...)):
    if x_tg_id != MAIN_ADMIN_ID:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই")
    target_id = req.get("telegram_id")
    db = read_db()
    if target_id in db["banned_users"]:
        db["banned_users"].remove(target_id)
        write_db_atomic(db)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


