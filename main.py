import os
import time
import asyncio
import sqlite3
import requests
from bs4 import BeautifulSoup
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackQueryHandler, ConversationHandler
)

# ================= CONFIG =================
TOKEN = os.environ.get("BOT_TOKEN", "8884909837:AAEF9MHEhDytK66yJKhLMijttlOCcHhCqrU")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-IN,en;q=0.9,hi;q=0.8',
    'Referer': 'https://erail.in/',
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

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
PNR_INPUT = 1; TRAIN_INPUT = 2; STATION_INPUT = 3
FARE_TRAIN = 4; FARE_FROM = 5; FARE_TO = 6
LIVE_INPUT = 7; COACH_TRAIN = 8

# ================= HELPERS =================
def get_user(uid):
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    return c.fetchone()

def ensure_user(uid, username=None):
    c.execute("INSERT OR IGNORE INTO users (user_id, username, language, joined) VALUES (?,?,?,?)",
              (uid, username, "hi", int(time.time())))
    conn.commit()

def get_lang(uid):
    user = get_user(uid)
    return user[2] if user else "hi"

def save_history(uid, qtype, query, result):
    c.execute("INSERT INTO journey_history (user_id, type, query, result, searched_at) VALUES (?,?,?,?,?)",
              (uid, qtype, query, result[:300], int(time.time())))
    conn.commit()

def txt(lang, hi, en):
    return hi if lang == "hi" else en

def main_keyboard():
    return ReplyKeyboardMarkup([
        ["🎫 PNR Status",      "🚂 Train Schedule"],
        ["📍 Live Train",      "🏛️ Station Board"],
        ["💰 Fare Calc",       "🚃 Coach Position"],
        ["⭐ Favourites",      "📜 History"],
        ["🔔 Alerts",          "🌐 Language"],
        ["ℹ️ Help"]
    ], resize_keyboard=True)

# ================= SCRAPERS =================
def scrape_pnr(pnr):
    try:
        url  = f"https://erail.in/pnr-status/{pnr}"
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        result = {'pnr': pnr, 'success': False}

        # Try multiple selectors for train info
        for sel in ['div.train-details', 'div.pnr-train', 'div.train-info', 'h2', 'h3']:
            el = soup.select_one(sel)
            if el and el.text.strip():
                result['train_info'] = el.text.strip()
                break

        # Passenger table
        passengers = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            for row in rows[1:6]:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    passengers.append(' | '.join(c.text.strip() for c in cols if c.text.strip()))
        result['passengers'] = passengers
        result['raw']        = soup.get_text(separator='\n', strip=True)[:1500]
        result['success']    = True
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}

def scrape_schedule(train_no):
    try:
        url  = f"https://erail.in/train/{train_no}"
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        result = {'train_no': train_no, 'success': False}

        # Train name
        for sel in ['h1', 'h2', 'div.train-name', 'title']:
            el = soup.select_one(sel)
            if el and el.text.strip():
                result['train_name'] = el.text.strip()
                break

        # Stations table
        stations = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) > 3:
                for row in rows[1:21]:
                    cols = row.find_all('td')
                    if len(cols) >= 3:
                        stations.append({
                            'name': cols[0].text.strip(),
                            'arr':  cols[1].text.strip() if len(cols) > 1 else '—',
                            'dep':  cols[2].text.strip() if len(cols) > 2 else '—',
                            'day':  cols[3].text.strip() if len(cols) > 3 else '—',
                        })
                if stations:
                    break
        result['stations'] = stations
        result['success']  = len(stations) > 0
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}

def scrape_live(train_no):
    try:
        url  = f"https://erail.in/live-train-running-status/{train_no}"
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        result = {'train_no': train_no, 'success': False}

        # Get all meaningful text
        result['raw'] = soup.get_text(separator='\n', strip=True)[:1500]

        # Try to find delay/status
        for keyword in ['delay', 'late', 'on time', 'running']:
            for el in soup.find_all(string=lambda t: t and keyword.lower() in t.lower()):
                result['status_hint'] = el.strip()
                break

        # Stops table
        stops = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) > 3:
                for row in rows[1:10]:
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        stops.append(' | '.join(c.text.strip() for c in cols if c.text.strip()))
                if stops:
                    break
        result['stops']   = stops
        result['success'] = True
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}

