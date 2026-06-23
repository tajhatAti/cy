"""
╔══════════════════════════════════════════════════════╗
║        BDCyber Intelligence Bot v1.0                ║
║  OSINT | Link Analyzer | Hash | Breach | Admin Log  ║
╚══════════════════════════════════════════════════════╝
"""

import os
import json
import time
import hashlib
import base64
import socket
import threading
import requests
import sqlite3
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types
from urllib.parse import urlparse

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "YOUR_TOKEN")
OWNER_ID   = int(os.environ.get("OWNER_ID", "123456789"))
GH_TOKEN   = os.environ.get("GH_TOKEN", "")
GH_REPO    = os.environ.get("GH_REPO", "user/repo")
GH_FILE    = "bdcyber_state.json"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
db = sqlite3.connect("bdcyber.db", check_same_thread=False)
db_lock = threading.Lock()
cur = db.cursor()

cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
    uid INTEGER PRIMARY KEY,
    name TEXT,
    username TEXT,
    first_seen TEXT,
    last_seen TEXT,
    total_actions INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER,
    action TEXT,
    detail TEXT,
    timestamp TEXT
);
CREATE TABLE IF NOT EXISTS banned (
    uid INTEGER PRIMARY KEY,
    reason TEXT
);
""")
db.commit()

def log_action(uid, action, detail=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        cur.execute("INSERT INTO activity_log VALUES (NULL,?,?,?,?)",
                    (uid, action, detail[:300], now))
        cur.execute("UPDATE users SET last_seen=?, total_actions=total_actions+1 WHERE uid=?",
                    (now, uid))
        db.commit()

def register_user(message):
    uid = message.from_user.id
    name = message.from_user.first_name or ""
    uname = message.from_user.username or ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        cur.execute("""INSERT OR IGNORE INTO users
                       VALUES (?,?,?,?,?,0)""", (uid, name, uname, now, now))
        db.commit()

def is_banned(uid):
    cur.execute("SELECT 1 FROM banned WHERE uid=?", (uid,))
    return cur.fetchone() is not None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GITHUB STATE BACKUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bot_start_time = datetime.now()
restart_count = 0

def gh_headers():
    return {"Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json"}

def save_to_github():
    if not GH_TOKEN: return
    try:
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM activity_log")
        total_actions = cur.fetchone()[0]

        payload_data = {
            "last_save": datetime.now().isoformat(),
            "restart_count": restart_count,
            "total_users": total_users,
            "total_actions": total_actions,
            "bot_start": bot_start_time.isoformat()
        }
        content = base64.b64encode(json.dumps(payload_data, indent=2).encode()).decode()
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE}"
        sha = None
        r = requests.get(url, headers=gh_headers(), timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
        payload = {"message": f"Auto save {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                   "content": content}
        if sha: payload["sha"] = sha
        requests.put(url, headers=gh_headers(), json=payload, timeout=15)
        print("[GitHub] State saved ✓")
    except Exception as e:
        print(f"[GitHub] Error: {e}")

def auto_save_loop():
    while True:
        time.sleep(300)
        save_to_github()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_ping():
    results = {}
    for name, host, port in [
        ("Telegram API", "api.telegram.org", 443),
        ("Telegram DC", "149.154.167.220", 443),
        ("Internet", "8.8.8.8", 53),
    ]:
        try:
            s = time.time()
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            results[name] = round((time.time() - s) * 1000, 1)
        except:
            results[name] = None
    return results

def fmt_ping(ms):
    if ms is None: return "❌ Timeout"
    if ms < 100: return f"🟢 {ms}ms"
    if ms < 300: return f"🟡 {ms}ms"
    return f"🔴 {ms}ms"

def get_uptime():
    d = datetime.now() - bot_start_time
    h, r = divmod(d.seconds, 3600)
    m, s = divmod(r, 60)
    return f"{d.days}d {h}h {m}m {s}s"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 1: OSINT — Username Checker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def check_username(username):
    platforms = {
        "GitHub": f"https://github.com/{username}",
        "Instagram": f"https://www.instagram.com/{username}/",
        "TikTok": f"https://www.tiktok.com/@{username}",
        "Twitter/X": f"https://twitter.com/{username}",
        "Reddit": f"https://www.reddit.com/user/{username}",
        "Telegram": f"https://t.me/{username}",
        "Pinterest": f"https://www.pinterest.com/{username}/",
        "Linktree": f"https://linktr.ee/{username}",
    }
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    for platform, url in platforms.items():
        try:
            r = requests.get(url, headers=headers, timeout=6, allow_redirects=True)
            if r.status_code == 200:
                results[platform] = ("✅ Found", url)
            elif r.status_code == 404:
                results[platform] = ("❌ Not found", url)
            else:
                results[platform] = (f"⚠️ {r.status_code}", url)
        except:
            results[platform] = ("⏱ Timeout", url)
    return results

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 2: Link Analyzer — Redirect chain + unshorten
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def analyze_link(url):
    if not url.startswith("http"):
        url = "https://" + url
    headers = {"User-Agent": "Mozilla/5.0"}
    chain = []
    try:
        r = requests.get(url, headers=headers, timeout=10,
                         allow_redirects=True, stream=True)
        for resp in r.history:
            chain.append(resp.url)
        chain.append(r.url)
        final = r.url
        parsed = urlparse(final)
        domain = parsed.netloc
        return {
            "chain": chain,
            "final": final,
            "domain": domain,
            "status": r.status_code,
            "hops": len(chain) - 1
        }
    except Exception as e:
        return {"error": str(e)}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 3: Hash Checker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def hash_text(text):
    t = text.encode()
    return {
        "MD5": hashlib.md5(t).hexdigest(),
        "SHA1": hashlib.sha1(t).hexdigest(),
        "SHA256": hashlib.sha256(t).hexdigest(),
        "SHA512": hashlib.sha512(t).hexdigest(),
    }

def hash_file(file_bytes):
    return {
        "MD5": hashlib.md5(file_bytes).hexdigest(),
        "SHA1": hashlib.sha1(file_bytes).hexdigest(),
        "SHA256": hashlib.sha256(file_bytes).hexdigest(),
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 4: Breach Checker (HaveIBeenPwned — free)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def check_breach(email):
    try:
        headers = {
            "User-Agent": "BDCyberBot-Educational",
            "hibp-api-key": os.environ.get("HIBP_KEY", "")
        }
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            breaches = r.json()
            return {"found": True, "count": len(breaches),
                    "names": [b["Name"] for b in breaches[:5]]}
        elif r.status_code == 404:
            return {"found": False}
        elif r.status_code == 401:
            return {"error": "HIBP API key লাগবে। hibp.com থেকে নাও।"}
        else:
            return {"error": f"Status {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN MENU
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main_menu(uid):
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("🔍 Username OSINT", callback_data="osint"),
        types.InlineKeyboardButton("🔗 Link Analyzer", callback_data="linkcheck"),
        types.InlineKeyboardButton("🔐 Hash Generator", callback_data="hashgen"),
        types.InlineKeyboardButton("📧 Breach Check", callback_data="breach"),
        types.InlineKeyboardButton("📡 Ping", callback_data="ping"),
        types.InlineKeyboardButton("📊 My Stats", callback_data="mystats"),
    )
    if uid == OWNER_ID:
        mk.add(types.InlineKeyboardButton("🔧 Admin Panel", callback_data="admin"))
    return mk

@bot.message_handler(commands=['start'])
def cmd_start(message):
    if is_banned(message.from_user.id):
        bot.reply_to(message, "⛔ Banned.")
        return
    register_user(message)
    log_action(message.from_user.id, "START", "/start command")
    bot.send_message(message.chat.id,
        f"👋 <b>BDCyber Intelligence Bot</b>\n\n"
        f"🔍 OSINT Username Lookup\n"
        f"🔗 Link Analyzer & Unshortener\n"
        f"🔐 Hash Generator (MD5/SHA)\n"
        f"📧 Data Breach Checker\n"
        f"📡 Real Server Ping\n\n"
        f"⚠️ <i>Educational purpose only</i>",
        reply_markup=main_menu(message.from_user.id)
    )

@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    register_user(message)
    log_action(message.from_user.id, "PING", "")
    msg = bot.reply_to(message, "📡 Pinging...")
    start = time.time()
    p = get_ping()
    latency = round((time.time() - start) * 1000, 1)
    bot.edit_message_text(
        f"<b>📡 PING RESULTS</b>\n\n"
        f"🤖 Bot: <b>{latency}ms</b>\n"
        f"📨 Telegram API: <b>{fmt_ping(p['Telegram API'])}</b>\n"
        f"🏢 Telegram DC: <b>{fmt_ping(p['Telegram DC'])}</b>\n"
        f"🌐 Internet: <b>{fmt_ping(p['Internet'])}</b>\n\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}",
        message.chat.id, msg.message_id
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATE MACHINE — user কোন mode এ আছে
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
user_states = {}  # {uid: "osint" / "link" / "hash" / "breach"}

@bot.callback_query_handler(func=lambda c: True)
def handle_callbacks(call):
    uid = call.from_user.id
    if is_banned(uid):
        return
    register_user(call.message)
    bot.answer_callback_query(call.id)
    data = call.data
    log_action(uid, "BUTTON", data)

    if data == "osint":
        user_states[uid] = "osint"
        bot.send_message(uid,
            "🔍 <b>Username OSINT</b>\n\n"
            "যে username check করতে চাও সেটা লেখো:\n"
            "Example: <code>ahad123</code>"
        )

    elif data == "linkcheck":
        user_states[uid] = "link"
        bot.send_message(uid,
            "🔗 <b>Link Analyzer</b>\n\n"
            "যে link analyze করতে চাও paste করো:\n"
            "Example: <code>https://bit.ly/example</code>"
        )

    elif data == "hashgen":
        user_states[uid] = "hash"
        bot.send_message(uid,
            "🔐 <b>Hash Generator</b>\n\n"
            "যে text এর hash বের করতে চাও লেখো:\n"
            "Example: <code>password123</code>\n\n"
            "অথবা যেকোনো file পাঠাও → file hash দেবো।"
        )

    elif data == "breach":
        user_states[uid] = "breach"
        bot.send_message(uid,
            "📧 <b>Breach Checker</b>\n\n"
            "Email address লেখো:\n"
            "Example: <code>test@gmail.com</code>\n\n"
            "⚠️ <i>HaveIBeenPwned database check করবো।</i>"
        )

    elif data == "ping":
        p = get_ping()
        bot.send_message(uid,
            f"<b>📡 PING</b>\n"
            f"Telegram API: {fmt_ping(p['Telegram API'])}\n"
            f"Telegram DC: {fmt_ping(p['Telegram DC'])}\n"
            f"Internet: {fmt_ping(p['Internet'])}"
        )

    elif data == "mystats":
        cur.execute("SELECT total_actions, first_seen, last_seen FROM users WHERE uid=?", (uid,))
        row = cur.fetchone()
        cur.execute("SELECT action, detail, timestamp FROM activity_log WHERE uid=? ORDER BY id DESC LIMIT 5", (uid,))
        logs = cur.fetchall()
        txt = f"<b>📊 তোমার Stats</b>\n\n"
        if row:
            txt += f"Total actions: <b>{row[0]}</b>\n"
            txt += f"First seen: <b>{row[1][:10]}</b>\n"
            txt += f"Last seen: <b>{row[2][:10]}</b>\n\n"
        txt += "<b>Recent activity:</b>\n"
        for action, detail, ts in logs:
            txt += f"• {action} — <i>{ts[11:16]}</i>\n"
        bot.send_message(uid, txt)

    elif data == "admin" and uid == OWNER_ID:
        show_admin_panel(uid)

    elif data == "admin_users":
        cur.execute("SELECT uid, name, username, total_actions, last_seen FROM users ORDER BY total_actions DESC LIMIT 15")
        rows = cur.fetchall()
        txt = "<b>👥 TOP USERS:</b>\n\n"
        for r in rows:
            txt += f"• <code>{r[0]}</code> {r[1]} (@{r[2]}) — {r[3]} actions\n"
        bot.send_message(uid, txt)

    elif data == "admin_logs":
        cur.execute("SELECT uid, action, detail, timestamp FROM activity_log ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        txt = "<b>📋 RECENT ACTIVITY LOG:</b>\n\n"
        for r in rows:
            txt += f"<code>{r[0]}</code> | {r[1]} | {r[3][11:16]}\n"
            if r[2]: txt += f"   └ {r[2][:50]}\n"
        bot.send_message(uid, txt)

    elif data == "admin_stats":
        cur.execute("SELECT COUNT(*) FROM users")
        tu = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM activity_log")
        ta = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM banned")
        tb = cur.fetchone()[0]
        bot.send_message(uid,
            f"<b>📊 FULL STATS</b>\n\n"
            f"👥 Total Users: <b>{tu}</b>\n"
            f"📋 Total Actions: <b>{ta}</b>\n"
            f"⛔ Banned: <b>{tb}</b>\n"
            f"⏱ Uptime: <b>{get_uptime()}</b>\n"
            f"🔄 Restarts: <b>{restart_count}</b>"
        )

    elif data.startswith("user_detail_"):
        target = int(data.split("_")[2])
        cur.execute("SELECT uid, name, username, total_actions, first_seen, last_seen FROM users WHERE uid=?", (target,))
        u = cur.fetchone()
        cur.execute("SELECT action, detail, timestamp FROM activity_log WHERE uid=? ORDER BY id DESC LIMIT 20", (target,))
        logs = cur.fetchall()
        txt = (
            f"<b>👤 USER DETAIL</b>\n\n"
            f"ID: <code>{u[0]}</code>\n"
            f"Name: {u[1]}\n"
            f"Username: @{u[2]}\n"
            f"Actions: <b>{u[3]}</b>\n"
            f"First: {u[4][:16]}\n"
            f"Last: {u[5][:16]}\n\n"
            f"<b>📋 Activity Log (last 20):</b>\n"
        )
        for action, detail, ts in logs:
            txt += f"• [{ts[11:16]}] <b>{action}</b>"
            if detail: txt += f" — {detail[:60]}"
            txt += "\n"
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton(f"⛔ Ban این User", callback_data=f"ban_{target}"))
        bot.send_message(uid, txt, reply_markup=mk)

    elif data.startswith("ban_") and uid == OWNER_ID:
        target = int(data.split("_")[1])
        with db_lock:
            cur.execute("INSERT OR REPLACE INTO banned VALUES (?,?)", (target, "Admin ban"))
            db.commit()
        bot.send_message(uid, f"⛔ User <code>{target}</code> banned!")

    elif data == "admin_save":
        save_to_github()
        bot.send_message(uid, "✅ Saved to GitHub!")

def show_admin_panel(uid):
    cur.execute("SELECT COUNT(*) FROM users")
    tu = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM activity_log")
    ta = cur.fetchone()[0]
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("👥 User List", callback_data="admin_users"),
        types.InlineKeyboardButton("📋 Activity Log", callback_data="admin_logs"),
        types.InlineKeyboardButton("📊 Full Stats", callback_data="admin_stats"),
        types.InlineKeyboardButton("💾 Save Now", callback_data="admin_save"),
        types.InlineKeyboardButton("📡 Ping", callback_data="ping"),
    )
    bot.send_message(uid,
        f"<b>🔧 ADMIN PANEL</b>\n\n"
        f"👥 Users: <b>{tu}</b>\n"
        f"📋 Actions: <b>{ta}</b>\n"
        f"⏱ Uptime: <b>{get_uptime()}</b>",
        reply_markup=mk
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEXT HANDLER — state machine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    if is_banned(uid): return
    register_user(message)
    text = message.text.strip()

    if text.startswith("/"): return

    state = user_states.get(uid)

    if state == "osint":
        user_states.pop(uid)
        log_action(uid, "OSINT", text)
        msg = bot.reply_to(message, f"🔍 Checking <b>{text}</b> on 8 platforms...")
        results = check_username(text)
        txt = f"<b>🔍 OSINT: @{text}</b>\n\n"
        for platform, (status, url) in results.items():
            txt += f"{status} <b>{platform}</b>\n"
            if "Found" in status:
                txt += f"   └ <a href='{url}'>{url}</a>\n"
        bot.edit_message_text(txt, message.chat.id, msg.message_id,
                              disable_web_page_preview=True)

    elif state == "link":
        user_states.pop(uid)
        log_action(uid, "LINK_ANALYZE", text)
        msg = bot.reply_to(message, "🔗 Analyzing link...")
        result = analyze_link(text)
        if "error" in result:
            bot.edit_message_text(f"❌ Error: {result['error']}", message.chat.id, msg.message_id)
            return
        txt = f"<b>🔗 LINK ANALYSIS</b>\n\n"
        txt += f"🏁 Final URL:\n<code>{result['final']}</code>\n\n"
        txt += f"🌐 Domain: <b>{result['domain']}</b>\n"
        txt += f"🔀 Redirects: <b>{result['hops']}</b>\n"
        txt += f"📶 Status: <b>{result['status']}</b>\n"
        if len(result['chain']) > 1:
            txt += f"\n<b>Redirect chain:</b>\n"
            for i, u in enumerate(result['chain']):
                txt += f"{i+1}. <code>{u[:60]}</code>\n"
        bot.edit_message_text(txt, message.chat.id, msg.message_id)

    elif state == "hash":
        user_states.pop(uid)
        log_action(uid, "HASH", text[:50])
        hashes = hash_text(text)
        txt = f"<b>🔐 HASH RESULTS</b>\n\n"
        txt += f"Input: <code>{text[:50]}</code>\n\n"
        for algo, val in hashes.items():
            txt += f"<b>{algo}:</b>\n<code>{val}</code>\n\n"
        bot.reply_to(message, txt)

    elif state == "breach":
        user_states.pop(uid)
        log_action(uid, "BREACH_CHECK", text)
        msg = bot.reply_to(message, "📧 Checking breach database...")
        result = check_breach(text)
        if "error" in result:
            bot.edit_message_text(f"⚠️ {result['error']}", message.chat.id, msg.message_id)
        elif result["found"]:
            names = ", ".join(result["names"])
            bot.edit_message_text(
                f"<b>📧 BREACH RESULT</b>\n\n"
                f"Email: <code>{text}</code>\n"
                f"⚠️ <b>Found in {result['count']} breach(es)!</b>\n\n"
                f"Sites: {names}{'...' if result['count'] > 5 else ''}",
                message.chat.id, msg.message_id
            )
        else:
            bot.edit_message_text(
                f"<b>📧 BREACH RESULT</b>\n\n"
                f"Email: <code>{text}</code>\n"
                f"✅ <b>No breaches found!</b>",
                message.chat.id, msg.message_id
            )

    else:
        log_action(uid, "MSG", text[:100])
        bot.reply_to(message, "💡 /start দিয়ে menu দেখো।",
                     reply_markup=main_menu(uid))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILE HANDLER — hash file
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.message_handler(content_types=['document'])
def handle_file(message):
    uid = message.from_user.id
    if is_banned(uid): return
    register_user(message)
    log_action(uid, "FILE_UPLOAD", message.document.file_name)

    state = user_states.get(uid)
    if state == "hash":
        user_states.pop(uid)
        msg = bot.reply_to(message, "🔐 Hashing file...")
        try:
            fi = bot.get_file(message.document.file_id)
            raw = bot.download_file(fi.file_path)
            hashes = hash_file(raw)
            txt = f"<b>🔐 FILE HASH</b>\n\n"
            txt += f"File: <code>{message.document.file_name}</code>\n"
            txt += f"Size: {round(len(raw)/1024, 1)} KB\n\n"
            for algo, val in hashes.items():
                txt += f"<b>{algo}:</b>\n<code>{val}</code>\n\n"
            bot.edit_message_text(txt, message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {e}", message.chat.id, msg.message_id)
    else:
        bot.reply_to(message, "📁 File পেলাম। Hash করতে চাইলে আগে 🔐 Hash বাটন চাপো।")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADMIN COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    show_admin_panel(message.from_user.id)

@bot.message_handler(commands=['user'])
def cmd_user_detail(message):
    if message.from_user.id != OWNER_ID: return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /user [user_id]")
        return
    target = int(parts[1])
    cur.execute("SELECT uid, name, username, total_actions, first_seen, last_seen FROM users WHERE uid=?", (target,))
    u = cur.fetchone()
    if not u:
        bot.reply_to(message, "User not found!")
        return
    cur.execute("SELECT action, detail, timestamp FROM activity_log WHERE uid=? ORDER BY id DESC LIMIT 30", (target,))
    logs = cur.fetchall()
    txt = (
        f"<b>👤 USER: {u[1]}</b>\n"
        f"ID: <code>{u[0]}</code>\n"
        f"Username: @{u[2]}\n"
        f"Actions: <b>{u[3]}</b>\n"
        f"First: {u[4][:16]}\n"
        f"Last: {u[5][:16]}\n\n"
        f"<b>📋 Full Log:</b>\n"
    )
    for action, detail, ts in logs:
        txt += f"[{ts[11:16]}] <b>{action}</b>"
        if detail: txt += f" — {detail[:80]}"
        txt += "\n"
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("⛔ Ban", callback_data=f"ban_{target}"))
    bot.reply_to(message, txt, reply_markup=mk)

@bot.message_handler(commands=['ban'])
def cmd_ban(message):
    if message.from_user.id != OWNER_ID: return
    parts = message.text.split()
    if len(parts) < 2: return
    target = int(parts[1])
    with db_lock:
        cur.execute("INSERT OR REPLACE INTO banned VALUES (?,?)", (target, "Manual ban"))
        db.commit()
    bot.reply_to(message, f"⛔ Banned <code>{target}</code>!")

@bot.message_handler(commands=['unban'])
def cmd_unban(message):
    if message.from_user.id != OWNER_ID: return
    parts = message.text.split()
    if len(parts) < 2: return
    with db_lock:
        cur.execute("DELETE FROM banned WHERE uid=?", (int(parts[1]),))
        db.commit()
    bot.reply_to(message, f"✅ Unbanned!")

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if message.from_user.id != OWNER_ID: return
    parts = message.text.split(None, 1)
    if len(parts) < 2: return
    cur.execute("SELECT uid FROM users")
    users = cur.fetchall()
    sent = failed = 0
    for (uid,) in users:
        try:
            bot.send_message(uid, f"📢 <b>Broadcast:</b>\n\n{parts[1]}")
            sent += 1
            time.sleep(0.05)
        except: failed += 1
    bot.reply_to(message, f"✅ Sent: {sent} | ❌ Failed: {failed}")

@bot.message_handler(commands=['save'])
def cmd_save(message):
    if message.from_user.id != OWNER_ID: return
    save_to_github()
    bot.reply_to(message, "✅ Saved to GitHub!")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KEEP ALIVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class PingServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        cur.execute("SELECT COUNT(*) FROM users")
        u = cur.fetchone()[0]
        self.wfile.write(f"BDCyber Bot | Uptime: {get_uptime()} | Users: {u}".encode())
    def log_message(self, *args): pass

def keep_alive():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), PingServer).serve_forever()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LAUNCH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  BDCyber Intelligence Bot v1.0")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=auto_save_loop, daemon=True).start()
    print("[Bot] Started!")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
