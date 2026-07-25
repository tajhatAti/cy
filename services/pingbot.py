"""In-process Telegram "Server Alive" bot — runs inside the main web service.

When the env var TELEGRAM_PING_BOT_TOKEN is set, a daemon thread starts on
app boot and long-polls Telegram. No extra Render service, no RunSpace job,
no separate process needed. Because the main web service already has a
self-ping loop keeping it awake, the bot stays up 24/7 too.

Commands:
    /start /help  — welcome + command list
    /ping [url]   — real server-side HTTP ping (server → target, no TG hop lag)

If the env var is NOT set, the bot stays dormant — zero overhead.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

log = logging.getLogger("codenest.pingbot")

BOT_TOKEN = os.getenv("TELEGRAM_PING_BOT_TOKEN", "").strip()
DEFAULT_TARGET = os.getenv("PING_DEFAULT_TARGET", "https://ahadorg.onrender.com").strip()
PING_TIMEOUT_S = float(os.getenv("PING_TIMEOUT_S", "8"))
POLL_TIMEOUT_S = 40
ERROR_BACKOFF_S = 5
UA = "CodeNest-AliveBot/1.0"

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _tg(method: str, **params) -> dict:
    if not TG_API:
        return {}
    try:
        s = _get_session()
        r = s.get(f"{TG_API}/{method}", params=params, timeout=POLL_TIMEOUT_S + 20)
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram %s failed: %s", method, exc)
        return {}


def _send(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    _tg("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode,
        disable_web_page_preview=True)


def _is_blocked_host(host: str) -> bool:
    """Lightweight SSRF guard — don't let the public bot hit localhost/metadata."""
    h = host.lower()
    bad = ("localhost", "metadata.google.internal", "metadata",
           "127.0.0.1", "0.0.0.0", "::1", "ip6-localhost", "ip6-loopback")
    if h in bad:
        return True
    if h.endswith(".local") or h.endswith(".internal"):
        return True
    # Block private IP literals
    try:
        import ipaddress
        ip = ipaddress.ip_address(h.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    except ValueError:
        pass
    return False


def _ping(target: str) -> dict:
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    try:
        p = urlparse(target)
        if p.scheme not in ("http", "https") or not p.hostname:
            return {"ok": False, "error": "Please give a valid http(s) URL"}
        if _is_blocked_host(p.hostname):
            return {"ok": False, "error": "That host is blocked (internal/private)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"bad URL: {e}"}

    s = _get_session()
    headers = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
    t0 = time.perf_counter()
    try:
        r = s.request("HEAD", target, headers=headers, allow_redirects=True,
                      timeout=PING_TIMEOUT_S)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000
        return {"ok": True, "status": r.status_code, "latency_ms": round(ms, 2),
                "target": target, "final_url": r.url}
    except requests.exceptions.RequestException:
        # Fallback to GET for hosts that reject HEAD
        t0 = time.perf_counter()
        try:
            r = s.request("GET", target, headers=headers, allow_redirects=True,
                          timeout=PING_TIMEOUT_S, stream=True)
            r.close()
            t1 = time.perf_counter()
            ms = (t1 - t0) * 1000
            return {"ok": True, "status": r.status_code, "latency_ms": round(ms, 2),
                    "target": target, "final_url": r.url}
        except requests.exceptions.ConnectTimeout:
            return {"ok": False, "error": f"timed out ({int(PING_TIMEOUT_S)}s)", "target": target}
        except requests.exceptions.SSLError as e:
            return {"ok": False, "error": f"TLS error: {type(e).__name__}", "target": target}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: connection failed", "target": target}


def _reply(text: str, first_name: str) -> str:
    t = (text or "").strip()
    low = t.lower()

    if low.startswith("/start"):
        return (
            f"👋 Hi {first_name}! I'm the CodeNest Server-Alive bot.\n\n"
            "/ping [url]  — check if a website is alive and see real response time\n"
            "/help — commands\n\n"
            "Timing is measured server-side, so Telegram lag is NOT counted. "
            "Default target: " + DEFAULT_TARGET
        )

    if low.startswith("/help"):
        return (
            "🤖 *Commands*\n\n"
            "`/start` — welcome\n"
            "`/help` — this message\n"
            f"`/ping` — ping the default site ({DEFAULT_TARGET})\n"
            "`/ping <url>` — ping any URL (auto-adds https://)\n"
            "`/status` — bot + server uptime info"
        )

    if low.startswith("/status"):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            "🟢 *Bot is running*\n\n"
            f"🕒 Server time: `{now}`\n"
            f"🎯 Default target: `{DEFAULT_TARGET}`\n"
            "⏱️ Timing method: server-side HEAD request"
        )

    if low.startswith("/ping"):
        parts = t.split(None, 1)
        target = parts[1].strip() if len(parts) > 1 else DEFAULT_TARGET
        res = _ping(target)
        if not res.get("ok"):
            return (
                f"🌐 *Target:* {res.get('target', target)}\n"
                f"❌ {res.get('error', 'ping failed')}\n\n"
                "_The server could not reach this host._"
            )
        ms = res["latency_ms"]
        if ms < 150: bar = "🟢"
        elif ms < 500: bar = "🟡"
        elif ms < 1500: bar = "🟠"
        else: bar = "🔴"
        s = res["status"]
        if 200 <= s < 400: verdict = "✅ *ALIVE*"
        elif s >= 500: verdict = "💥 *SERVER ERROR*"
        elif s >= 400: verdict = "⚠️ *REACHABLE but client error*"
        else: verdict = "❓ *UNEXPECTED*"
        return (
            f"🌐 *Target:* {res['target']}\n"
            f"{verdict}\n"
            f"⚡ Latency: {bar} `{ms:.2f} ms`\n"
            f"📊 HTTP `{s}`\n\n"
            "_server-side timing — Telegram delay excluded_"
        )

    return "🤖 Try /help for commands, or /ping to check the server."


def _poll_loop() -> None:
    log.info("🤖 Telegram ping bot starting (default=%s)", DEFAULT_TARGET)
    offset = 0
    me = _tg("getMe")
    if me and me.get("ok"):
        log.info("🤖 Bot identity: @%s", (me.get("result") or {}).get("username", "?"))
    else:
        log.warning("🤖 getMe failed — check TELEGRAM_PING_BOT_TOKEN")

    while True:
        try:
            updates = _tg("getUpdates", offset=offset, timeout=POLL_TIMEOUT_S)
            if updates and not updates.get("ok"):
                desc = updates.get("description", "?")
                log.error("getUpdates error: %s — retrying in %ss", desc, ERROR_BACKOFF_S * 2)
                time.sleep(ERROR_BACKOFF_S * 2)
                continue
            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                text = msg.get("text")
                if not chat_id or not text:
                    continue
                who = ((msg.get("from") or {}).get("first_name")) or "friend"
                try:
                    out = _reply(text, who)
                    _send(chat_id, out)
                except Exception as e:  # noqa: BLE001
                    log.exception("reply failed for chat %s", chat_id)
                    _send(chat_id, f"⚠️ Error: {e}")
            time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log.exception("poll loop error — retrying in %ss", ERROR_BACKOFF_S)
            time.sleep(ERROR_BACKOFF_S)


def start_bot() -> None:
    """Kick off the bot poller on a daemon thread. Safe to call multiple times."""
    if not BOT_TOKEN:
        log.info("ℹ️ TELEGRAM_PING_BOT_TOKEN not set — ping bot dormant.")
        return
    t = threading.Thread(target=_poll_loop, name="telegram-ping-bot", daemon=True)
    t.start()
    log.info("🤖 Telegram ping bot thread started")