def scrape_station(stn_code):
    try:
        url  = f"https://erail.in/station/{stn_code}"
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        result = {'station': stn_code, 'success': False}

        trains = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) > 3:
                for row in rows[1:11]:
                    cols = row.find_all('td')
                    if len(cols) >= 3:
                        trains.append({
                            'no':   cols[0].text.strip(),
                            'name': cols[1].text.strip() if len(cols) > 1 else '—',
                            'arr':  cols[2].text.strip() if len(cols) > 2 else '—',
                            'dep':  cols[3].text.strip() if len(cols) > 3 else '—',
                        })
                if trains:
                    break
        result['trains']  = trains
        result['success'] = len(trains) > 0
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}

def scrape_fare(train_no, from_stn, to_stn):
    try:
        url  = f"https://erail.in/fare/{train_no}/{from_stn}/{to_stn}"
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        result = {'train_no': train_no, 'from': from_stn, 'to': to_stn, 'success': False}

        fares = {}
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    fares[cols[0].text.strip()] = cols[1].text.strip()
            if fares:
                break
        result['fares']   = fares
        result['raw']     = soup.get_text(separator='\n', strip=True)[:800]
        result['success'] = True
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}

# ================= FORMAT RESULTS =================
def fmt_pnr(data, lang, pnr):
    if not data['success']:
        return txt(lang, f"❌ PNR {pnr} nahi mila! Sahi PNR daalo.", f"❌ PNR {pnr} not found!")
    msg = f"🎫 *PNR Status — {pnr}*\n━━━━━━━━━━━━━━━━━\n"
    if data.get('train_info'):
        msg += f"🚂 {data['train_info']}\n\n"
    if data.get('passengers'):
        msg += "👥 *Passengers:*\n"
        for p in data['passengers'][:5]:
            msg += f"  • {p}\n"
    elif data.get('raw'):
        # Extract useful lines from raw text
        lines = [l for l in data['raw'].split('\n') if l.strip() and len(l.strip()) > 3]
        for line in lines[:15]:
            msg += f"{line}\n"
    return msg

def fmt_schedule(data, lang):
    if not data['success']:
        return txt(lang, "❌ Train schedule nahi mila!", "❌ Train schedule not found!")
    msg = f"🚂 *Train Schedule — {data['train_no']}*\n"
    if data.get('train_name'):
        msg += f"📋 {data['train_name']}\n"
    msg += "━━━━━━━━━━━━━━━━━\n\n"
    for s in data.get('stations', [])[:15]:
        msg += f"🏛️ *{s['name']}*\n  🟢 {s['arr']} → 🔴 {s['dep']}  📅 Day {s['day']}\n\n"
    return msg

def fmt_live(data, lang):
    if not data['success']:
        return txt(lang, "❌ Live status nahi mila!", "❌ Live status not found!")
    msg = f"📍 *Live Status — Train {data['train_no']}*\n━━━━━━━━━━━━━━━━━\n"
    if data.get('status_hint'):
        msg += f"⚡ {data['status_hint']}\n\n"
    if data.get('stops'):
        msg += "🚉 *Recent Stops:*\n"
        for s in data['stops'][:8]:
            msg += f"  • {s}\n"
    elif data.get('raw'):
        lines = [l for l in data['raw'].split('\n') if l.strip() and len(l.strip()) > 3]
        for line in lines[:12]:
            msg += f"{line}\n"
    msg += f"\n_Updated: {time.strftime('%I:%M %p')}_"
    return msg

def fmt_station(data, lang):
    if not data['success']:
        return txt(lang, f"❌ Station board nahi mila!", f"❌ Station board not found!")
    msg = f"🏛️ *Station Board — {data['station']}*\n━━━━━━━━━━━━━━━━━\n\n"
    for t in data.get('trains', [])[:10]:
        msg += f"🚂 *{t['no']}* - {t['name']}\n  🟢 {t['arr']} | 🔴 {t['dep']}\n\n"
    return msg

def fmt_fare(data, lang):
    msg = f"💰 *Fare — {data.get('train_no','?')}*\n"
    msg += f"📍 {data.get('from','?')} → {data.get('to','?')}\n━━━━━━━━━━━━━━━━━\n\n"
    if data.get('fares'):
        for cls, fare in data['fares'].items():
            msg += f"💺 {cls}: ₹{fare}\n"
    elif data.get('raw'):
        lines = [l for l in data['raw'].split('\n') if l.strip() and '₹' in l or 'fare' in l.lower() or any(c in l for c in ['SL','3A','2A','1A','CC'])]
        for line in lines[:10]:
            msg += f"{line}\n"
    return msg if len(msg) > 50 else txt(lang, "❌ Fare nahi mila!", "❌ Fare not found!")

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    await update.message.reply_text(
        "🚂 *Indian Railway Bot*\n━━━━━━━━━━━━━━━━━\n\n"
        "Namaste! 🙏\n\nMain aapki railway journey mein help karunga!\n\n"
        "✅ PNR Status\n✅ Train Schedule\n✅ Live Train Status\n"
        "✅ Station Board\n✅ Fare Calculator\n✅ Coach Position\n"
        "✅ Favourites & History\n✅ Train Alerts\n\n"
        "Neeche se option chuno 👇",
        reply_markup=main_keyboard(), parse_mode="Markdown"
    )

