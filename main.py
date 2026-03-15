import asyncio
import threading
import json
import os
import uuid
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory
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

# ── Flask ─────────────────────────────────────────────────────────────
flask_app = Flask(__name__, static_folder=".")
CORS(flask_app)

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"applications":[],"contacts":[],"stats":{"total":0,"reviewed":0,"pending":0,"completed":0}}
    with open(DATA_FILE,"r",encoding="utf-8") as f: return json.load(f)

def save_data(data):
    with open(DATA_FILE,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"buttons":[],"welcome_message":"Xush kelibsiz!"}
    with open(CONFIG_FILE,"r",encoding="utf-8") as f: return json.load(f)

def save_config(config):
    with open(CONFIG_FILE,"w",encoding="utf-8") as f: json.dump(config,f,ensure_ascii=False,indent=2)

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

@flask_app.route("/")
def index(): return send_from_directory(".","admin.html")

@flask_app.route("/api/applications",methods=["GET"])
def get_applications():
    data=load_data(); section=request.args.get("section",""); status=request.args.get("status","")
    apps=data.get("applications",[])
    if section: apps=[a for a in apps if a.get("section")==section]
    if status:  apps=[a for a in apps if a.get("status")==status]
    return jsonify({"applications":sorted(apps,key=lambda x:x["id"],reverse=True),"stats":data.get("stats",{})})

@flask_app.route("/api/contacts",methods=["GET"])
def get_contacts():
    data=load_data(); contacts=data.get("contacts",[]); status=request.args.get("status","")
    if status: contacts=[c for c in contacts if c.get("status")==status]
    return jsonify({"contacts":sorted(contacts,key=lambda x:x["id"],reverse=True)})

@flask_app.route("/api/applications/<int:app_id>/status",methods=["PUT"])
def update_app_status(app_id):
    data=load_data(); new_status=request.json.get("status")
    if new_status not in ["pending","reviewed","completed"]: return jsonify({"error":"Noto'g'ri status"}),400
    for a in data.get("applications",[]):
        if a["id"]==app_id:
            old=a["status"]; a["status"]=new_status
            data["stats"][old]=max(0,data["stats"].get(old,0)-1)
            data["stats"][new_status]=data["stats"].get(new_status,0)+1
            save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>/reply",methods=["POST"])
def reply_to_application(app_id):
    data=load_data(); text=request.json.get("text","").strip()
    if not text: return jsonify({"error":"Xabar bo'sh"}),400
    for a in data.get("applications",[]):
        if a["id"]==app_id:
            a.setdefault("replies",[]).append({"text":text,"time":datetime.now().strftime("%H:%M, %d-%B")})
            save_data(data)
            if bot_loop:
                asyncio.run_coroutine_threadsafe(_send_msg(a["user_id"],f"📩 Mudarris School javobi:\n\n{text}"),bot_loop)
            return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/contacts/<int:contact_id>/reply",methods=["POST"])
def reply_to_contact(contact_id):
    data=load_data(); text=request.json.get("text","").strip()
    if not text: return jsonify({"error":"Xabar bo'sh"}),400
    for c in data.get("contacts",[]):
        if c["id"]==contact_id:
            c.setdefault("replies",[]).append({"text":text,"time":datetime.now().strftime("%H:%M, %d-%B")})
            c["status"]="replied"; save_data(data)
            if bot_loop:
                asyncio.run_coroutine_threadsafe(_send_msg(c["user_id"],f"📩 Mudarris School javobi:\n\n{text}"),bot_loop)
            return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/contacts/<int:contact_id>/status",methods=["PUT"])
def update_contact_status(contact_id):
    data=load_data(); new_status=request.json.get("status")
    for c in data.get("contacts",[]):
        if c["id"]==contact_id: c["status"]=new_status; save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>/edit",methods=["PUT"])
def edit_application(app_id):
    data=load_data(); d=request.json
    for a in data.get("applications",[]):
        if a["id"]==app_id:
            if "name" in d: a["name"]=d["name"]
            if "phone" in d: a["phone"]=d["phone"]
            if "info" in d: a["info"]=d["info"]
            if "status" in d:
                old,new_s=a["status"],d["status"]
                if old!=new_s:
                    data["stats"][old]=max(0,data["stats"].get(old,0)-1)
                    data["stats"][new_s]=data["stats"].get(new_s,0)+1
                a["status"]=new_s
            save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/applications/<int:app_id>",methods=["DELETE"])
def delete_application(app_id):
    data=load_data()
    for i,a in enumerate(data.get("applications",[])):
        if a["id"]==app_id:
            old=a["status"]; data["applications"].pop(i)
            data["stats"]["total"]=max(0,data["stats"].get("total",0)-1)
            data["stats"][old]=max(0,data["stats"].get(old,0)-1)
            save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/contacts/<int:contact_id>/edit",methods=["PUT"])
