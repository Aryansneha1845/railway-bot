import os
import time
import asyncio
import sqlite3
import requests
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackQueryHandler, ConversationHandler
)

# ================= CONFIG =================
TOKEN        = os.environ.get("BOT_TOKEN", "8884909837:AAEF9MHEhDytK66yJKhLMijttlOCcHhCqrU")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "3e1ae476b9msh23fa56ceb864394p189efcjsn0608a7111b65")

# TWO APIs
PNR_HOST  = "irctc-indian-railway-pnr-status.p.rapidapi.com"
TRAIN_HOST = "irctc-train-api.p.rapidapi.com"

def pnr_headers():
    return {
        "x-rapidapi-host": PNR_HOST,
        "x-rapidapi-key":  RAPIDAPI_KEY,
        "Content-Type":    "application/json"
    }

def train_headers():
    return {
        "x-rapidapi-host": TRAIN_HOST,
        "x-rapidapi-key":  RAPIDAPI_KEY,
        "Content-Type":    "application/json"
    }

BASE = f"https://{TRAIN_HOST}/api/v1"

# ================= DB =================
conn = sqlite3.connect("railway_bot.db", check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, username TEXT, language TEXT DEFAULT 'hi', joined INTEGER)""")
c.execute("""CREATE TABLE IF NOT EXISTS journey_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
    query TEXT, result TEXT, searched_at INTEGER)""")
c.execute("""CREATE TABLE IF NOT EXISTS favourite_trains (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, train_no TEXT, train_name TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
    train_no TEXT, pnr TEXT, active INTEGER DEFAULT 1, created_at INTEGER)""")
conn.commit()

# ================= STATES =================
PNR_INPUT     = 1
TRAIN_INPUT   = 2
STATION_INPUT = 3
FARE_FROM     = 4
FARE_TO       = 5
FARE_CLASS    = 6
LIVE_INPUT    = 7
COACH_TRAIN   = 8
BETWEEN_FROM  = 9
BETWEEN_TO    = 10
BETWEEN_DATE  = 11

# ================= UI =================
def main_keyboard(lang="hi"):
    return ReplyKeyboardMarkup([
        ["🎫 PNR Status",       "🚂 Train Schedule"],
        ["🔍 Trains Between",   "📍 Live Train"],
        ["🏛️ Station Board",   "🚃 Coach Position"],
        ["⏱️ Delay History",   "⭐ Favourites"],
        ["📜 History",          "🔔 Alerts"],
        ["🌐 Language",         "ℹ️ Help"]
    ], resize_keyboard=True)

# ================= HELPERS =================
def get_user(uid):
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    return c.fetchone()

def ensure_user(uid, username=None):
    c.execute("INSERT OR IGNORE INTO users (user_id,username,language,joined) VALUES (?,?,?,?)",
              (uid, username, "hi", int(time.time())))
    conn.commit()

def get_lang(uid):
    u = get_user(uid)
    return u[2] if u else "hi"

def save_history(uid, qtype, query, result):
    c.execute("INSERT INTO journey_history (user_id,type,query,result,searched_at) VALUES (?,?,?,?,?)",
              (uid, qtype, query, result[:200], int(time.time())))
    conn.commit()

def txt(lang, hi, en):
    return hi if lang == "hi" else en

# ================= API CALLS =================
def api_pnr(pnr):
    try:
        r = requests.get(f"https://{PNR_HOST}/getPNRStatus/{pnr}",
                         headers=pnr_headers(), timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_train_details(train_no):
    try:
        r = requests.get(f"{BASE}/train-details",
                         headers=train_headers(),
                         params={"trainNo": train_no}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_live(train_no, start_day=1):
    try:
        r = requests.get(f"{BASE}/live-train-status",
                         headers=train_headers(),
                         params={"trainNo": train_no, "startDay": start_day}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_between(from_stn, to_stn, date):
    try:
        r = requests.get(f"{BASE}/trains-between-stations",
                         headers=train_headers(),
                         params={"startStationCode": from_stn,
                                 "endStationCode": to_stn,
                                 "date": date}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_coach(train_no):
    try:
        r = requests.get(f"{BASE}/train-coach-data",
                         headers=train_headers(),
                         params={"trainNo": train_no}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_delay(train_no, period="thismonth"):
    try:
        r = requests.get(f"{BASE}/train-delay-history",
                         headers=train_headers(),
                         params={"trainNo": train_no, "period": period}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ================= FORMATTERS =================
def format_pnr(data, lang):
    try:
        if not data.get("success", True):
            msg = data.get("message", "")
            if "FLUSHED" in msg:
                return txt(lang, "❌ Ye PNR expire ho chuka hai!", "❌ This PNR is flushed!")
            return txt(lang, f"❌ PNR nahi mila! {msg}", f"❌ PNR not found! {msg}")

        d = data.get("data", data)
        if not d or "error" in data:
            return txt(lang, "❌ PNR nahi mila!", "❌ PNR not found!")

        pnr        = d.get("pnrNumber", "—")
        train      = d.get("trainNumber", "—")
        train_name = d.get("trainName", "—")
        from_stn   = d.get("boardingPoint", d.get("sourceStation", "—"))
        to_stn     = d.get("reservationUpto", d.get("destinationStation", "—"))
        doj        = d.get("dateOfJourney", "—")
        arr        = d.get("arrivalDate", "—")
        cls        = d.get("journeyClass", d.get("classType", "—"))
        chart      = d.get("chartStatus", "—")
        fare       = d.get("ticketFare", "—")
        passengers = d.get("passengerList", [])

        msg = (
            f"🎫 *PNR Status*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔢 PNR     : `{pnr}`\n"
            f"🚂 Train   : {train} - {train_name}\n"
            f"📍 From    : {from_stn}\n"
            f"📍 To      : {to_stn}\n"
            f"📅 Journey : {doj}\n"
            f"🕐 Arrival : {arr}\n"
            f"💺 Class   : {cls}\n"
            f"📋 Chart   : {chart}\n"
            f"💰 Fare    : ₹{fare}\n"
            f"━━━━━━━━━━━━━━━━━\n"
        )
        if passengers:
            msg += "👥 *Passenger Status:*\n"
            for p in passengers:
                i         = p.get("passengerSerialNumber", "—")
                booking   = p.get("bookingStatusDetails", "—")
                current   = p.get("currentStatusDetails", "—")
                curr_code = p.get("currentStatus", "")
                if curr_code == "CNF":   emoji = "✅"
                elif curr_code == "RAC": emoji = "🟡"
                elif curr_code in ("WL","RLWL","GNWL","PQWL"): emoji = "🔴"
                else: emoji = "ℹ️"
                msg += f"  {emoji} P{i}: Booked `{booking}` → Now *{current}*\n"
        return msg
    except:
        return txt(lang, "❌ Data parse nahi hua!", "❌ Could not parse data!")

def format_schedule(data, lang):
    try:
        if "error" in data:
            return txt(lang, "❌ Train nahi mili!", "❌ Train not found!")
        # This API returns list directly or under data key
        stations = data if isinstance(data, list) else data.get("data", data.get("route", []))
        train_no   = data.get("trainNumber", data.get("train_no", "—")) if isinstance(data, dict) else "—"
        train_name = data.get("trainName", data.get("train_name", "—")) if isinstance(data, dict) else "—"

        msg = (
            f"🚂 *Train Schedule*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔢 Train : {train_no} - {train_name}\n"
            f"━━━━━━━━━━━━━━━━━\n"
        )
        if isinstance(stations, list):
            for s in stations[:15]:
                stn  = s.get("stationCode", s.get("station_code", "—"))
                name = s.get("stationName", s.get("station_name", "—"))
                arr  = s.get("arrivalTime", s.get("arrival", "—"))
                dep  = s.get("departureTime", s.get("departure", "—"))
                day  = s.get("dayCount", s.get("day", ""))
                msg += f"🏛️ *{name}* ({stn})\n"
                msg += f"   🟢 Arr: {arr}  🔴 Dep: {dep}  📅 Day {day}\n\n"
            if len(stations) > 15:
                msg += f"_...aur {len(stations)-15} stations_\n"
        else:
            msg += txt(lang, "❌ Schedule data nahi mila!", "❌ Schedule data not found!")
        return msg
    except:
        return txt(lang, "❌ Schedule nahi mila!", "❌ Schedule not found!")

def format_live(data, lang):
    try:
        if "error" in data:
            return txt(lang, "❌ Live status nahi mila!", "❌ Live status not found!")
        d = data if isinstance(data, dict) else {}
        # handle nested data key
        if "data" in d:
            d = d["data"]

        train_no   = d.get("trainNumber", d.get("train_no", "—"))
        train_name = d.get("trainName", d.get("train_name", "—"))
        curr_stn   = d.get("currentStation", d.get("current_station", "—"))
        if isinstance(curr_stn, dict):
            curr_stn = curr_stn.get("stationName", curr_stn.get("name", "—"))
        next_stn   = d.get("nextStation", d.get("next_station", "—"))
        if isinstance(next_stn, dict):
            next_stn = next_stn.get("stationName", next_stn.get("name", "—"))
        delay      = d.get("delayedBy", d.get("delay", 0))
        status     = d.get("trainStatus", d.get("status", "—"))

        delay_txt = f"⚠️ {delay} min late" if delay and int(str(delay).replace('-','') or 0) > 0 else "✅ On Time"

        return (
            f"📍 *Live Train Status*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🚂 Train      : {train_no} - {train_name}\n"
            f"📍 At Station : *{curr_stn}*\n"
            f"➡️ Next Stop  : {next_stn}\n"
            f"⏱️ Delay      : {delay_txt}\n"
            f"📊 Status     : {status}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"_Updated: {time.strftime('%I:%M %p')}_"
        )
    except:
        return txt(lang, "❌ Live status nahi mila!", "❌ Live status not found!")

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    await update.message.reply_text(
        "🚂 *Indian Railway Bot*\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "Namaste! 🙏\n\n"
        "✅ PNR Status\n✅ Train Schedule\n✅ Live Train\n"
        "✅ Trains Between Stations\n✅ Coach Position\n"
        "✅ Delay History\n✅ Favourites & Alerts\n\n"
        "Neeche se option chuno 👇",
        reply_markup=main_keyboard(get_lang(uid)),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ================= PNR =================
async def pnr_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(
        txt(lang,
            "🎫 *PNR Status*\n\nApna 10 digit PNR bhejo 👇\n_Example: 8448678822_",
            "🎫 *PNR Status*\n\nEnter 10 digit PNR 👇\n_Example: 8448678822_"),
        parse_mode="Markdown")
    return PNR_INPUT

async def pnr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    pnr  = update.message.text.strip()
    if not pnr.isdigit() or len(pnr) != 10:
        await update.message.reply_text(txt(lang, "❌ 10 digit PNR daalo!", "❌ Enter valid 10 digit PNR!"))
        return PNR_INPUT
    msg    = await update.message.reply_text(txt(lang, "⏳ PNR check ho raha hai...", "⏳ Checking PNR..."))
    data   = api_pnr(pnr)
    result = format_pnr(data, lang)
    save_history(uid, "PNR", pnr, result)
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data=f"pnr_{pnr}"),
            InlineKeyboardButton("🔔 Alert",   callback_data=f"alert_pnr_{pnr}")
        ]]))
    return ConversationHandler.END

# ================= TRAIN SCHEDULE =================
async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(
        txt(lang,
            "🚂 *Train Schedule*\n\nTrain number bhejo 👇\n_Example: 22177_",
            "🚂 *Train Schedule*\n\nEnter train number 👇\n_Example: 22177_"),
        parse_mode="Markdown")
    return TRAIN_INPUT

async def schedule_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    if not train_no.isdigit():
        await update.message.reply_text(txt(lang, "❌ Sahi train number daalo!", "❌ Enter valid train number!"))
        return TRAIN_INPUT
    msg    = await update.message.reply_text(txt(lang, "⏳ Schedule aa raha hai...", "⏳ Fetching schedule..."))
    data   = api_train_details(train_no)
    result = format_schedule(data, lang)
    save_history(uid, "SCHEDULE", train_no, result)
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ Favourite", callback_data=f"fav_add_{train_no}"),
            InlineKeyboardButton("📍 Live",      callback_data=f"live_{train_no}")
        ]]))
    return ConversationHandler.END

# ================= LIVE STATUS =================
async def live_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(
        txt(lang,
            "📍 *Live Train*\n\nTrain number bhejo 👇\n_Example: 22177_",
            "📍 *Live Train*\n\nEnter train number 👇\n_Example: 22177_"),
        parse_mode="Markdown")
    return LIVE_INPUT

async def live_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    if not train_no.isdigit():
        await update.message.reply_text(txt(lang, "❌ Sahi train number daalo!", "❌ Enter valid train number!"))
        return LIVE_INPUT
    msg    = await update.message.reply_text(txt(lang, "⏳ Live status aa raha hai...", "⏳ Fetching live status..."))
    data   = api_live(train_no)
    result = format_live(data, lang)
    save_history(uid, "LIVE", train_no, result)
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh",   callback_data=f"live_{train_no}"),
            InlineKeyboardButton("🔔 Late Alert", callback_data=f"alert_train_{train_no}")
        ]]))
    return ConversationHandler.END

# ================= TRAINS BETWEEN STATIONS =================
async def between_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(
        txt(lang,
            "🔍 *Trains Between Stations*\n\nFrom station code bhejo 👇\n_Example: CSMT_",
            "🔍 *Trains Between Stations*\n\nEnter FROM station code 👇\n_Example: CSMT_"),
        parse_mode="Markdown")
    return BETWEEN_FROM

async def between_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    context.user_data["between_from"] = update.message.text.strip().upper()
    await update.message.reply_text(
        txt(lang, "📍 To station code bhejo 👇\n_Example: NDLS_",
                  "📍 Enter TO station code 👇\n_Example: NDLS_"),
        parse_mode="Markdown")
    return BETWEEN_TO

async def between_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    context.user_data["between_to"] = update.message.text.strip().upper()
    await update.message.reply_text(
        txt(lang, "📅 Date bhejo (DD-MM-YYYY) 👇\n_Example: 09-06-2026_",
                  "📅 Enter date (DD-MM-YYYY) 👇\n_Example: 09-06-2026_"),
        parse_mode="Markdown")
    return BETWEEN_DATE

async def between_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    date     = update.message.text.strip()
    from_stn = context.user_data.get("between_from", "")
    to_stn   = context.user_data.get("between_to", "")
    msg      = await update.message.reply_text(txt(lang, "⏳ Trains dhundh raha hun...", "⏳ Searching trains..."))
    data     = api_between(from_stn, to_stn, date)
    try:
        trains = data if isinstance(data, list) else data.get("data", [])
        if not trains:
            result = txt(lang, "❌ Koi train nahi mili!", "❌ No trains found!")
        else:
            result = f"🔍 *Trains: {from_stn} → {to_stn}*\n📅 {date}\n━━━━━━━━━━━━━━━━━\n\n"
            for t in trains[:10]:
                tno   = t.get("trainNumber", t.get("train_no", "—"))
                tname = t.get("trainName", t.get("train_name", "—"))
                dep   = t.get("departureTime", t.get("departure", "—"))
                arr   = t.get("arrivalTime", t.get("arrival", "—"))
                dur   = t.get("duration", "—")
                result += f"🚂 *{tno}* - {tname}\n   🟢 Dep: {dep}  🔴 Arr: {arr}  ⏱️ {dur}\n\n"
            if len(trains) > 10:
                result += f"_...aur {len(trains)-10} trains_"
    except:
        result = txt(lang, "❌ Data parse nahi hua!", "❌ Could not parse data!")
    save_history(uid, "BETWEEN", f"{from_stn}-{to_stn}", result)
    await msg.edit_text(result, parse_mode="Markdown")
    return ConversationHandler.END

# ================= COACH POSITION =================
async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(
        txt(lang,
            "🚃 *Coach Position*\n\nTrain number bhejo 👇\n_Example: 22177_",
            "🚃 *Coach Position*\n\nEnter train number 👇\n_Example: 22177_"),
        parse_mode="Markdown")
    return COACH_TRAIN

async def coach_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    msg      = await update.message.reply_text(txt(lang, "⏳ Coach data aa raha hai...", "⏳ Fetching coach data..."))
    data     = api_coach(train_no)
    try:
        coaches = data if isinstance(data, list) else data.get("data", data.get("coaches", []))
        if not coaches:
            result = txt(lang, "❌ Coach data nahi mila!", "❌ Coach data not found!")
        else:
            result = f"🚃 *Coach Position — {train_no}*\n━━━━━━━━━━━━━━━━━\n\n"
            for coach in coaches[:20]:
                cno   = coach.get("coachNumber", coach.get("coach_no", "—"))
                ctype = coach.get("coachType", coach.get("type", "—"))
                pos   = coach.get("position", "—")
                result += f"🚃 *{cno}* ({ctype}) — Pos: {pos}\n"
    except:
        result = txt(lang, "❌ Data fetch nahi hua!", "❌ Could not fetch data!")
    await msg.edit_text(result, parse_mode="Markdown")
    return ConversationHandler.END

# ================= DELAY HISTORY =================
async def delay_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(
        txt(lang,
            "⏱️ *Delay History*\n\nTrain number bhejo 👇\n_Example: 22177_",
            "⏱️ *Delay History*\n\nEnter train number 👇\n_Example: 22177_"),
        parse_mode="Markdown")
    return TRAIN_INPUT

async def delay_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    msg      = await update.message.reply_text(txt(lang, "⏳ Delay history aa rahi hai...", "⏳ Fetching delay history..."))
    data     = api_delay(train_no)
    try:
        d      = data if isinstance(data, dict) else {}
        result = (
            f"⏱️ *Delay History — {train_no}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 Avg Delay : {d.get('averageDelay', d.get('avg_delay', '—'))} min\n"
            f"🔴 Max Delay : {d.get('maxDelay', d.get('max_delay', '—'))} min\n"
            f"✅ On Time   : {d.get('onTimePercentage', d.get('on_time_pct', '—'))}%\n"
            f"📅 Period    : This Month\n"
            f"━━━━━━━━━━━━━━━━━"
        )
    except:
        result = txt(lang, "❌ Delay data nahi mila!", "❌ Delay data not found!")
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📅 This Week",  callback_data=f"delay_{train_no}_thisweek"),
            InlineKeyboardButton("📅 This Year",  callback_data=f"delay_{train_no}_thisyear"),
        ]]))
    return ConversationHandler.END

# ================= FAVOURITES =================
async def favourites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    c.execute("SELECT train_no, train_name FROM favourite_trains WHERE user_id=?", (uid,))
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text(
            txt(lang, "⭐ Koi favourite nahi! Train schedule dekh ke save karo.", "⭐ No favourites yet!"),
            parse_mode="Markdown")
        return
    msg  = "⭐ *Favourite Trains*\n\n"
    btns = []
    for tno, tname in rows:
        msg += f"🚂 {tno} - {tname or 'Unknown'}\n"
        btns.append([
            InlineKeyboardButton(f"📍 Live: {tno}", callback_data=f"live_{tno}"),
            InlineKeyboardButton("🗑️ Remove",       callback_data=f"fav_rm_{tno}")
        ])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

# ================= HISTORY =================
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    c.execute("SELECT type,query,searched_at FROM journey_history WHERE user_id=? ORDER BY searched_at DESC LIMIT 10", (uid,))
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text(txt(lang, "📜 Koi history nahi!", "📜 No history!"))
        return
    msg = "📜 *Search History*\n\n"
    for qtype, query, ts in rows:
        date = time.strftime("%d/%m %I:%M%p", time.localtime(ts))
        msg += f"🔹 {qtype}: `{query}` — {date}\n"
    await update.message.reply_text(msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Clear", callback_data="clear_history")]]))

# ================= ALERTS =================
async def alerts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    c.execute("SELECT id,type,train_no,pnr FROM alerts WHERE user_id=? AND active=1", (uid,))
    rows = c.fetchall()
    msg  = "🔔 *Active Alerts*\n\n"
    btns = []
    if not rows:
        msg += txt(lang, "Koi alert nahi! PNR ya Train check karte waqt set karo.", "No alerts!")
    for aid, atype, tno, pnr in rows:
        label = f"PNR: {pnr}" if atype == "PNR" else f"Train: {tno}"
        msg  += f"🔔 {atype} — {label}\n"
        btns.append([InlineKeyboardButton(f"❌ Remove #{aid}", callback_data=f"alert_rm_{aid}")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns) if btns else None, parse_mode="Markdown")

# ================= HELP =================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Railway Bot — Help*\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🎫 *PNR Status* — Ticket ka current status\n"
        "🚂 *Train Schedule* — Sare stops\n"
        "📍 *Live Train* — Abhi kahan hai train\n"
        "🔍 *Trains Between* — Do stations ke beech trains\n"
        "🚃 *Coach Position* — Coach kahan hoga platform pe\n"
        "⏱️ *Delay History* — Train kitna late rehti hai\n"
        "⭐ *Favourites* — Apni trains save karo\n"
        "📜 *History* — Purane searches\n"
        "🔔 *Alerts* — Late alert set karo\n"
        "🌐 *Language* — Hindi / English\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "🆘 Helpline: *139* | RPF: *182*",
        parse_mode="Markdown")

# ================= LANGUAGE =================
async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌐 *Language*\n\nChuno:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇮🇳 Hindi",   callback_data="lang_hi"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
        ]]), parse_mode="Markdown")

# ================= CALLBACK =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid   = query.from_user.id
    lang  = get_lang(uid)
    data  = query.data
    await query.answer()

    if data.startswith("pnr_"):
        pnr    = data[4:]
        result = format_pnr(api_pnr(pnr), lang)
        await query.edit_message_text(result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"pnr_{pnr}"),
                InlineKeyboardButton("🔔 Alert",   callback_data=f"alert_pnr_{pnr}")
            ]]))

    elif data.startswith("live_"):
        tno    = data[5:]
        result = format_live(api_live(tno), lang)
        await query.edit_message_text(result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",   callback_data=f"live_{tno}"),
                InlineKeyboardButton("🔔 Late Alert", callback_data=f"alert_train_{tno}")
            ]]))

    elif data.startswith("delay_"):
        parts  = data.split("_")
        tno    = parts[1]
        period = parts[2] if len(parts) > 2 else "thismonth"
        d      = api_delay(tno, period)
        try:
            result = (
                f"⏱️ *Delay History — {tno}*\n━━━━━━━━━━━━━━━━━\n"
                f"📊 Avg: {d.get('averageDelay','—')} min\n"
                f"🔴 Max: {d.get('maxDelay','—')} min\n"
                f"✅ On Time: {d.get('onTimePercentage','—')}%\n"
                f"📅 Period: {period}"
            )
        except:
            result = "❌ Data nahi mila!"
        await query.edit_message_text(result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📅 This Week",  callback_data=f"delay_{tno}_thisweek"),
                InlineKeyboardButton("📅 This Year",  callback_data=f"delay_{tno}_thisyear"),
            ]]))

    elif data.startswith("fav_add_"):
        tno = data[8:]
        c.execute("INSERT OR IGNORE INTO favourite_trains (user_id,train_no) VALUES (?,?)", (uid, tno))
        conn.commit()
        await query.answer("⭐ Favourite mein add!", show_alert=True)

    elif data.startswith("fav_rm_"):
        tno = data[7:]
        c.execute("DELETE FROM favourite_trains WHERE user_id=? AND train_no=?", (uid, tno))
        conn.commit()
        await query.answer("🗑️ Remove ho gaya!", show_alert=True)

    elif data == "clear_history":
        c.execute("DELETE FROM journey_history WHERE user_id=?", (uid,))
        conn.commit()
        await query.edit_message_text("🗑️ History clear!")

    elif data.startswith("alert_pnr_"):
        pnr = data[10:]
        c.execute("INSERT INTO alerts (user_id,type,pnr,created_at) VALUES (?,?,?,?)", (uid,"PNR",pnr,int(time.time())))
        conn.commit()
        await query.answer("🔔 Alert set!", show_alert=True)

    elif data.startswith("alert_train_"):
        tno = data[12:]
        c.execute("INSERT INTO alerts (user_id,type,train_no,created_at) VALUES (?,?,?,?)", (uid,"TRAIN",tno,int(time.time())))
        conn.commit()
        await query.answer("🔔 Alert set!", show_alert=True)

    elif data.startswith("alert_rm_"):
        aid = int(data[9:])
        c.execute("UPDATE alerts SET active=0 WHERE id=? AND user_id=?", (aid, uid))
        conn.commit()
        await query.answer("❌ Alert removed!", show_alert=True)

    elif data.startswith("lang_"):
        new_lang = data[5:]
        c.execute("UPDATE users SET language=? WHERE user_id=?", (new_lang, uid))
        conn.commit()
        await query.edit_message_text(txt(new_lang, "✅ Hindi set!", "✅ English set!"))

# ================= TEXT HANDLER =================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid  = update.effective_user.id
    ensure_user(uid, update.effective_user.username)

    if text == "🎫 PNR Status":        return await pnr_start(update, context)
    elif text == "🚂 Train Schedule":  return await schedule_start(update, context)
    elif text == "📍 Live Train":      return await live_start(update, context)
    elif text == "🔍 Trains Between":  return await between_start(update, context)
    elif text == "🚃 Coach Position":  return await coach_start(update, context)
    elif text == "⏱️ Delay History":  return await delay_start(update, context)
    elif text == "⭐ Favourites":      return await favourites(update, context)
    elif text == "📜 History":         return await history(update, context)
    elif text == "🔔 Alerts":          return await alerts_menu(update, context)
    elif text == "🌐 Language":        return await language_menu(update, context)
    elif text == "ℹ️ Help":            return await help_cmd(update, context)
    elif text == "🏛️ Station Board":
        lang = get_lang(uid)
        await update.message.reply_text(
            txt(lang, "🏛️ Station Board abhi available nahi — jaldi aayega!", "🏛️ Station Board coming soon!"))

# ================= ERROR =================
async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    print(f"[ERROR] {context.error}")
    traceback.print_exc()

# ================= RUN =================
app = (
    ApplicationBuilder()
    .token(TOKEN)
    .connect_timeout(30)
    .read_timeout(30)
    .write_timeout(30)
    .pool_timeout(30)
    .build()
)

conv_pnr = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🎫 PNR Status$"), pnr_start)],
    states={PNR_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pnr_check)]},
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)
conv_schedule = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🚂 Train Schedule$"), schedule_start)],
    states={TRAIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_check)]},
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)
conv_live = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^📍 Live Train$"), live_start)],
    states={LIVE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, live_check)]},
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)
conv_between = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🔍 Trains Between$"), between_start)],
    states={
        BETWEEN_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, between_to)],
        BETWEEN_TO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, between_date)],
        BETWEEN_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, between_check)],
    },
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)
conv_coach = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🚃 Coach Position$"), coach_start)],
    states={COACH_TRAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, coach_check)]},
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)
conv_delay = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^⏱️ Delay History$"), delay_start)],
    states={TRAIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delay_check)]},
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help",  help_cmd))
app.add_handler(conv_pnr)
app.add_handler(conv_schedule)
app.add_handler(conv_live)
app.add_handler(conv_between)
app.add_handler(conv_coach)
app.add_handler(conv_delay)
app.add_handler(CallbackQueryHandler(callback_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_error_handler(error_handler)

print("🚂 INDIAN RAILWAY BOT RUNNING!")
app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])