# ================= PNR =================
async def pnr_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎫 *PNR Status*\n\n10 digit PNR bhejo 👇\n_Example: 2134567890_",
        parse_mode="Markdown")
    return PNR_INPUT

async def pnr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    pnr  = update.message.text.strip()
    if not pnr.isdigit() or len(pnr) != 10:
        await update.message.reply_text(txt(lang, "❌ 10 digit PNR daalo!", "❌ Enter valid 10 digit PNR!"))
        return PNR_INPUT
    msg = await update.message.reply_text("⏳ PNR check ho raha hai...")
    data   = scrape_pnr(pnr)
    result = fmt_pnr(data, lang, pnr)
    save_history(uid, "PNR", pnr, result)
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh",    callback_data=f"pnr_{pnr}"),
            InlineKeyboardButton("🔔 Alert Set",  callback_data=f"alert_pnr_{pnr}")
        ]]))
    return ConversationHandler.END

# ================= SCHEDULE =================
async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚂 *Train Schedule*\n\nTrain number bhejo 👇\n_Example: 12951_",
        parse_mode="Markdown")
    return TRAIN_INPUT

async def schedule_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    if not train_no.isdigit():
        await update.message.reply_text("❌ Sahi train number daalo!")
        return TRAIN_INPUT
    msg    = await update.message.reply_text("⏳ Schedule fetch ho raha hai...")
    data   = scrape_schedule(train_no)
    result = fmt_schedule(data, lang)
    save_history(uid, "SCHEDULE", train_no, result)
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ Favourite", callback_data=f"fav_{train_no}"),
            InlineKeyboardButton("📍 Live",      callback_data=f"live_{train_no}")
        ]]))
    return ConversationHandler.END

# ================= LIVE =================
async def live_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📍 *Live Train Status*\n\nTrain number bhejo 👇\n_Example: 12951_",
        parse_mode="Markdown")
    return LIVE_INPUT

async def live_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    if not train_no.isdigit():
        await update.message.reply_text("❌ Sahi train number daalo!")
        return LIVE_INPUT
    msg    = await update.message.reply_text("⏳ Live status fetch ho raha hai...")
    data   = scrape_live(train_no)
    result = fmt_live(data, lang)
    save_history(uid, "LIVE", train_no, result)
    await msg.edit_text(result, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh",   callback_data=f"live_{train_no}"),
            InlineKeyboardButton("🔔 Late Alert", callback_data=f"alert_train_{train_no}")
        ]]))
    return ConversationHandler.END

# ================= STATION =================
async def station_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛️ *Station Board*\n\nStation code bhejo 👇\n_Example: NDLS, CSTM, BCT_",
        parse_mode="Markdown")
    return STATION_INPUT

async def station_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    lang   = get_lang(uid)
    stn    = update.message.text.strip().upper()
    msg    = await update.message.reply_text("⏳ Station board fetch ho raha hai...")
    data   = scrape_station(stn)
    result = fmt_station(data, lang)
    save_history(uid, "STATION", stn, result)
    await msg.edit_text(result, parse_mode="Markdown")
    return ConversationHandler.END

# ================= FARE =================
async def fare_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💰 *Fare Calculator*\n\nTrain number bhejo 👇\n_Example: 12951_",
        parse_mode="Markdown")
    return FARE_TRAIN

async def fare_train(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['fare_train'] = update.message.text.strip()
    await update.message.reply_text("📍 FROM station code bhejo\n_Example: NDLS_", parse_mode="Markdown")
    return FARE_FROM

async def fare_from_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['fare_from'] = update.message.text.strip().upper()
    await update.message.reply_text("📍 TO station code bhejo\n_Example: CSTM_", parse_mode="Markdown")
    return FARE_TO

async def fare_to_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    to_stn   = update.message.text.strip().upper()
    train_no = context.user_data.get('fare_train', '')
    from_stn = context.user_data.get('fare_from', '')
    msg      = await update.message.reply_text("⏳ Fare calculate ho raha hai...")
    data     = scrape_fare(train_no, from_stn, to_stn)
    result   = fmt_fare(data, lang)
    save_history(uid, "FARE", f"{train_no}/{from_stn}/{to_stn}", result)
    await msg.edit_text(result, parse_mode="Markdown")
    return ConversationHandler.END

# ================= COACH POSITION =================
async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚃 *Coach Position*\n\nTrain number bhejo 👇\n_Example: 12951_",
        parse_mode="Markdown")
    return COACH_TRAIN