def edit_contact(contact_id):
    data=load_data(); d=request.json
    for c in data.get("contacts",[]):
        if c["id"]==contact_id:
            if "phone" in d: c["phone"]=d["phone"]
            if "text" in d: c["text"]=d["text"]
            if "status" in d: c["status"]=d["status"]
            save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/contacts/<int:contact_id>",methods=["DELETE"])
def delete_contact(contact_id):
    data=load_data()
    for i,c in enumerate(data.get("contacts",[])):
        if c["id"]==contact_id: data["contacts"].pop(i); save_data(data); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/config",methods=["GET"])
def get_config(): return jsonify(load_config())

@flask_app.route("/api/config/welcome",methods=["PUT"])
def update_welcome():
    config=load_config(); config["welcome_message"]=request.json.get("message",config["welcome_message"])
    save_config(config); return jsonify({"success":True})

@flask_app.route("/api/buttons",methods=["POST"])
def add_button():
    config=load_config(); d=request.json
    new_btn={"id":str(uuid.uuid4())[:8],"label":(d.get("icon","")+' '+d.get("text","")).strip(),
             "icon":d.get("icon",""),"text":d.get("text","Yangi tugma"),"type":d.get("type","message"),
             "message":d.get("message",""),"section":d.get("section",""),"children":[]}
    parent_id=d.get("parent_id","")
    if parent_id: find_parent_and_add(config["buttons"],parent_id,new_btn)
    else: config["buttons"].append(new_btn)
    save_config(config); return jsonify({"success":True,"button":new_btn})

@flask_app.route("/api/buttons/<btn_id>",methods=["PUT"])
def update_button(btn_id):
    config=load_config(); d=request.json; icon=d.get("icon",""); text=d.get("text","")
    update={"icon":icon,"text":text,"label":(icon+" "+text).strip(),"type":d.get("type","message"),
            "message":d.get("message",""),"section":d.get("section","")}
    if find_and_update(config["buttons"],btn_id,update): save_config(config); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

@flask_app.route("/api/buttons/<btn_id>",methods=["DELETE"])
def delete_button(btn_id):
    config=load_config()
    if find_and_delete(config["buttons"],btn_id): save_config(config); return jsonify({"success":True})
    return jsonify({"error":"Topilmadi"}),404

