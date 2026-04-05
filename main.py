import asyncio
import threading
import json
import os
import uuid
import hashlib
import requests as req_lib
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, Response, session
from flask_cors import CORS

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)

TOKEN       = os.environ.get("BOT_TOKEN", "7960644035:AAFtxTBpMzr7FwaDNcce4rkKsVxjjerQkz4")
ABOUT_IMAGE = "https://img2.teletype.in/files/11/02/1102b31a-e987-4445-8b29-9cfc25f905d6.jpeg"
DATA_FILE   = "data.json"
CONFIG_FILE = "config.json"
PORT        = int(os.environ.get("PORT", 5000))
SECRET_KEY  = os.environ.get("SECRET_KEY", "mudarris-secret-2026")
APP_URL     = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")

# ── Default users (login/parol) ────────────────────────────────────────
DEFAULT_USERS = {
    "admin":     {"password": hashlib.md5(b"admin123").hexdigest(),    "role": "super_admin", "name": "Super Admin"},
    "hr":        {"password": hashlib.md5(b"hr1234").hexdigest(),       "role": "hr_manager",  "name": "HR Manager"},
    "recruiter": {"password": hashlib.md5(b"recruit123").hexdigest(),   "role": "recruiter",   "name": "Recruiter"},
}

# ── Pipeline stages ────────────────────────────────────────────────────
PIPELINE_STAGES = [
    {"id": "new",       "label": "Yangi ariza",      "color": "#3b82f6"},
    {"id": "reviewed",  "label": "Ko'rib chiqildi",  "color": "#f59e0b"},
    {"id": "interview", "label": "Intervyu",          "color": "#f97316"},
    {"id": "offer",     "label": "Taklif yuborildi", "color": "#a78bfa"},
    {"id": "hired",     "label": "Qabul qilindi",    "color": "#22c55e"},
    {"id": "rejected",  "label": "Rad etildi",       "color": "#ef4444"},
]

# ── Reply templates ────────────────────────────────────────────────────
DEFAULT_TEMPLATES = [
    {"id": "t1", "name": "Ariza qabul",       "text": "✅ Arizangiz qabul qilindi! Tez orada ko'rib chiqamiz."},
    {"id": "t2", "name": "Ko'rib chiqilmoqda","text": "👀 Arizangiz ko'rib chiqilmoqda. Natija 3-5 ish kuni ichida ma'lum qilinadi."},
    {"id": "t3", "name": "Intervyuga taklif", "text": "🎉 Tabriklaymiz! Siz intervyuga taklif etilmoqdasiz. Iltimos, qulayroq vaqt haqida xabar bering."},
    {"id": "t4", "name": "Rad etish",         "text": "😔 Afsuski, hozircha jarayonni siz bilan davom ettira olmaymiz. Kelajakda omad tilaymiz!"},
    {"id": "t5", "name": "Qabul qilindi",     "text": "🌟 Tabriklaymiz! Siz saralashdan muvaffaqiyatli o'tdingiz. HR xodimimiz siz bilan bog'lanadi."},
    {"id": "t6", "name": "Qo'shimcha hujjat", "text": "📎 Iltimos, qo'shimcha hujjatlar (diplom, sertifikat) yuborishingizni so'raymiz."},
]

# ── In-memory cache ────────────────────────────────────────────────────
_data_cache   = None
_config_cache = None
_lock = threading.Lock()

def load_data():
    global _data_cache
    if _data_cache is not None: return _data_cache
    if not os.path.exists(DATA_FILE):
        _data_cache = {"applications":[],"contacts":[],"stats":{"total":0,"reviewed":0,"pending":0,"completed":0},"vacancies":[],"templates":DEFAULT_TEMPLATES}
    else:
        with open(DATA_FILE,"r",encoding="utf-8") as f: _data_cache = json.load(f)
    # ensure vacancies & templates exist
    _data_cache.setdefault("vacancies", [])
    _data_cache.setdefault("templates", DEFAULT_TEMPLATES)
    return _data_cache

