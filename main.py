"""
╔══════════════════════════════════════════════════════════════╗
║          BDCyber Intelligence Bot v2.0                      ║
║  OSINT | Link | Hash | IP | DNS | SSL | Telegram Lookup     ║
║  Full Activity Logging | BD Timezone | Admin Dashboard      ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, hashlib, base64, socket, threading
import requests, sqlite3
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import telebot
from telebot import types

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN")
OWNER_ID  = int(os.environ.get("OWNER_ID", "123456789"))
GH_TOKEN  = os.environ.get("GH_TOKEN", "")
GH_REPO   = os.environ.get("GH_REPO", "user/repo")
GH_FILE   = "bdcyber_state.json"

BD_TZ = timezone(timedelta(hours=6))

def now_bd():
    return datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")

def now_bd_short():
    return datetime.now(BD_TZ).strftime("%d/%m %H:%M")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
bot_start_time = datetime.now(BD_TZ)
restart_count = [0]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
db = sqlite3.connect("bdcyber.db", check_same_thread=False)
db_lock = threading.Lock()
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
    uid INTEGER PRIMARY KEY,
    name TEXT, username TEXT,
    first_seen TEXT, last_seen TEXT,
    total_actions INTEGER DEFAULT 0,
    total_buttons INTEGER DEFAULT 0,
    total_inputs INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER, action_type TEXT,
    action TEXT, detail TEXT, timestamp TEXT
);
CREATE TABLE IF NOT EXISTS banned (
    uid INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT
);
""")
db.commit()

def bd_now(): return datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")

def register_user(u):
    now = bd_now()
    with db_lock:
        cur.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,0,0,0)",
                    (u.id, u.first_name or "", u.username or "", now, now))
        cur.execute("UPDATE users SET last_seen=?, name=?, username=? WHERE uid=?",
                    (now, u.first_name or "", u.username or "", u.id))
        db.commit()

def log_action(uid, action_type, action, detail=""):
    with db_lock:
        cur.execute("INSERT INTO activity_log VALUES (NULL,?,?,?,?,?)",
                    (uid, action_type, action, str(detail)[:400], bd_now()))
        if action_type == "BUTTON":
            cur.execute("UPDATE users SET total_actions=total_actions+1, total_buttons=total_buttons+1, last_seen=? WHERE uid=?", (bd_now(), uid))
        elif action_type == "INPUT":
            cur.execute("UPDATE users SET total_actions=total_actions+1, total_inputs=total_inputs+1, last_seen=? WHERE uid=?", (bd_now(), uid))
        else:
            cur.execute("UPDATE users SET total_actions=total_actions+1, last_seen=? WHERE uid=?", (bd_now(), uid))
        db.commit()

def is_banned(uid):
    cur.execute("SELECT 1 FROM banned WHERE uid=?", (uid,))
    return cur.fetchone() is not None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GITHUB BACKUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def save_to_github():
    if not GH_TOKEN: return
    try:
        cur.execute("SELECT COUNT(*) FROM users")
        tu = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM activity_log")
        ta = cur.fetchone()[0]
        data = {"last_save": bd_now(), "restart_count": restart_count[0],
                "total_users": tu, "total_actions": ta}
        content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE}"
        headers = {"Authorization": f"token {GH_TOKEN}",
                   "Accept": "application/vnd.github.v3+json"}
        sha = None
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
        payload = {"message": f"Auto save {bd_now()}", "content": content}
        if sha: payload["sha"] = sha
        requests.put(url, headers=headers, json=payload, timeout=15)
        print(f"[GitHub] Saved ✓")
    except Exception as e:
        print(f"[GitHub] Error: {e}")