async def coach_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    train_no = update.message.text.strip()
    msg      = await update.message.reply_text("⏳ Coach position fetch ho rahi hai...")
    try:
        url  = f"https://erail.in/coach-position/{train_no}"
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        coaches = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    coaches.append(' | '.join(c.text.strip() for c in cols))
            if coaches:
                break
        if coaches:
            result = f"🚃 *Coach Position — {train_no}*\n━━━━━━━━━━━━━━━━━\n\n"
            for coach in coaches[:20]:
                result += f"• {coach}\n"
        else:
            lines  = [l for l in soup.get_text('\n', strip=True).split('\n') if l.strip() and len(l) > 2]
            result = f"🚃 *Coach Position — {train_no}*\n━━━━━━━━━━━━━━━━━\n\n"
            result += '\n'.join(lines[:15])
    except Exception as e:
        result = txt(lang, "❌ Coach data nahi mila!", "❌ Coach data not found!")
    await msg.edit_text(result, parse_mode="Markdown")
    return ConversationHandler.END

# ================= FAVOURITES =================
async def favourites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    c.execute("SELECT train_no, train_name FROM favourite_trains WHERE user_id=?", (uid,))
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text(
            txt(lang, "⭐ Koi favourite nahi!\nTrain schedule dekhke save karo.", "⭐ No favourites!\nSave from train schedule."))
        return
    msg  = "⭐ *Favourite Trains*\n\n"
    btns = []
    for train_no, name in rows:
        msg += f"🚂 {train_no} - {name or 'Unknown'}\n"
        btns.append([
            InlineKeyboardButton(f"📍 Live: {train_no}", callback_data=f"live_{train_no}"),
            InlineKeyboardButton("🗑️ Remove",             callback_data=f"fav_rm_{train_no}")
        ])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

# ================= HISTORY =================
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    c.execute("SELECT type, query, searched_at FROM journey_history WHERE user_id=? ORDER BY searched_at DESC LIMIT 10", (uid,))
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text(txt(lang, "📜 Koi history nahi!", "📜 No history!"))
        return
    msg = "📜 *Search History*\n\n"
    for qtype, query, ts in rows:
        date  = time.strftime("%d/%m %I:%M%p", time.localtime(ts))
        msg  += f"🔹 {qtype}: `{query}` — {date}\n"
    await update.message.reply_text(msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Clear", callback_data="clear_history")]]))

# ================= ALERTS =================
async def alerts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    c.execute("SELECT id, type, train_no, pnr FROM alerts WHERE user_id=? AND active=1", (uid,))
    rows = c.fetchall()
    msg  = "🔔 *Active Alerts*\n\n"
    btns = []
    if not rows:
        msg += txt(lang, "Koi active alert nahi!\nPNR check karte waqt set karo.", "No active alerts!")
    for aid, atype, train_no, pnr in rows:
        label = f"PNR: {pnr}" if atype == "PNR" else f"Train: {train_no}"
        msg  += f"🔔 {atype} — {label}\n"
        btns.append([InlineKeyboardButton(f"❌ Remove #{aid}", callback_data=f"alert_rm_{aid}")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns) if btns else None, parse_mode="Markdown")

# ================= LANGUAGE =================
async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌐 *Language / भाषा*",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇮🇳 Hindi",   callback_data="lang_hi"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
        ]]),
        parse_mode="Markdown"
    )

# ================= HELP =================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Railway Bot — Help*\n━━━━━━━━━━━━━━━━━\n\n"
        "🎫 *PNR Status* — Ticket current status\n"
        "🚂 *Train Schedule* — Sare stops aur timing\n"
        "📍 *Live Train* — Abhi train kahan hai\n"
        "🏛️ *Station Board* — Station ki aane wali trains\n"
        "💰 *Fare Calc* — Route ka fare\n"
        "🚃 *Coach Position* — Coach platform pe kahan\n"
        "⭐ *Favourites* — Apni trains save karo\n"
        "📜 *History* — Purane searches\n"
        "🔔 *Alerts* — Train/PNR alerts\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "🆘 Railway Helpline: *139*\n"
        "🚔 RPF: *182*\n"
        "🏥 Medical: *138*\n"
        "👮 Security: *1800-111-322*",
        parse_mode="Markdown"
    )