def save_data(data):
    global _data_cache
    _data_cache = data
    with open(DATA_FILE,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)

def load_config():
    global _config_cache
    if _config_cache is not None: return _config_cache
    if not os.path.exists(CONFIG_FILE):
        _config_cache = {"buttons":[],"welcome_message":"Xush kelibsiz!"}
    else:
        with open(CONFIG_FILE,"r",encoding="utf-8") as f: _config_cache = json.load(f)
    return _config_cache

def save_config(config):
    global _config_cache
    _config_cache = config
    with open(CONFIG_FILE,"w",encoding="utf-8") as f: json.dump(config,f,ensure_ascii=False,indent=2)

# ── Helpers ────────────────────────────────────────────────────────────
def find_and_update(buttons,btn_id,new_data):
    for i,btn in enumerate(buttons):
        if btn["id"]==btn_id: buttons[i].update(new_data); return True
        if find_and_update(btn.get("children",[]),btn_id,new_data): return True
    return False

def find_and_delete(buttons,btn_id):
    for i,btn in enumerate(buttons):
        if btn["id"]==btn_id: buttons.pop(i); return True
        if find_and_delete(btn.get("children",[]),btn_id): return True
    return False

def find_parent_and_add(buttons,parent_id,new_btn):
    for btn in buttons:
        if btn["id"]==parent_id:
            btn.setdefault("children",[]).append(new_btn); btn["type"]="menu"; return True
        if find_parent_and_add(btn.get("children",[]),parent_id,new_btn): return True
    return False

bot_loop = None

# ── Flask ──────────────────────────────────────────────────────────────
flask_app = Flask(__name__, static_folder=".")
flask_app.secret_key = SECRET_KEY
CORS(flask_app, supports_credentials=True)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "Kirish talab etiladi"}), 401
        return f(*args, **kwargs)
    return decorated

