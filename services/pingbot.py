"""
Telegram Bot - @mytestrenderbot
Only /code + Inline Buttons (Fixed)
"""
import os
import re
import threading
import time
import requests
import json
from collections import defaultdict

BOT_TOKEN = os.getenv("TELEGRAM_PING_BOT_TOKEN", "").strip()
RUNNER_SECRET = os.getenv("RUNNER_SERVICE_SECRET", "")
SITE_BASE = os.getenv("SITE_BASE_URL", "https://ahadorg.onrender.com").rstrip("/")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

code_buffer = defaultdict(list)
buffer_timer = {}
last_job = {}


def _tg(method, **params):
    if not TG_API:
        return {}
    try:
        r = requests.get(f"{TG_API}/{method}", params=params, timeout=50)
        return r.json()
    except:
        return {}


def _send(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    _tg("sendMessage", **data)


# ==================== 5 SECOND BUFFER ====================
def flush_code(chat_id, first_name):
    if chat_id not in code_buffer:
        return
    code = "\n".join(code_buffer[chat_id])
    del code_buffer[chat_id]
    if chat_id in buffer_timer:
        del buffer_timer[chat_id]
    deploy_code(code, chat_id, first_name)


def collect_code(chat_id, text, first_name):
    code_buffer[chat_id].append(text)
    if chat_id in buffer_timer:
        buffer_timer[chat_id].cancel()
    timer = threading.Timer(5.0, flush_code, args=[chat_id, first_name])
    timer.start()
    buffer_timer[chat_id] = timer


# ==================== DEPLOY + BUTTONS ====================
def detect_libs(code):
    imports = re.findall(r'^\s*(?:import|from)\s+([a-zA-Z0-9_]+)', code, re.MULTILINE)
    common = {"requests": "requests", "flask": "flask", "fastapi": "fastapi",
              "pandas": "pandas", "openai": "openai"}
    return [common.get(i.lower()) for i in imports if i.lower() in common]


def get_buttons(runner_id, url):
    return {
        "inline_keyboard": [
            [
                {"text": "📜 Logs", "callback_data": f"logs:{runner_id}"},
                {"text": "⏱ Uptime", "callback_data": f"uptime:{runner_id}"}
            ],
            [
                {"text": "🔄 Restart", "callback_data": f"restart:{runner_id}"},
                {"text": "🛑 Stop", "callback_data": f"stop:{runner_id}"}
            ],
            [
                {"text": "📥 DB", "callback_data": f"db:{runner_id}"},
                {"text": "📋 My Jobs", "callback_data": "myjobs"}
            ],
            [{"text": "🌐 Open Live URL", "url": url}]
        ]
    }


def deploy_code(code, chat_id, first_name):
    libs = detect_libs(code)
    lang = "python"
    if "console.log" in code.lower():
        lang = "javascript"
    elif "<html" in code.lower():
        lang = "html"

    username = first_name.lower().replace(" ", "_")[:10]
    job_name = f"tg-{username}-{int(time.time())}"

    msg = f"✅ *Code Received*\nLanguage: `{lang}`"
    if libs:
        msg += f"\nInstalling: `{', '.join(libs)}`"
    msg += f"\n\nDeploying `{job_name}`..."
    _send(chat_id, msg)

    payload = {"name": job_name, "language": lang, "code": code}
    if libs:
        payload["requirements"] = "\n".join(libs)

    try:
        from services.runner_client import _runner_http
        resp = _runner_http("POST", "/internal/jobs", payload)
        
        if resp.status_code != 201:
            _send(chat_id, f"❌ {resp.json().get('detail', 'Failed')}")
            return

        job = resp.json()
        runner_id = job.get("id")
        url = job.get("web_url") or f"{SITE_BASE}/live/{job_name}"
        last_job[chat_id] = runner_id

        # Send message WITH buttons
        success_msg = f"🚀 *Deployed Successfully!*\n\nLive URL: {url}"
        buttons = get_buttons(runner_id, url)
        _send(chat_id, success_msg, reply_markup=buttons)

    except Exception as e:
        _send(chat_id, f"Error: {str(e)}")


# ==================== CALLBACK ====================
def handle_callback(chat_id, data):
    try:
        action, runner_id = data.split(":")
    except:
        if data == "myjobs":
            _send(chat_id, "Use the inline buttons after deploying with /code.")
            return
        return

    from services.runner_client import _runner_http

    if action == "logs":
        try:
            r = _runner_http("GET", f"/internal/jobs/{runner_id}")
            logs = r.json().get("logs", "No logs")[-650:]
            _send(chat_id, f"```\n{logs}\n```")
        except:
            _send(chat_id, "❌ Could not fetch logs")

    elif action == "uptime":
        try:
            r = _runner_http("GET", f"/internal/jobs/{runner_id}")
            data = r.json()
            _send(chat_id, f"⏱ Uptime: {data.get('uptime_s', 0)}s\nStatus: `{data.get('status')}`")
        except:
            _send(chat_id, "❌ Error")

    elif action == "restart":
        try:
            _runner_http("POST", f"/internal/jobs/{runner_id}/restart")
            _send(chat_id, "🔄 Restart requested!")
        except:
            _send(chat_id, "❌ Restart failed")

    elif action == "stop":
        try:
            _runner_http("POST", f"/internal/jobs/{runner_id}/stop")
            _send(chat_id, "🛑 Job stopped.")
        except:
            _send(chat_id, "❌ Stop failed")

    elif action == "db":
        _send(chat_id, "📥 DB download coming soon...")


# ==================== MAIN ====================
def poll_loop():
    if not BOT_TOKEN:
        return
    print("🤖 Bot starting...")
    offset = 0

    while True:
        try:
            updates = _tg("getUpdates", offset=offset, timeout=40)
            if not updates or not updates.get("ok"):
                time.sleep(1)
                continue

            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1

                if "message" in upd:
                    msg = upd["message"]
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "") or ""
                    first_name = msg.get("from", {}).get("first_name", "user")

                    if text.startswith("/start"):
                        _send(chat_id, f"👋 Hi {first_name}!\n\n"
                              "Send `/code` then paste your code.\n"
                              "All controls will appear as buttons after deploy.")

                    elif text.startswith("/code"):
                        _send(chat_id, "✅ Send your code now")

                    else:
                        collect_code(chat_id, text, first_name)

                elif "callback_query" in upd:
                    cb = upd["callback_query"]
                    handle_callback(cb["message"]["chat"]["id"], cb["data"])
                    _tg("answerCallbackQuery", callback_query_id=cb["id"])

        except Exception as e:
            print("Poll error:", e)
            time.sleep(3)


def start_bot():
    if not BOT_TOKEN:
        print("TELEGRAM_PING_BOT_TOKEN not set")
        return
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    print("✅ Bot started (Inline buttons fixed)")


if __name__ == "__main__":
    start_bot()