# ================= CALLBACK HANDLER =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid   = query.from_user.id
    lang  = get_lang(uid)
    data  = query.data
    await query.answer()

    if data.startswith("pnr_"):
        pnr    = data[4:]
        result = fmt_pnr(scrape_pnr(pnr), lang, pnr)
        await query.edit_message_text(result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",   callback_data=f"pnr_{pnr}"),
                InlineKeyboardButton("🔔 Alert Set", callback_data=f"alert_pnr_{pnr}")
            ]]))

    elif data.startswith("live_"):
        train_no = data[5:]
        result   = fmt_live(scrape_live(train_no), lang)
        await query.edit_message_text(result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",    callback_data=f"live_{train_no}"),
                InlineKeyboardButton("🔔 Late Alert", callback_data=f"alert_train_{train_no}")
            ]]))

    elif data.startswith("fav_rm_"):
        train_no = data[7:]
        c.execute("DELETE FROM favourite_trains WHERE user_id=? AND train_no=?", (uid, train_no))
        conn.commit()
        await query.answer("🗑️ Removed!", show_alert=True)

    elif data.startswith("fav_"):
        train_no = data[4:]
        c.execute("INSERT OR IGNORE INTO favourite_trains (user_id, train_no) VALUES (?,?)", (uid, train_no))
        conn.commit()
        await query.answer("⭐ Favourite mein add!", show_alert=True)

    elif data == "clear_history":
        c.execute("DELETE FROM journey_history WHERE user_id=?", (uid,))
        conn.commit()
        await query.edit_message_text("🗑️ History clear ho gayi!")

    elif data.startswith("alert_pnr_"):
        pnr = data[10:]
        c.execute("INSERT INTO alerts (user_id, type, pnr, created_at) VALUES (?,?,?,?)",
                  (uid, "PNR", pnr, int(time.time())))
        conn.commit()
        await query.answer("🔔 PNR Alert set ho gaya!", show_alert=True)

    elif data.startswith("alert_train_"):
        train_no = data[12:]
        c.execute("INSERT INTO alerts (user_id, type, train_no, created_at) VALUES (?,?,?,?)",
                  (uid, "TRAIN", train_no, int(time.time())))
        conn.commit()
        await query.answer("🔔 Train Alert set ho gaya!", show_alert=True)

    elif data.startswith("alert_rm_"):
        aid = int(data[9:])
        c.execute("UPDATE alerts SET active=0 WHERE id=? AND user_id=?", (aid, uid))
        conn.commit()
        await query.answer("❌ Alert removed!", show_alert=True)

    elif data.startswith("lang_"):
        new_lang = data[5:]
        c.execute("UPDATE users SET language=? WHERE user_id=?", (new_lang, uid))
        conn.commit()
        await query.edit_message_text(
            "✅ Hindi set ho gayi! 🇮🇳" if new_lang == "hi" else "✅ English set! 🇬🇧")

# ================= TEXT HANDLER =================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    text = update.message.text

    if text == "⭐ Favourites":     await favourites(update, context)
    elif text == "📜 History":      await history(update, context)
    elif text == "🔔 Alerts":       await alerts_menu(update, context)
    elif text == "🌐 Language":     await language_menu(update, context)
    elif text == "ℹ️ Help":         await help_cmd(update, context)

# ================= ERROR HANDLER =================
async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    print(f"[ERROR] {context.error}")
    traceback.print_exc()

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

def cancel(update, context): return ConversationHandler.END

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help",  help_cmd))

app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🎫 PNR Status$"), pnr_start)],
    states={PNR_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pnr_check)]},
    fallbacks=[CommandHandler("cancel", cancel)]
))
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🚂 Train Schedule$"), schedule_start)],
    states={TRAIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_check)]},
    fallbacks=[CommandHandler("cancel", cancel)]
))
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^📍 Live Train$"), live_start)],
    states={LIVE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, live_check)]},
    fallbacks=[CommandHandler("cancel", cancel)]
))
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🏛️ Station Board$"), station_start)],
    states={STATION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, station_check)]},
    fallbacks=[CommandHandler("cancel", cancel)]
))
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^💰 Fare Calc$"), fare_start)],
    states={
        FARE_TRAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, fare_train)],
        FARE_FROM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, fare_from_input)],
        FARE_TO:    [MessageHandler(filters.TEXT & ~filters.COMMAND, fare_to_input)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
))
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🚃 Coach Position$"), coach_start)],
    states={COACH_TRAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, coach_check)]},
    fallbacks=[CommandHandler("cancel", cancel)]
))

app.add_handler(CallbackQueryHandler(callback_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_error_handler(error_handler)

print("🚂 INDIAN RAILWAY BOT RUNNING — erail.in scraping mode!")
app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])