def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user"):
                return jsonify({"error": "Kirish talab etiladi"}), 401
            if session["user"].get("role") not in roles:
                return jsonify({"error": "Ruxsat yo'q"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── AUTH ──────────────────────────────────────────────────────────────
@flask_app.route("/api/login", methods=["POST"])
def login():
    d = request.json or {}
    username = d.get("username","").strip()
    password = d.get("password","").strip()
    pw_hash  = hashlib.md5(password.encode()).hexdigest()
    user = DEFAULT_USERS.get(username)
    if not user or user["password"] != pw_hash:
        return jsonify({"error": "Login yoki parol noto'g'ri"}), 401
    session["user"] = {"username": username, "role": user["role"], "name": user["name"]}
    session.permanent = True
    return jsonify({"success": True, "user": session["user"]})

@flask_app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@flask_app.route("/api/me", methods=["GET"])
def me():
    if session.get("user"):
        return jsonify({"user": session["user"]})
    return jsonify({"user": None})

# ── MAIN PAGE ──────────────────────────────────────────────────────────
@flask_app.route("/")
def index(): return send_from_directory(".","admin.html")

# ── ALL DATA (combined) ────────────────────────────────────────────────
@flask_app.route("/api/all", methods=["GET"])
@login_required
def get_all():
    with _lock:
        data   = load_data()
        config = load_config()
    return jsonify({
        "applications": sorted(data.get("applications",[]), key=lambda x: x["id"], reverse=True),
        "contacts":     sorted(data.get("contacts",[]),     key=lambda x: x["id"], reverse=True),
        "stats":        data.get("stats", {}),
        "config":       config,
        "vacancies":    data.get("vacancies", []),
        "templates":    data.get("templates", DEFAULT_TEMPLATES),
        "pipeline_stages": PIPELINE_STAGES,
    })

# ── APPLICATIONS ───────────────────────────────────────────────────────
@flask_app.route("/api/applications", methods=["GET"])
@login_required
def get_applications():
    with _lock: data = load_data()
    section  = request.args.get("section","")
    status   = request.args.get("status","")
    search   = request.args.get("q","").lower()
    apps = data.get("applications",[])
    if section: apps=[a for a in apps if a.get("section")==section]
    if status:  apps=[a for a in apps if a.get("stage", a.get("status",""))==status]
    if search:  apps=[a for a in apps if search in (a.get("name","")+"@"+a.get("username","")+""+a.get("phone","")).lower()]
    return jsonify({"applications":sorted(apps,key=lambda x:x["id"],reverse=True),"stats":data.get("stats",{})})

@flask_app.route("/api/applications/<int:app_id>/stage", methods=["PUT"])
@login_required
def update_app_stage(app_id):
    new_stage = request.json.get("stage")
    valid = [s["id"] for s in PIPELINE_STAGES]
    if new_stage not in valid: return jsonify({"error":"Noto'g'ri bosqich"}),400
    with _lock:
        data = load_data()
        for a in data["applications"]:
            if a["id"]==app_id:
                old_stage = a.get("stage", a.get("status","new"))
                a["stage"] = new_stage
                # stats uchun eski status moslashtirish
                if new_stage in ["hired","rejected"]: a["status"]="completed"
                elif new_stage=="reviewed": a["status"]="reviewed"
                else: a["status"]="pending"
                # log
                a.setdefault("history",[]).append({
                    "action": f"Bosqich o'zgartirildi: {old_stage} → {new_stage}",
                    "by": session.get("user",{}).get("name","Admin"),
                    "time": datetime.now().strftime("%H:%M, %d-%B")
                })
                save_data(data)
                uid = a["user_id"]
                # stage xabarini nomzodga yuborish
                stage_msgs = {
                    "reviewed":  "👀 Arizangiz ko'rib chiqilmoqda. Natija 3-5 ish kuni ichida ma'lum qilinadi.",
                    "interview": "🎉 Tabriklaymiz! Intervyuga taklif etilmoqdasiz. HR siz bilan bog'lanadi.",
                    "offer":     "🌟 Sizga ish taklifi yuborildi! HR siz bilan bog'lanadi.",
                    "hired":     "✅ Tabriklaymiz! Siz qabul qilindingiz. Xush kelibsiz, jamoamizga!",
                    "rejected":  "😔 Afsuski, hozircha jarayonni siz bilan davom ettira olmaymiz. Kelajakda omad!",
                }
                msg = stage_msgs.get(new_stage)
                if msg and bot_loop:
                    asyncio.run_coroutine_threadsafe(_send_msg(uid, f"📩 Mudarris School:\n\n{msg}"), bot_loop)
                return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>/status", methods=["PUT"])
@login_required
def update_app_status(app_id):
    new_status = request.json.get("status")
    if new_status not in ["pending","reviewed","completed"]: return jsonify({"error":"Noto'g'ri status"}),400
    with _lock:
        data = load_data()
        for a in data["applications"]:
            if a["id"]==app_id:
                old=a["status"]; a["status"]=new_status
                data["stats"][old]=max(0,data["stats"].get(old,0)-1)
                data["stats"][new_status]=data["stats"].get(new_status,0)+1
                save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>/reply", methods=["POST"])
@login_required
def reply_to_application(app_id):
    text = request.json.get("text","").strip()
    if not text: return jsonify({"error":"Xabar bo'sh"}),400
    with _lock:
        data = load_data()
        for a in data["applications"]:
            if a["id"]==app_id:
                a.setdefault("replies",[]).append({
                    "text":text,
                    "time":datetime.now().strftime("%H:%M, %d-%B"),
                    "by": session.get("user",{}).get("name","Admin")
                })
                a.setdefault("history",[]).append({
                    "action": f"Javob yuborildi",
                    "by": session.get("user",{}).get("name","Admin"),
                    "time": datetime.now().strftime("%H:%M, %d-%B")
                })
                save_data(data)
                uid = a["user_id"]
                if bot_loop:
                    asyncio.run_coroutine_threadsafe(_send_msg(uid,f"📩 Mudarris School javobi:\n\n{text}"),bot_loop)
                return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>/star", methods=["PUT"])
@login_required
def star_application(app_id):
    stars = request.json.get("stars", 0)
    note  = request.json.get("note","")
    with _lock:
        data = load_data()
        for a in data["applications"]:
            if a["id"]==app_id:
                a["stars"] = stars
                a["hr_note"] = note
                save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>/edit", methods=["PUT"])
@login_required
def edit_application(app_id):
    d = request.json
    with _lock:
        data = load_data()
        for a in data["applications"]:
            if a["id"]==app_id:
                for field in ["name","phone","age","experience","note","info"]:
                    if field in d: a[field]=d[field]
                if "status" in d:
                    old,new_s=a["status"],d["status"]
                    if old!=new_s:
                        data["stats"][old]=max(0,data["stats"].get(old,0)-1)
                        data["stats"][new_s]=data["stats"].get(new_s,0)+1
                    a["status"]=new_s
                save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>", methods=["DELETE"])
@login_required
def delete_application(app_id):
    with _lock:
        data = load_data()
        for i,a in enumerate(data["applications"]):
            if a["id"]==app_id:
                old=a["status"]; data["applications"].pop(i)
                data["stats"]["total"]=max(0,data["stats"].get("total",0)-1)
                data["stats"][old]=max(0,data["stats"].get(old,0)-1)
                save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

# ── CONTACTS ───────────────────────────────────────────────────────────
@flask_app.route("/api/contacts", methods=["GET"])
@login_required
def get_contacts():
    with _lock: data = load_data()
    status = request.args.get("status","")
    contacts = data.get("contacts",[])
    if status: contacts=[c for c in contacts if c.get("status")==status]
    return jsonify({"contacts":sorted(contacts,key=lambda x:x["id"],reverse=True)})

@flask_app.route("/api/contacts/<int:contact_id>/reply", methods=["POST"])
@login_required
def reply_to_contact(contact_id):
    text = request.json.get("text","").strip()
    if not text: return jsonify({"error":"Xabar bo'sh"}),400
    with _lock:
        data = load_data()
        for c in data["contacts"]:
            if c["id"]==contact_id:
                c.setdefault("replies",[]).append({"text":text,"time":datetime.now().strftime("%H:%M, %d-%B"),"by":session.get("user",{}).get("name","Admin")})
                c["status"]="replied"; save_data(data)
                uid = c["user_id"]
                if bot_loop:
                    asyncio.run_coroutine_threadsafe(_send_msg(uid,f"📩 Mudarris School javobi:\n\n{text}"),bot_loop)
                return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/contacts/<int:contact_id>", methods=["DELETE"])
@login_required
def delete_contact(contact_id):
    with _lock:
        data = load_data()
        for i,c in enumerate(data["contacts"]):
            if c["id"]==contact_id:
                data["contacts"].pop(i); save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

# ── CV ─────────────────────────────────────────────────────────────────
@flask_app.route("/api/cv/<file_id>")
@login_required
def get_cv(file_id):
    try:
        r  = req_lib.get(f"https://api.telegram.org/bot{TOKEN}/getFile?file_id={file_id}", timeout=8)
        info = r.json()
        if not info.get("ok"): return jsonify({"error":"Fayl topilmadi"}),404
        fp = info["result"]["file_path"]
        fr = req_lib.get(f"https://api.telegram.org/file/bot{TOKEN}/{fp}", timeout=25)
        ext= fp.rsplit(".",1)[-1].lower()
        ct = {"pdf":"application/pdf","jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}.get(ext,"application/octet-stream")
        return Response(fr.content, content_type=ct, headers={"Content-Disposition":f"inline; filename=cv.{ext}"})
    except Exception as e:
        return jsonify({"error":str(e)}),500

# ── VACANCIES ──────────────────────────────────────────────────────────
@flask_app.route("/api/vacancies", methods=["GET"])
@login_required
def get_vacancies():
    with _lock: data = load_data()
    return jsonify({"vacancies": data.get("vacancies",[])})

@flask_app.route("/api/vacancies", methods=["POST"])
@login_required
def add_vacancy():
    d = request.json
    with _lock:
        data = load_data()
        v = {
            "id": str(uuid.uuid4())[:8],
            "title":      d.get("title",""),
            "department": d.get("department",""),
            "status":     d.get("status","open"),
            "open_date":  d.get("open_date", datetime.now().strftime("%Y-%m-%d")),
            "deadline":   d.get("deadline",""),
            "slots":      int(d.get("slots",1)),
            "show_in_bot": d.get("show_in_bot", True),
            "description": d.get("description",""),
            "created_by":  session.get("user",{}).get("name","Admin"),
            "created_at":  datetime.now().strftime("%H:%M, %d-%B"),
        }
        data["vacancies"].append(v)
        save_data(data)
    return jsonify({"success":True, "vacancy":v})

@flask_app.route("/api/vacancies/<vid>", methods=["PUT"])
@login_required
def update_vacancy(vid):
    d = request.json
    with _lock:
        data = load_data()
        for v in data["vacancies"]:
            if v["id"]==vid:
                for f in ["title","department","status","open_date","deadline","slots","show_in_bot","description"]:
                    if f in d: v[f]=d[f]
                save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/vacancies/<vid>", methods=["DELETE"])
@login_required
def delete_vacancy(vid):
    with _lock:
        data = load_data()
        data["vacancies"] = [v for v in data["vacancies"] if v["id"]!=vid]
        save_data(data)
    return jsonify({"success":True})

# ── TEMPLATES ──────────────────────────────────────────────────────────
@flask_app.route("/api/templates", methods=["GET"])
@login_required
def get_templates():
    with _lock: data = load_data()
    return jsonify({"templates": data.get("templates", DEFAULT_TEMPLATES)})

@flask_app.route("/api/templates", methods=["POST"])
@login_required
def add_template():
    d = request.json
    with _lock:
        data = load_data()
        t = {"id": str(uuid.uuid4())[:8], "name": d.get("name",""), "text": d.get("text","")}
        data.setdefault("templates", DEFAULT_TEMPLATES).append(t)
        save_data(data)
    return jsonify({"success":True,"template":t})

@flask_app.route("/api/templates/<tid>", methods=["PUT"])
@login_required
def update_template(tid):
    d = request.json
    with _lock:
        data = load_data()
        for t in data.get("templates",[]):
            if t["id"]==tid:
                if "name" in d: t["name"]=d["name"]
                if "text" in d: t["text"]=d["text"]
                save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/templates/<tid>", methods=["DELETE"])
@login_required
def delete_template(tid):
    with _lock:
        data = load_data()
        data["templates"] = [t for t in data.get("templates",[]) if t["id"]!=tid]
        save_data(data)
    return jsonify({"success":True})

# ── CONFIG ─────────────────────────────────────────────────────────────
@flask_app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    with _lock: return jsonify(load_config())

@flask_app.route("/api/config/welcome", methods=["PUT"])
@login_required
def update_welcome():
    with _lock:
        config = load_config()
        config["welcome_message"] = request.json.get("message","")
        save_config(config)
    return jsonify({"success":True})

@flask_app.route("/api/buttons", methods=["GET"])
@login_required
def get_buttons():
    with _lock: return jsonify(load_config())

@flask_app.route("/api/buttons", methods=["POST"])
@login_required
def add_button():
    d=request.json; icon=d.get("icon",""); text=d.get("text","")
    new_btn={"id":uuid.uuid4().hex[:8],"label":(icon+" "+text).strip(),"icon":icon,"text":text,
             "type":d.get("type","message"),"message":d.get("message",""),
             "section":d.get("section",""),"children":[]}
    with _lock:
        config=load_config(); pid=d.get("parent_id","")
        if pid: find_parent_and_add(config["buttons"],pid,new_btn)
        else:   config["buttons"].append(new_btn)
        save_config(config)
    return jsonify({"success":True,"button":new_btn})

@flask_app.route("/api/buttons/<btn_id>", methods=["PUT"])
@login_required
def update_button(btn_id):
    d=request.json; icon=d.get("icon",""); text=d.get("text","")
    upd={"icon":icon,"text":text,"label":(icon+" "+text).strip(),"type":d.get("type","message"),"message":d.get("message",""),"section":d.get("section","")}
    with _lock:
        config=load_config()
        if find_and_update(config["buttons"],btn_id,upd):
            save_config(config); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/buttons/<btn_id>", methods=["DELETE"])
@login_required
def delete_button(btn_id):
    with _lock:
        config=load_config()
        if find_and_delete(config["buttons"],btn_id):
            save_config(config); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

# ══════════════════════════════════════════════════════════════════════
#  BOT
# ══════════════════════════════════════════════════════════════════════
tg_bot  = Bot(token=TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

class AppForm(StatesGroup):
    waiting_name  = State()
    waiting_phone = State()
    waiting_age   = State()
    waiting_exp   = State()
    waiting_cv    = State()
    waiting_note  = State()

class ContactForm(StatesGroup):
    waiting_text  = State()
    waiting_phone = State()

async def _send_msg(user_id, text):
    try: await tg_bot.send_message(user_id, text)
    except Exception as e: print(f"Send error: {e}")

def find_by_id(buttons, btn_id):
    for btn in buttons:
        if btn.get("id")==btn_id: return btn
        found=find_by_id(btn.get("children",[]),btn_id)
        if found: return found
    return None

def find_by_label(buttons, label):
    for btn in buttons:
        if btn.get("label")==label: return btn
        found=find_by_label(btn.get("children",[]),label)
        if found: return found
    return None

def make_keyboard(buttons, extra_back=False):
    rows,row=[],[]
    for btn in buttons:
        row.append(KeyboardButton(text=btn["label"]))
        if len(row)==2: rows.append(row); row=[]
    if row: rows.append(row)
    if extra_back: rows.append([KeyboardButton(text="⬅️ Orqaga")])
    return ReplyKeyboardMarkup(keyboard=rows,resize_keyboard=True)

SKIP_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏩ O'tkazib yuborish")]],resize_keyboard=True)

def save_application(user,section,detail,name,phone,age,exp,cv_file_id,cv_type,note):
    with _lock:
        data=load_data()
        app={
            "id":len(data["applications"])+1,"user_id":user.id,
            "tg_name":f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username":user.username or "","name":name,"phone":phone,
            "age":age,"experience":exp,"cv_file_id":cv_file_id,"cv_type":cv_type,
            "note":note,"info":f"Yosh: {age} | Tajriba: {exp}",
            "section":section,"detail":detail,
            "time":datetime.now().strftime("%H:%M, %d-%B"),
            "status":"pending","stage":"new","stars":0,"hr_note":"",
            "replies":[],"history":[{"action":"Ariza qabul qilindi","by":"Bot","time":datetime.now().strftime("%H:%M, %d-%B")}]
        }
        data["applications"].append(app)
        data["stats"]["total"]+=1; data["stats"]["pending"]+=1
        save_data(data)
    return app["id"]

def save_contact(user,text,phone):
    with _lock:
        data=load_data()
        c={"id":len(data["contacts"])+1,"user_id":user.id,
           "tg_name":f"{user.first_name or ''} {user.last_name or ''}".strip(),
           "username":user.username or "","text":text,"phone":phone,
           "time":datetime.now().strftime("%H:%M, %d-%B"),"status":"new","replies":[]}
        data["contacts"].append(c); save_data(data)
    return c["id"]

@dp.message(CommandStart())
async def start_cmd(message:types.Message,state:FSMContext):
    await state.clear(); cfg=load_config()
    await message.answer(cfg.get("welcome_message","Xush kelibsiz!"),reply_markup=make_keyboard(cfg["buttons"]))

@dp.message(F.text=="⬅️ Orqaga")
async def go_back(message:types.Message,state:FSMContext):
    await state.clear(); cfg=load_config()
    await message.answer("Asosiy menyu:",reply_markup=make_keyboard(cfg["buttons"]))

@dp.message(AppForm.waiting_name)
async def step_name(message:types.Message,state:FSMContext):
    if not message.text: return
    await state.update_data(name=message.text.strip()); await state.set_state(AppForm.waiting_phone)
    await message.answer("📱 <b>2-qadam:</b> Telefon raqam:\nMasalan: +998901234567",parse_mode="HTML")

@dp.message(AppForm.waiting_phone)
async def step_phone(message:types.Message,state:FSMContext):
    if not message.text: return
    await state.update_data(phone=message.text.strip()); await state.set_state(AppForm.waiting_age)
    await message.answer("🎂 <b>3-qadam:</b> Yoshingiz:\nMasalan: <b>25</b>",parse_mode="HTML")

@dp.message(AppForm.waiting_age)
async def step_age(message:types.Message,state:FSMContext):
    if not message.text: return
    await state.update_data(age=message.text.strip()); await state.set_state(AppForm.waiting_exp)
    await message.answer("💼 <b>4-qadam:</b> Tajriba:\nMasalan: <b>3 yil</b> yoki <b>Tajribam yo'q</b>",parse_mode="HTML")

@dp.message(AppForm.waiting_exp)
async def step_exp(message:types.Message,state:FSMContext):
    if not message.text: return
    await state.update_data(experience=message.text.strip()); await state.set_state(AppForm.waiting_cv)
    await message.answer("📎 <b>5-qadam:</b> CV yuboring:\n• PDF hujjat\n• Yoki rasm\n\nCV yo'q bo'lsa — <b>⏩ O'tkazib yuborish</b>",parse_mode="HTML",reply_markup=SKIP_KB)

@dp.message(AppForm.waiting_cv,F.document)
async def step_cv_doc(message:types.Message,state:FSMContext):
    await state.update_data(cv_file_id=message.document.file_id,cv_type="document")
    await state.set_state(AppForm.waiting_note)
    await message.answer("✅ CV qabul qilindi!\n\n💬 <b>6-qadam:</b> Qo'shimcha izoh <i>(ixtiyoriy)</i>:",parse_mode="HTML",reply_markup=SKIP_KB)

@dp.message(AppForm.waiting_cv,F.photo)
async def step_cv_photo(message:types.Message,state:FSMContext):
    await state.update_data(cv_file_id=message.photo[-1].file_id,cv_type="photo")
    await state.set_state(AppForm.waiting_note)
    await message.answer("✅ CV rasmi qabul qilindi!\n\n💬 <b>6-qadam:</b> Qo'shimcha izoh <i>(ixtiyoriy)</i>:",parse_mode="HTML",reply_markup=SKIP_KB)

@dp.message(AppForm.waiting_cv)
async def step_cv_skip(message:types.Message,state:FSMContext):
    await state.update_data(cv_file_id=None,cv_type="none")
    await state.set_state(AppForm.waiting_note)
    await message.answer("💬 <b>6-qadam:</b> Qo'shimcha izoh <i>(ixtiyoriy)</i>:",parse_mode="HTML",reply_markup=SKIP_KB)

@dp.message(AppForm.waiting_note)
async def step_note(message:types.Message,state:FSMContext):
    note=""
    if message.text and message.text!="⏩ O'tkazib yuborish": note=message.text.strip()
    d=await state.get_data()
    app_id=save_application(user=message.from_user,section=d.get("section",""),detail=d.get("detail",""),
        name=d.get("name",""),phone=d.get("phone",""),age=d.get("age",""),exp=d.get("experience",""),
        cv_file_id=d.get("cv_file_id"),cv_type=d.get("cv_type","none"),note=note)
    await state.clear(); cfg=load_config()
    cv_s="✅ Yuklandi" if d.get("cv_type")!="none" else "➖"
    await message.answer(
        f"🎉 <b>Ariza qabul qilindi!</b>\n\n"
        f"👤 {d.get('name','')} | 📱 {d.get('phone','')}\n"
        f"🎂 {d.get('age','')} yosh | 💼 {d.get('experience','')}\n"
        f"📎 CV: {cv_s}\n🆔 <b>#{app_id}</b>\n\nTez orada bog'lanishadi! 🙏",
        parse_mode="HTML",reply_markup=make_keyboard(cfg["buttons"]))

@dp.message(ContactForm.waiting_text)
async def step_contact_text(message:types.Message,state:FSMContext):
    if not message.text: return
    await state.update_data(contact_text=message.text.strip()); await state.set_state(ContactForm.waiting_phone)
    await message.answer("📱 Telefon raqamingizni yozing:\nMasalan: +998901234567")

@dp.message(ContactForm.waiting_phone)
async def step_contact_phone(message:types.Message,state:FSMContext):
    if not message.text: return
    d=await state.get_data(); cid=save_contact(message.from_user,d["contact_text"],message.text.strip())
    await state.clear(); cfg=load_config()
    await message.answer(f"✅ Xabar qabul qilindi! 🆔 #{cid}\n\nTez orada javob beramiz!",reply_markup=make_keyboard(cfg["buttons"]))

@dp.message(StateFilter(default_state))
async def handle_menu(message:types.Message,state:FSMContext):
    if not message.text: return
    cfg=load_config(); d=await state.get_data(); cur=d.get("current_menu_id")
    btn=None
    if cur:
        parent=find_by_id(cfg["buttons"],cur)
        if parent:
            for ch in parent.get("children",[]):
                if ch.get("label")==message.text: btn=ch; break
    if not btn: btn=find_by_label(cfg["buttons"],message.text)
    if not btn: return
    if "bog'laning" in btn.get("text","").lower():
        await state.update_data(current_menu_id=None); await state.set_state(ContactForm.waiting_text)
        await message.answer("📞 Savolingizni yozing:",reply_markup=ReplyKeyboardRemove()); return
    children=btn.get("children",[])
    if children:
        await state.update_data(current_menu_id=btn["id"])
        await message.answer(btn.get("message","").strip() or "Tanlang:",reply_markup=make_keyboard(children,extra_back=True))
    elif btn.get("type")=="application":
        await state.update_data(current_menu_id=None,section=btn.get("section",btn["text"]),detail=btn["text"])
        await state.set_state(AppForm.waiting_name)
        await message.answer(f"📋 <b>Ariza</b> — {btn['text']}\n\n👤 <b>1-qadam:</b> Ism Familiya:",parse_mode="HTML",reply_markup=ReplyKeyboardRemove())
    else:
        await state.update_data(current_menu_id=None)
        txt=btn.get("message","").strip()
        if not txt: return
        if "haqimizda" in btn.get("text","").lower() and not cur:
            kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Mudarris School kanali",url="https://t.me/mudarris_maktabi")]])
            try: await message.answer_photo(photo=ABOUT_IMAGE,caption=txt,reply_markup=kb)
            except: await message.answer(txt,reply_markup=kb)
        else:
            await message.answer(txt)

# ── Keep-alive ─────────────────────────────────────────────────────────
async def keep_alive():
    await asyncio.sleep(60)
    while True:
        try:
            if APP_URL: req_lib.get(f"https://{APP_URL}/api/me",timeout=5)
        except: pass
        await asyncio.sleep(240)

async def run_bot():
    global bot_loop
    bot_loop=asyncio.get_running_loop()
    load_data(); load_config()
    print("✅ Bot ishga tushdi")
    asyncio.create_task(keep_alive())
    await dp.start_polling(tg_bot,allowed_updates=["message"])

def run_flask():
    print(f"✅ Admin panel: http://0.0.0.0:{PORT}")
    flask_app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False,threaded=True)

if __name__=="__main__":
    t=threading.Thread(target=run_flask,daemon=True); t.start()
    asyncio.run(run_bot())