# ── Bot ───────────────────────────────────────────────────────────────
tg_bot  = Bot(token=TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

class AppForm(StatesGroup):
    waiting_name  = State()
    waiting_info  = State()
    waiting_phone = State()

class ContactForm(StatesGroup):
    waiting_text  = State()
    waiting_phone = State()

async def _send_msg(user_id, text):
    try: await tg_bot.send_message(user_id, text)
    except Exception as e: print(f"Send error: {e}")

def find_by_id(buttons,btn_id):
    for btn in buttons:
        if btn.get("id")==btn_id: return btn
        found=find_by_id(btn.get("children",[]),btn_id)
        if found: return found
    return None

def find_by_label(buttons,label):
    for btn in buttons:
        if btn.get("label")==label: return btn
        found=find_by_label(btn.get("children",[]),label)
        if found: return found
    return None

def make_keyboard(buttons,extra_back=False):
    rows,row=[],[]
    for btn in buttons:
        row.append(KeyboardButton(text=btn["label"]))
        if len(row)==2: rows.append(row); row=[]
    if row: rows.append(row)
    if extra_back: rows.append([KeyboardButton(text="⬅️ Orqaga")])
    return ReplyKeyboardMarkup(keyboard=rows,resize_keyboard=True)

def save_application(user,section,detail,name,info,phone):
    data=load_data()
    app={"id":len(data["applications"])+1,"user_id":user.id,
         "tg_name":f"{user.first_name or ''} {user.last_name or ''}".strip(),
         "username":user.username or "","name":name,"info":info,"phone":phone,
         "section":section,"detail":detail,"time":datetime.now().strftime("%H:%M, %d-%B"),
         "status":"pending","replies":[]}
    data["applications"].append(app)
    data["stats"]["total"]+=1; data["stats"]["pending"]+=1
    save_data(data); return app["id"]

def save_contact(user,text,phone):
    data=load_data()
    contact={"id":len(data["contacts"])+1,"user_id":user.id,
             "tg_name":f"{user.first_name or ''} {user.last_name or ''}".strip(),
             "username":user.username or "","text":text,"phone":phone,
             "time":datetime.now().strftime("%H:%M, %d-%B"),"status":"new","replies":[]}
    data["contacts"].append(contact); save_data(data); return contact["id"]

@dp.message(CommandStart())
async def start_command(message:types.Message,state:FSMContext):
    await state.clear(); config=load_config()
    await message.answer(config.get("welcome_message","Xush kelibsiz!"),reply_markup=make_keyboard(config["buttons"]))

@dp.message(F.text=="⬅️ Orqaga")
async def back_to_main(message:types.Message,state:FSMContext):
    await state.clear(); config=load_config()
    await message.answer("Asosiy menyuga qaytdingiz:",reply_markup=make_keyboard(config["buttons"]))

@dp.message(AppForm.waiting_name)
async def get_name(message:types.Message,state:FSMContext):
    await state.update_data(name=message.text.strip()); await state.set_state(AppForm.waiting_info)
    await message.answer("📝 O'zingiz haqingizda qisqacha ma'lumot yozing:\n(tajriba, mutaxassislik, yosh va h.k.)")

@dp.message(AppForm.waiting_info)
async def get_info(message:types.Message,state:FSMContext):
    await state.update_data(info=message.text.strip()); await state.set_state(AppForm.waiting_phone)
    await message.answer("📱 Telefon raqamingizni yozing:\nMasalan: +998901234567")

@dp.message(AppForm.waiting_phone)
async def get_phone_app(message:types.Message,state:FSMContext):
    d=await state.get_data()
    app_id=save_application(message.from_user,d["section"],d["detail"],d["name"],d["info"],message.text.strip())
    await state.clear(); config=load_config()
    await message.answer(
        f"✅ Arizangiz qabul qilindi!\n\n👤 Ism: {d['name']}\n📝 Ma'lumot: {d['info']}\n"
        f"📱 Telefon: {message.text.strip()}\n🆔 Ariza raqami: #{app_id}\n\nMutaxassislarimiz tez orada bog'lanishadi.",
        reply_markup=make_keyboard(config["buttons"]))

@dp.message(ContactForm.waiting_text)
async def get_contact_text(message:types.Message,state:FSMContext):
    await state.update_data(contact_text=message.text.strip()); await state.set_state(ContactForm.waiting_phone)
    await message.answer("📱 Telefon raqamingizni yozing:\nMasalan: +998901234567")

@dp.message(ContactForm.waiting_phone)
async def get_phone_contact(message:types.Message,state:FSMContext):
    d=await state.get_data()
    contact_id=save_contact(message.from_user,d["contact_text"],message.text.strip())
    await state.clear(); config=load_config()
    await message.answer(
        f"✅ Xabaringiz qabul qilindi!\n\n📱 Raqam: {message.text.strip()}\n"
        f"🆔 Murojaat raqami: #{contact_id}\n\nTez orada javob beramiz!",
        reply_markup=make_keyboard(config["buttons"]))

@dp.message(StateFilter(default_state))
async def handle_any(message:types.Message,state:FSMContext):
    if not message.text: return
    config=load_config(); fsm_data=await state.get_data(); current_menu_id=fsm_data.get("current_menu_id")
    btn=None
    if current_menu_id:
        parent=find_by_id(config["buttons"],current_menu_id)
        if parent and parent.get("children"):
            for child in parent["children"]:
                if child.get("label")==message.text: btn=child; break
    if not btn: btn=find_by_label(config["buttons"],message.text)
    if not btn: return
    children=btn.get("children",[])
    if "bog'laning" in btn.get("text","").lower():
        await state.update_data(current_menu_id=None); await state.set_state(ContactForm.waiting_text)
        await message.answer("📞 Biz bilan bog'laning\n\n✍️ Savolingiz yoki muammoingizni yozing:",reply_markup=ReplyKeyboardRemove()); return
    if children:
        await state.update_data(current_menu_id=btn.get("id"))
        msg=btn.get("message","").strip() or "Tanlang:"
        await message.answer(msg,reply_markup=make_keyboard(children,extra_back=True))
    elif btn.get("type")=="application":
        await state.update_data(current_menu_id=None,section=btn.get("section",btn["text"]),detail=btn["text"])
        await state.set_state(AppForm.waiting_name)
        await message.answer(f"📋 Ariza to'ldirish\n\nBo'lim: {btn['text']}\n\n👤 Ism va familiyangizni yozing:",reply_markup=ReplyKeyboardRemove())
    else:
        await state.update_data(current_menu_id=None)
        txt=btn.get("message","").strip()
        if not txt: return
        if "haqimizda" in btn.get("text","").lower() and not current_menu_id:
            channel_btn=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Mudarris School kanali",url="https://t.me/mudarris_maktabi")]])
            try: await message.answer_photo(photo=ABOUT_IMAGE,caption=txt,reply_markup=channel_btn); return
            except: await message.answer(txt,reply_markup=channel_btn); return
        await message.answer(txt)

# ── Start ─────────────────────────────────────────────────────────────
async def run_bot():
    global bot_loop
    bot_loop=asyncio.get_running_loop()
    print("✅ Bot ishga tushdi")
    await dp.start_polling(tg_bot)

def run_flask():
    print(f"✅ Admin panel: http://0.0.0.0:{PORT}")
    flask_app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)

if __name__=="__main__":
    t=threading.Thread(target=run_flask,daemon=True)
    t.start()
    asyncio.run(run_bot())
