import os
import time
import json
import uuid
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

lock = filelock.FileLock(LOCK_FILE, timeout=10)

app = FastAPI(title="Free Fire Esports Engine", version="7.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_default_db() -> Dict[str, Any]:
    return {
        "users": {},
        "creators": {},
        "tournaments": {},
        "banned_users": [],
        "announcements": [],  # List of dicts: {id, text, image_url, created_at}
        "ad_views_count": 0
    }

# persistent reading logic
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

def schedule_tournament_deletion(tournament_id: str, delay_seconds: int = 1020):
    async def _delete_task():
        await asyncio.sleep(delay_seconds)
        db = read_db()
        if tournament_id in db["tournaments"]:
            del db["tournaments"][tournament_id]
            write_db_atomic(db)
    asyncio.create_task(_delete_task())

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
    max_players: int = 48
    prize: str
    task_description: str
    task_link: str
    rules: str
    start_time: str

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
    for t_id, t in db["tournaments"].items():
        for sq in t.get("squads", {}).values():
            for m in sq.get("members", []):
                if m.get("tg_id") == tg_id:
                    return True
    return False

# API Routes
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
            "is_verified": False
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
        raise HTTPException(status_code=404, detail="User not found")
    
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
        raise HTTPException(status_code=404, detail="User not found")
    
    db["users"][tg_id_str]["unlock_until"] = int(time.time()) + (24 * 3600)
    db["ad_views_count"] = db.get("ad_views_count", 0) + 1
    write_db_atomic(db)
    return {"status": "success", "unlock_until": db["users"][tg_id_str]["unlock_until"]}

@app.get("/api/tournaments")
async def get_tournaments():
    db = read_db()
    return {"tournaments": list(db["tournaments"].values())}

@app.post("/api/tournaments/create")
async def create_tournament(t_data: TournamentCreate, x_tg_id: int = Header(...)):
    db = read_db()
    role = get_role(x_tg_id, db)
    if role not in ["CREATOR", "MAIN_ADMIN"]:
        raise HTTPException(status_code=403, detail="প্রবেশাধিকার নেই।")
    
    to_delete = [t_id for t_id, t in db["tournaments"].items() if t["creator_id"] == x_tg_id]
    for tid in to_delete:
        del db["tournaments"][tid]

    t_id = f"TOR_{int(time.time())}"
    creator_info = db["creators"].get(str(x_tg_id), {})
    host_name = creator_info.get("squad_name", "Official Host Squad")
    
    db["tournaments"][t_id] = {
        "id": t_id,
        "title": t_data.title,
        "code": t_data.code,
        "mode": t_data.mode,
        "max_players": t_data.max_players,
        "prize": t_data.prize,
        "task_description": t_data.task_description,
        "task_link": t_data.task_link,
        "rules": t_data.rules,
        "start_time": t_data.start_time,
        "creator_id": x_tg_id,
        "creator_name": host_name,
        "status": "ACTIVE",
        "total_joined_players": 0,
        "squads": {}
    }
    write_db_atomic(db)
    return {"status": "success", "tournament_id": t_id}

@app.post("/api/tournaments/start-match")
async def start_match(req: Dict[str, str], x_tg_id: int = Header(...)):
    t_id = req.get("tournament_id")
    db = read_db()
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="Tournament not found")
    t = db["tournaments"][t_id]
    
    if x_tg_id != MAIN_ADMIN_ID and t["creator_id"] != x_tg_id:
        raise HTTPException(status_code=403, detail="অনুমতি নেই।")
        
    t["status"] = "STARTED"
    write_db_atomic(db)
    schedule_tournament_deletion(t_id, 1020)
    return {"status": "success", "message": "Match Started! Auto delete in 17 minutes."}

@app.post("/api/tournaments/register-leader")
async def register_leader(reg: LeaderRegistration, x_tg_id: int = Header(...)):
    db = read_db()
    if user_has_active_tournament(x_tg_id, db):
        raise HTTPException(status_code=400, detail="আপনি বর্তমানে একটি টুর্নামেন্টে যুক্ত আছেন!")

    t_id = reg.tournament_id
    if t_id not in db["tournaments"]:
        raise HTTPException(status_code=404, detail="টুর্নামেন্টটি খুঁজে পাওয়া যায়নি।")
    
    t = db["tournaments"][t_id]
    if t["total_joined_players"] >= t["max_players"]:
        raise HTTPException(status_code=400, detail="লবি ইতিমধ্যে পূর্ণ হয়ে গেছে!")

    squad_code = f"SQ-{uuid.uuid4().hex[:6].upper()}"
    t["squads"][squad_code] = {
        "squad_code": squad_code,
        "squad_name": reg.squad_name,
        "leader_tg_id": x_tg_id,
        "tournament_id": t_id,
        "tournament_title": t["title"],
        "members": [{"tg_id": x_tg_id, "nickname": reg.p1_nickname, "ff_id": reg.p1_ff_id, "role": "Leader"}]
    }
    t["total_joined_players"] += 1
    
    if t["total_joined_players"] >= t["max_players"]:
        t["status"] = "FULL_STARTED"
        schedule_tournament_deletion(t_id, 1020)

    write_db_atomic(db)
    return {"status": "success", "squad_code": squad_code, "task_link": t["task_link"]}

@app.post("/api/tournaments/join-squad")
async def join_squad(req: JoinSquadByCode, x_tg_id: int = Header(...)):
    db = read_db()
    if user_has_active_tournament(x_tg_id, db):
        raise HTTPException(status_code=400, detail="আপনি ইতিমধ্যে একটি টুর্নামেন্টে যুক্ত রয়েছেন!")

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
    target_tournament["total_joined_players"] += 1

    if target_tournament["total_joined_players"] >= target_tournament["max_players"]:
        target_tournament["status"] = "FULL_STARTED"
        schedule_tournament_deletion(target_tournament["id"], 1020)

    write_db_atomic(db)
    return {"status": "success", "message": "স্কোয়াডে জয়েন সফল হয়েছে!", "task_link": target_tournament["task_link"]}

@app.get("/api/user/my-squads")
async def get_my_squads(x_tg_id: int = Header(...)):
    db = read_db()
    user_squads = []
    for t_id, t in db["tournaments"].items():
        for sq_code, sq in t.get("squads", {}).items():
            if sq.get("leader_tg_id") == x_tg_id:
                user_squads.append(sq)
    return {"squads": user_squads}

@app.delete("/api/tournaments/squad/{squad_code}")
async def delete_squad(squad_code: str, x_tg_id: int = Header(...)):
    db = read_db()
    found = False
    for t_id, t in db["tournaments"].items():
        if squad_code in t.get("squads", {}):
            sq = t["squads"][squad_code]
            if sq.get("leader_tg_id") == x_tg_id or x_tg_id == MAIN_ADMIN_ID:
                t["total_joined_players"] -= len(sq.get("members", []))
                if t["total_joined_players"] < 0: t["total_joined_players"] = 0
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

# --- Admin Panel APIs ---
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
    
    # 👑 Main Admin Can NOT be banned (সুরক্ষিত)
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
 Header(...)):
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