def load_from_github():
    if not GH_TOKEN: return
    try:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE}"
        headers = {"Authorization": f"token {GH_TOKEN}"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = json.loads(base64.b64decode(r.json()["content"]).decode())
            restart_count[0] = data.get("restart_count", 0) + 1
            print(f"[GitHub] Loaded. Restart #{restart_count[0]}")
    except Exception as e:
        print(f"[GitHub] Load error: {e}")

def auto_save_loop():
    while True:
        time.sleep(300)
        save_to_github()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UPTIME + PING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_uptime():
    d = datetime.now(BD_TZ) - bot_start_time
    h, r = divmod(d.seconds, 3600)
    m, s = divmod(r, 60)
    return f"{d.days}d {h}h {m}m {s}s"

def get_ping():
    res = {}
    for name, host, port in [
        ("Telegram API", "api.telegram.org", 443),
        ("Telegram DC2", "149.154.167.220", 443),
        ("Google DNS", "8.8.8.8", 53),
        ("Cloudflare", "1.1.1.1", 53),
    ]:
        try:
            s = time.time()
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            res[name] = round((time.time() - s) * 1000, 1)
        except: res[name] = None
    return res

def fp(ms):
    if ms is None: return "❌"
    if ms < 80: return f"🟢 {ms}ms"
    if ms < 200: return f"🟡 {ms}ms"
    return f"🔴 {ms}ms"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 1: DEEP OSINT USERNAME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLATFORMS = {
    "GitHub":       ("https://github.com/{}", [200]),
    "Instagram":    ("https://www.instagram.com/{}/", [200]),
    "TikTok":       ("https://www.tiktok.com/@{}", [200]),
    "Twitter/X":    ("https://twitter.com/{}", [200]),
    "Reddit":       ("https://www.reddit.com/user/{}", [200]),
    "Telegram":     ("https://t.me/{}", [200]),
    "Pinterest":    ("https://www.pinterest.com/{}/", [200]),
    "Linktree":     ("https://linktr.ee/{}", [200]),
    "Pastebin":     ("https://pastebin.com/u/{}", [200]),
    "Keybase":      ("https://keybase.io/{}", [200]),
    "DevTo":        ("https://dev.to/{}", [200]),
    "HackerNews":   ("https://news.ycombinator.com/user?id={}", [200]),
    "GitLab":       ("https://gitlab.com/{}", [200]),
    "Steam":        ("https://steamcommunity.com/id/{}", [200]),
    "Medium":       ("https://medium.com/@{}", [200]),
    "Twitch":       ("https://www.twitch.tv/{}", [200]),
}

def check_username(username):
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    def check_one(name, url_tpl, ok_codes):
        url = url_tpl.format(username)
        try:
            r = requests.get(url, headers=headers, timeout=7, allow_redirects=True)
            if r.status_code in ok_codes:
                results[name] = ("✅", url)
            elif r.status_code == 404:
                results[name] = ("❌", url)
            else:
                results[name] = (f"⚠️{r.status_code}", url)
        except: results[name] = ("⏱", "")
    threads = []
    for name, (tpl, codes) in PLATFORMS.items():
        t = threading.Thread(target=check_one, args=(name, tpl, codes))
        t.start(); threads.append(t)
    for t in threads: t.join(timeout=10)
    return results

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 2: TELEGRAM USER/CHANNEL LOOKUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def telegram_lookup(username):
    username = username.lstrip("@")
    result = {}
    try:
        r = requests.get(f"https://t.me/{username}", timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
        result["url"] = f"https://t.me/{username}"
        result["status"] = "✅ Exists" if r.status_code == 200 else "❌ Not found"
        # Parse title
        if '<meta property="og:title" content="' in html:
            title = html.split('<meta property="og:title" content="')[1].split('"')[0]
            result["title"] = title
        # Parse description
        if '<meta property="og:description" content="' in html:
            desc = html.split('<meta property="og:description" content="')[1].split('"')[0]
            result["description"] = desc[:200]
        # Parse image
        if '<meta property="og:image" content="' in html:
            img = html.split('<meta property="og:image" content="')[1].split('"')[0]
            result["photo"] = img
        # Members count
        if 'members' in html.lower():
            for line in html.split('\n'):
                if 'members' in line.lower() and any(c.isdigit() for c in line):
                    import re
                    nums = re.findall(r'[\d,]+', line)
                    if nums: result["members"] = nums[0]
                    break
        result["type"] = "Channel/Group" if any(x in html for x in ['subscribers', 'members']) else "User"
    except Exception as e:
        result["error"] = str(e)
    return result

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 3: IP INFO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ip_lookup(ip):
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,zip,lat,lon,timezone,isp,org,as,mobile,proxy,hosting,query",
                         timeout=8)
        return r.json()
    except Exception as e:
        return {"status": "fail", "message": str(e)}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 4: DNS LOOKUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def dns_lookup(domain):
    domain = domain.replace("https://","").replace("http://","").split("/")[0]
    result = {"domain": domain}
    try:
        ip = socket.gethostbyname(domain)
        result["ip"] = ip
        # Reverse DNS
        try:
            reverse = socket.gethostbyaddr(ip)[0]
            result["reverse"] = reverse
        except: result["reverse"] = "N/A"
        # IP info
        ip_info = ip_lookup(ip)
        if ip_info.get("status") == "success":
            result["country"] = ip_info.get("country", "")
            result["isp"] = ip_info.get("isp", "")
            result["org"] = ip_info.get("org", "")
            result["hosting"] = ip_info.get("hosting", False)
            result["proxy"] = ip_info.get("proxy", False)
    except Exception as e:
        result["error"] = str(e)
    return result

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 5: SSL CHECKER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ssl_check(domain):
    domain = domain.replace("https://","").replace("http://","").split("/")[0]
    try:
        import ssl
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(8)
            s.connect((domain, 443))
            cert = s.getpeercert()
            expire = cert.get("notAfter", "")
            issuer = dict(x[0] for x in cert.get("issuer", []))
            subject = dict(x[0] for x in cert.get("subject", []))
            return {
                "valid": True,
                "domain": subject.get("commonName", domain),
                "issuer": issuer.get("organizationName", "Unknown"),
                "expires": expire,
                "san": [x[1] for x in cert.get("subjectAltName", [])][:5]
            }
    except ssl.SSLCertVerificationError:
        return {"valid": False, "error": "Certificate invalid/expired"}
    except Exception as e:
        return {"valid": False, "error": str(e)}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 6: LINK ANALYZER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def analyze_link(url):
    if not url.startswith("http"): url = "https://" + url
    try:
        r = requests.get(url, timeout=10, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        chain = [h.url for h in r.history] + [r.url]
        parsed = urlparse(r.url)
        return {"chain": chain, "final": r.url, "domain": parsed.netloc,
                "status": r.status_code, "hops": len(chain)-1}
    except Exception as e:
        return {"error": str(e)}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 7: HASH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def make_hash(data: bytes):
    return {
        "MD5":    hashlib.md5(data).hexdigest(),
        "SHA1":   hashlib.sha1(data).hexdigest(),
        "SHA256": hashlib.sha256(data).hexdigest(),
        "SHA512": hashlib.sha512(data).hexdigest(),
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENGINE 8: EMAIL BREACH (no-key method)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def check_breach(email):
    try:
        # leakcheck.io free tier
        r = requests.get(
            f"https://leakcheck.io/api/public?check={email}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("found", 0) > 0:
                return {"found": True, "count": data["found"],
                        "sources": data.get("sources", [])[:5]}
            return {"found": False}
        return {"error": f"Status {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MENUS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main_menu(uid):
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("🔍 Username OSINT", callback_data="osint"),
        types.InlineKeyboardButton("📱 Telegram Lookup", callback_data="tglookup"),
        types.InlineKeyboardButton("🌐 IP Info", callback_data="ipinfo"),
        types.InlineKeyboardButton("🔗 Link Analyzer", callback_data="linkcheck"),
        types.InlineKeyboardButton("🔎 DNS Lookup", callback_data="dnslookup"),
        types.InlineKeyboardButton("🔒 SSL Checker", callback_data="sslcheck"),
        types.InlineKeyboardButton("🔐 Hash Generator", callback_data="hashgen"),
        types.InlineKeyboardButton("📧 Breach Check", callback_data="breach"),
        types.InlineKeyboardButton("📡 Ping", callback_data="ping"),
    )
    if uid == OWNER_ID:
        mk.add(types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin"))
    return mk

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATE MACHINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
user_states = {}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    if is_banned(uid):
        bot.reply_to(message, "⛔ You are banned.")
        return
    register_user(message.from_user)
    log_action(uid, "CMD", "/start")
    bot.send_message(uid,
        "┌─────────────────────────────┐\n"
        "│  <b>BDCyber Intelligence v2.0</b>  │\n"
        "└─────────────────────────────┘\n\n"
        "🔍 Username OSINT (16 platforms)\n"
        "📱 Telegram User/Channel Lookup\n"
        "🌐 IP Address Intelligence\n"
        "🔗 Link Analyzer & Unshortener\n"
        "🔎 DNS Lookup & Reverse DNS\n"
        "🔒 SSL Certificate Checker\n"
        "🔐 Hash Generator (MD5/SHA)\n"
        "📧 Email Breach Detection\n"
        "📡 Real Server Ping\n\n"
        f"🕐 BD Time: <b>{now_bd_short()}</b>\n"
        "⚠️ <i>Educational purpose only</i>",
        reply_markup=main_menu(uid)
    )

@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    register_user(message.from_user)
    log_action(message.from_user.id, "CMD", "/ping")
    msg = bot.reply_to(message, "📡 Testing connections...")
    s = time.time()
    p = get_ping()
    lat = round((time.time()-s)*1000, 1)
    bot.edit_message_text(
        f"<b>📡 PING RESULTS</b>\n"
        f"{'─'*28}\n"
        f"🤖 Bot Response:   <b>{lat}ms</b>\n"
        f"📨 Telegram API:   <b>{fp(p['Telegram API'])}</b>\n"
        f"🏢 Telegram DC2:   <b>{fp(p['Telegram DC2'])}</b>\n"
        f"🌐 Google DNS:     <b>{fp(p['Google DNS'])}</b>\n"
        f"☁️ Cloudflare:     <b>{fp(p['Cloudflare'])}</b>\n"
        f"{'─'*28}\n"
        f"🕐 <b>{now_bd_short()}</b> BD",
        message.chat.id, msg.message_id
    )

@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if message.from_user.id != OWNER_ID: return
    show_admin(message.from_user.id)

@bot.message_handler(commands=['user'])
def cmd_user(message):
    if message.from_user.id != OWNER_ID: return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /user [uid]")
        return
    show_user_detail(message.from_user.id, int(parts[1]))

@bot.message_handler(commands=['ban'])
def cmd_ban(message):
    if message.from_user.id != OWNER_ID: return
    parts = message.text.split(None, 2)
    if len(parts) < 2: return
    target = int(parts[1])
    reason = parts[2] if len(parts) > 2 else "Admin ban"
    with db_lock:
        cur.execute("INSERT OR REPLACE INTO banned VALUES (?,?,?)", (target, reason, bd_now()))
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
    bot.reply_to(message, "✅ Unbanned!")

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
            sent += 1; time.sleep(0.05)
        except: failed += 1
    bot.reply_to(message, f"✅ Sent: {sent} | ❌ Failed: {failed}")

@bot.message_handler(commands=['save'])
def cmd_save(message):
    if message.from_user.id != OWNER_ID: return
    save_to_github()
    bot.reply_to(message, "✅ Saved to GitHub!")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADMIN PANEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def show_admin(uid):
    cur.execute("SELECT COUNT(*) FROM users")
    tu = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM activity_log")
    ta = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM banned")
    tb = cur.fetchone()[0]
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("👥 All Users", callback_data="adm_users"),
        types.InlineKeyboardButton("📋 Activity Log", callback_data="adm_log_0"),
        types.InlineKeyboardButton("📊 Full Stats", callback_data="adm_stats"),
        types.InlineKeyboardButton("⛔ Banned List", callback_data="adm_banned"),
        types.InlineKeyboardButton("💾 Save GitHub", callback_data="adm_save"),
        types.InlineKeyboardButton("📡 Ping", callback_data="ping"),
    )
    bot.send_message(uid,
        f"┌──────────────────────────┐\n"
        f"│    <b>⚙️ ADMIN DASHBOARD</b>    │\n"
        f"└──────────────────────────┘\n\n"
        f"👥 Total Users:   <b>{tu}</b>\n"
        f"📋 Total Actions: <b>{ta}</b>\n"
        f"⛔ Banned:        <b>{tb}</b>\n"
        f"🔄 Restarts:      <b>{restart_count[0]}</b>\n"
        f"⏱ Uptime:        <b>{get_uptime()}</b>\n"
        f"🕐 BD Time:       <b>{now_bd_short()}</b>",
        reply_markup=mk
    )

def show_user_detail(admin_uid, target_uid):
    cur.execute("SELECT uid,name,username,total_actions,total_buttons,total_inputs,first_seen,last_seen FROM users WHERE uid=?", (target_uid,))
    u = cur.fetchone()
    if not u:
        bot.send_message(admin_uid, "❌ User not found!")
        return
    # ALL logs
    cur.execute("SELECT action_type, action, detail, timestamp FROM activity_log WHERE uid=? ORDER BY id ASC", (target_uid,))
    logs = cur.fetchall()

    header = (
        f"┌──────────────────────────────┐\n"
        f"│      <b>👤 USER DETAIL</b>         │\n"
        f"└──────────────────────────────┘\n\n"
        f"🆔 ID:       <code>{u[0]}</code>\n"
        f"📛 Name:     {u[1]}\n"
        f"🔗 Username: @{u[2]}\n"
        f"{'─'*30}\n"
        f"📊 Total Actions: <b>{u[3]}</b>\n"
        f"🖱 Buttons Pressed: <b>{u[4]}</b>\n"
        f"⌨️ Text Inputs: <b>{u[5]}</b>\n"
        f"📅 First Seen: <b>{u[6][:16]}</b>\n"
        f"🕐 Last Seen:  <b>{u[7][11:16]}</b> BD\n"
        f"{'─'*30}\n"
        f"<b>📋 FULL ACTIVITY LOG ({len(logs)} entries):</b>\n\n"
    )

    # Build log text — split if too long
    log_lines = ""
    for atype, action, detail, ts in logs:
        icon = "🖱" if atype=="BUTTON" else ("⌨️" if atype=="INPUT" else "⚡")
        line = f"{icon} [{ts[11:16]}] <b>{action}</b>"
        if detail and detail != action: line += f"\n   └ <i>{detail[:80]}</i>"
        log_lines += line + "\n"

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("⛔ Ban User", callback_data=f"ban_{target_uid}"),
        types.InlineKeyboardButton("🔙 Back", callback_data="admin"),
    )

    # Telegram message limit 4096 chars — split if needed
    full = header + log_lines
    if len(full) <= 4000:
        bot.send_message(admin_uid, full, reply_markup=mk)
    else:
        bot.send_message(admin_uid, header)
        # Send logs in chunks
        chunk = ""
        for line in log_lines.split("\n"):
            if len(chunk) + len(line) > 3800:
                bot.send_message(admin_uid, chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            bot.send_message(admin_uid, chunk, reply_markup=mk)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALLBACK HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    uid = call.from_user.id
    if is_banned(uid): return
    register_user(call.from_user)
    bot.answer_callback_query(call.id)
    d = call.data
    log_action(uid, "BUTTON", d)

    # Tools
    if d == "osint":
        user_states[uid] = "osint"
        bot.send_message(uid, "🔍 <b>Username OSINT</b>\n\nUsername লেখো (@ ছাড়া):\n<code>example: ahad123</code>")
    elif d == "tglookup":
        user_states[uid] = "tglookup"
        bot.send_message(uid, "📱 <b>Telegram Lookup</b>\n\nTelegram username লেখো:\n<code>example: durov</code>")
    elif d == "ipinfo":
        user_states[uid] = "ipinfo"
        bot.send_message(uid, "🌐 <b>IP Info</b>\n\nIP address লেখো:\n<code>example: 8.8.8.8</code>")
    elif d == "linkcheck":
        user_states[uid] = "link"
        bot.send_message(uid, "🔗 <b>Link Analyzer</b>\n\nURL paste করো:\n<code>example: https://bit.ly/xyz</code>")
    elif d == "dnslookup":
        user_states[uid] = "dns"
        bot.send_message(uid, "🔎 <b>DNS Lookup</b>\n\nDomain লেখো:\n<code>example: google.com</code>")
    elif d == "sslcheck":
        user_states[uid] = "ssl"
        bot.send_message(uid, "🔒 <b>SSL Checker</b>\n\nDomain লেখো:\n<code>example: google.com</code>")
    elif d == "hashgen":
        user_states[uid] = "hash"
        bot.send_message(uid, "🔐 <b>Hash Generator</b>\n\nText লেখো অথবা file পাঠাও:")
    elif d == "breach":
        user_states[uid] = "breach"
        bot.send_message(uid, "📧 <b>Breach Check</b>\n\nEmail address লেখো:\n<code>example: test@gmail.com</code>")
    elif d == "ping":
        p = get_ping()
        s = time.time()
        lat = round((time.time()-s)*1000+50, 1)
        bot.send_message(uid,
            f"<b>📡 PING</b>\n{'─'*24}\n"
            f"Telegram API: {fp(p['Telegram API'])}\n"
            f"Telegram DC2: {fp(p['Telegram DC2'])}\n"
            f"Google DNS:   {fp(p['Google DNS'])}\n"
            f"Cloudflare:   {fp(p['Cloudflare'])}\n"
            f"{'─'*24}\n🕐 {now_bd_short()} BD"
        )

    # Admin
    elif d == "admin" and uid == OWNER_ID:
        show_admin(uid)
    elif d == "adm_stats" and uid == OWNER_ID:
        cur.execute("SELECT COUNT(*) FROM users"); tu = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM activity_log"); ta = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM banned"); tb = cur.fetchone()[0]
        cur.execute("SELECT action, COUNT(*) as c FROM activity_log GROUP BY action ORDER BY c DESC LIMIT 10")
        top = cur.fetchall()
        txt = (f"<b>📊 FULL STATISTICS</b>\n{'─'*28}\n"
               f"👥 Users: <b>{tu}</b>\n"
               f"📋 Actions: <b>{ta}</b>\n"
               f"⛔ Banned: <b>{tb}</b>\n"
               f"🔄 Restarts: <b>{restart_count[0]}</b>\n"
               f"⏱ Uptime: <b>{get_uptime()}</b>\n"
               f"{'─'*28}\n<b>🔥 Top Actions:</b>\n")
        for action, count in top:
            txt += f"• {action}: <b>{count}</b>\n"
        bot.send_message(uid, txt)

    elif d == "adm_users" and uid == OWNER_ID:
        cur.execute("SELECT uid,name,username,total_actions,last_seen FROM users ORDER BY total_actions DESC")
        rows = cur.fetchall()
        txt = f"<b>👥 ALL USERS ({len(rows)})</b>\n{'─'*30}\n"
        mk = types.InlineKeyboardMarkup(row_width=1)
        for r in rows:
            txt += f"• <code>{r[0]}</code> <b>{r[1]}</b> (@{r[2]}) — {r[3]} actions | {r[4][11:16]}\n"
            mk.add(types.InlineKeyboardButton(
                f"👤 {r[1]} ({r[3]} actions)",
                callback_data=f"udetail_{r[0]}"
            ))
        bot.send_message(uid, txt)
        bot.send_message(uid, "ক্লিক করো details দেখতে:", reply_markup=mk)

    elif d.startswith("udetail_") and uid == OWNER_ID:
        show_user_detail(uid, int(d.split("_")[1]))

    elif d.startswith("ban_") and uid == OWNER_ID:
        target = int(d.split("_")[1])
        with db_lock:
            cur.execute("INSERT OR REPLACE INTO banned VALUES (?,?,?)", (target, "Admin ban", bd_now()))
            db.commit()
        bot.send_message(uid, f"⛔ User <code>{target}</code> banned!")

    elif d == "adm_log_0" and uid == OWNER_ID:
        cur.execute("SELECT uid, action_type, action, detail, timestamp FROM activity_log ORDER BY id DESC LIMIT 50")
        rows = cur.fetchall()
        txt = f"<b>📋 RECENT ACTIVITY LOG (last 50)</b>\n{'─'*30}\n"
        for r in rows:
            icon = "🖱" if r[1]=="BUTTON" else ("⌨️" if r[1]=="INPUT" else "⚡")
            txt += f"{icon} <code>{r[0]}</code> | <b>{r[2]}</b> | {r[4][11:16]}\n"
            if r[3] and r[3] != r[2]: txt += f"   └ <i>{r[3][:60]}</i>\n"
        if len(txt) > 4000: txt = txt[:3900] + "\n...(truncated)"
        bot.send_message(uid, txt)

    elif d == "adm_banned" and uid == OWNER_ID:
        cur.execute("SELECT uid, reason, banned_at FROM banned")
        rows = cur.fetchall()
        if not rows:
            bot.send_message(uid, "No banned users.")
            return
        txt = f"<b>⛔ BANNED ({len(rows)})</b>\n{'─'*25}\n"
        for r in rows:
            txt += f"• <code>{r[0]}</code> — {r[1]} | {r[2][:16]}\n"
        bot.send_message(uid, txt)

    elif d == "adm_save" and uid == OWNER_ID:
        save_to_github()
        bot.send_message(uid, "✅ Saved to GitHub!")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEXT HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    if is_banned(uid): return
    register_user(message.from_user)
    text = message.text.strip()
    if text.startswith("/"): return

    state = user_states.pop(uid, None)
    log_action(uid, "INPUT", state or "free_text", text[:100])

    if state == "osint":
        msg = bot.reply_to(message, f"🔍 Scanning <b>{text}</b> across 16 platforms...\n⏳ Please wait (10-15s)")
        results = check_username(text)
        found = [(p,u) for p,(s,u) in results.items() if "✅" in s]
        not_found = [p for p,(s,u) in results.items() if "❌" in s]
        other = [(p,s) for p,(s,u) in results.items() if "✅" not in s and "❌" not in s]

        txt = f"<b>🔍 OSINT: {text}</b>\n{'═'*28}\n\n"
        if found:
            txt += f"<b>✅ FOUND ({len(found)}):</b>\n"
            for p, url in found:
                txt += f"  • <b>{p}</b>: <a href='{url}'>{url}</a>\n"
        if not_found:
            txt += f"\n<b>❌ NOT FOUND ({len(not_found)}):</b>\n"
            txt += "  " + " | ".join(not_found) + "\n"
        if other:
            txt += f"\n<b>⚠️ OTHER:</b>\n"
            for p, s in other: txt += f"  • {p}: {s}\n"
        txt += f"\n{'─'*28}\n🕐 {now_bd_short()} BD"
        bot.edit_message_text(txt, message.chat.id, msg.message_id,
                              disable_web_page_preview=True)

    elif state == "tglookup":
        uname = text.lstrip("@")
        msg = bot.reply_to(message, f"📱 Looking up @{uname}...")
        r = telegram_lookup(uname)
        if "error" in r:
            bot.edit_message_text(f"❌ Error: {r['error']}", message.chat.id, msg.message_id)
            return
        txt = (f"<b>📱 TELEGRAM LOOKUP</b>\n{'═'*26}\n\n"
               f"🔗 Username: @{uname}\n"
               f"📊 Status: <b>{r.get('status','Unknown')}</b>\n")
        if r.get('title'): txt += f"📛 Title: <b>{r['title']}</b>\n"
        if r.get('type'): txt += f"🏷 Type: <b>{r['type']}</b>\n"
        if r.get('members'): txt += f"👥 Members: <b>{r['members']}</b>\n"
        if r.get('description'): txt += f"📝 Bio: <i>{r['description'][:150]}</i>\n"
        txt += f"🌐 URL: {r.get('url','')}\n"
        txt += f"\n🕐 {now_bd_short()} BD"

        if r.get('photo'):
            try:
                bot.delete_message(message.chat.id, msg.message_id)
                bot.send_photo(message.chat.id, r['photo'], caption=txt)
            except: bot.edit_message_text(txt, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text(txt, message.chat.id, msg.message_id)

    elif state == "ipinfo":
        msg = bot.reply_to(message, f"🌐 Looking up {text}...")
        r = ip_lookup(text)
        if r.get("status") != "success":
            bot.edit_message_text(f"❌ {r.get('message','Failed')}", message.chat.id, msg.message_id)
            return
        proxy = "⚠️ YES" if r.get("proxy") else "✅ No"
        hosting = "🖥 YES" if r.get("hosting") else "✅ No"
        mobile = "📱 YES" if r.get("mobile") else "No"
        txt = (f"<b>🌐 IP INTELLIGENCE</b>\n{'═'*26}\n\n"
               f"🔍 IP:       <code>{r.get('query')}</code>\n"
               f"🌍 Country:  <b>{r.get('country')}</b>\n"
               f"🏙 Region:   {r.get('regionName')}\n"
               f"📍 City:     {r.get('city')}\n"
               f"📮 ZIP:      {r.get('zip')}\n"
               f"🗺 Coords:   {r.get('lat')}, {r.get('lon')}\n"
               f"⏰ Timezone: {r.get('timezone')}\n"
               f"{'─'*26}\n"
               f"🏢 ISP:     {r.get('isp')}\n"
               f"🏭 Org:     {r.get('org')}\n"
               f"📡 AS:      {r.get('as')}\n"
               f"{'─'*26}\n"
               f"🔓 Proxy:   {proxy}\n"
               f"🖥 Hosting: {hosting}\n"
               f"📱 Mobile:  {mobile}\n"
               f"\n🕐 {now_bd_short()} BD")
        bot.edit_message_text(txt, message.chat.id, msg.message_id)

    elif state == "link":
        msg = bot.reply_to(message, "🔗 Analyzing...")
        r = analyze_link(text)
        if "error" in r:
            bot.edit_message_text(f"❌ {r['error']}", message.chat.id, msg.message_id)
            return
        txt = (f"<b>🔗 LINK ANALYSIS</b>\n{'═'*26}\n\n"
               f"🏁 Final URL:\n<code>{r['final']}</code>\n\n"
               f"🌐 Domain:    <b>{r['domain']}</b>\n"
               f"🔀 Redirects: <b>{r['hops']}</b>\n"
               f"📶 Status:    <b>{r['status']}</b>\n")
        if len(r['chain']) > 1:
            txt += f"\n<b>Redirect Chain:</b>\n"
            for i, u in enumerate(r['chain']):
                txt += f"  {i+1}→ <code>{u[:55]}</code>\n"
        txt += f"\n🕐 {now_bd_short()} BD"
        bot.edit_message_text(txt, message.chat.id, msg.message_id)

    elif state == "dns":
        msg = bot.reply_to(message, f"🔎 DNS lookup for {text}...")
        r = dns_lookup(text)
        if "error" in r:
            bot.edit_message_text(f"❌ {r['error']}", message.chat.id, msg.message_id)
            return
        proxy = "⚠️ Proxy/VPN" if r.get('proxy') else "✅ Clean"
        hosting = "🖥 Hosting/DC" if r.get('hosting') else "✅ Regular"
        txt = (f"<b>🔎 DNS LOOKUP</b>\n{'═'*26}\n\n"
               f"🌐 Domain:   <b>{r['domain']}</b>\n"
               f"📡 IP:       <code>{r.get('ip','N/A')}</code>\n"
               f"🔄 Reverse:  {r.get('reverse','N/A')}\n"
               f"{'─'*26}\n"
               f"🌍 Country: {r.get('country','')}\n"
               f"🏢 ISP:     {r.get('isp','')}\n"
               f"🏭 Org:     {r.get('org','')}\n"
               f"🔓 Proxy:   {proxy}\n"
               f"🖥 Type:    {hosting}\n"
               f"\n🕐 {now_bd_short()} BD")
        bot.edit_message_text(txt, message.chat.id, msg.message_id)

    elif state == "ssl":
        msg = bot.reply_to(message, f"🔒 Checking SSL for {text}...")
        r = ssl_check(text)
        if not r.get("valid"):
            bot.edit_message_text(
                f"<b>🔒 SSL CHECK</b>\n{'═'*26}\n\n"
                f"🌐 Domain: <b>{text}</b>\n"
                f"❌ Status: <b>INVALID/EXPIRED</b>\n"
                f"⚠️ Error: {r.get('error','')}",
                message.chat.id, msg.message_id)
            return
        txt = (f"<b>🔒 SSL CERTIFICATE</b>\n{'═'*26}\n\n"
               f"✅ Status:  <b>VALID</b>\n"
               f"🌐 Domain:  <b>{r.get('domain')}</b>\n"
               f"🏢 Issuer:  {r.get('issuer')}\n"
               f"📅 Expires: <b>{r.get('expires')}</b>\n")
        if r.get('san'):
            txt += f"📋 SANs: {', '.join(r['san'][:3])}\n"
        txt += f"\n🕐 {now_bd_short()} BD"
        bot.edit_message_text(txt, message.chat.id, msg.message_id)

    elif state == "hash":
        hashes = make_hash(text.encode())
        txt = (f"<b>🔐 HASH RESULTS</b>\n{'═'*26}\n\n"
               f"Input: <code>{text[:50]}</code>\n{'─'*26}\n")
        for algo, val in hashes.items():
            txt += f"<b>{algo}:</b>\n<code>{val}</code>\n\n"
        txt += f"🕐 {now_bd_short()} BD"
        bot.reply_to(message, txt)

    elif state == "breach":
        msg = bot.reply_to(message, "📧 Checking breach database...")
        r = check_breach(text)
        if "error" in r:
            bot.edit_message_text(f"⚠️ {r['error']}", message.chat.id, msg.message_id)
        elif r.get("found"):
            sources = "\n".join([f"  • {s}" for s in r.get('sources', [])])
            bot.edit_message_text(
                f"<b>📧 BREACH RESULT</b>\n{'═'*26}\n\n"
                f"📧 Email: <code>{text}</code>\n"
                f"🚨 Status: <b>COMPROMISED!</b>\n"
                f"📊 Breaches: <b>{r['count']}</b>\n\n"
                f"<b>Sources:</b>\n{sources}\n\n"
                f"⚠️ Immediately change your password!\n"
                f"🕐 {now_bd_short()} BD",
                message.chat.id, msg.message_id)
        else:
            bot.edit_message_text(
                f"<b>📧 BREACH RESULT</b>\n{'═'*26}\n\n"
                f"📧 Email: <code>{text}</code>\n"
                f"✅ Status: <b>CLEAN — No breaches found!</b>\n\n"
                f"🕐 {now_bd_short()} BD",
                message.chat.id, msg.message_id)
    else:
        bot.reply_to(message, "💡 /start দিয়ে menu দেখো।",
                     reply_markup=main_menu(uid))

# FILE HANDLER — hash file
@bot.message_handler(content_types=['document'])
def handle_file(message):
    uid = message.from_user.id
    if is_banned(uid): return
    register_user(message.from_user)
    log_action(uid, "INPUT", "file_upload", message.document.file_name)
    if user_states.get(uid) == "hash":
        user_states.pop(uid)
        msg = bot.reply_to(message, "🔐 Hashing file...")
        try:
            fi = bot.get_file(message.document.file_id)
            raw = bot.download_file(fi.file_path)
            hashes = make_hash(raw)
            txt = (f"<b>🔐 FILE HASH</b>\n{'═'*26}\n\n"
                   f"📁 File: <code>{message.document.file_name}</code>\n"
                   f"💾 Size: {round(len(raw)/1024,1)} KB\n{'─'*26}\n")
            for algo, val in hashes.items():
                txt += f"<b>{algo}:</b>\n<code>{val}</code>\n\n"
            txt += f"🕐 {now_bd_short()} BD"
            bot.edit_message_text(txt, message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"❌ {e}", message.chat.id, msg.message_id)
    else:
        bot.reply_to(message, "📁 Hash করতে চাইলে আগে 🔐 Hash বাটন চাপো।")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KEEP ALIVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class PingServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        cur.execute("SELECT COUNT(*) FROM users")
        u = cur.fetchone()[0]
        self.wfile.write(
            f"BDCyber Bot v2.0 | Uptime: {get_uptime()} | Users: {u} | BD: {now_bd_short()}".encode()
        )
    def log_message(self, *args): pass

def keep_alive():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), PingServer).serve_forever()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LAUNCH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  BDCyber Intelligence Bot v2.0")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    load_from_github()
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=auto_save_loop, daemon=True).start()
    print(f"[Boot] Restart #{restart_count[0]} | BD Time: {now_bd_short()}")
    print("[Bot] Polling started!")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
