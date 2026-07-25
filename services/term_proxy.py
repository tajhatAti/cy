"""
services/term_proxy.py — terminal HTTP/WS endpoints exposed by the MAIN app.

The interactive PTY terminal ALWAYS runs IN-PROCESS (embedded) on the main app:
PTY sessions are local by nature (they fork bash/python/node child processes in
THIS container). Even when RUNNER_SERVICE_URL points at a remote runner for
jobs, the terminal runs here.

* Authenticates user (cookie/JWT via get_current_user_and_session).
* Allocates/reuses a PTY session via in-process terminal.Manager.
* Supports MULTI-SLOT tabs: slot=N, name=..., persist=true for 24/7 mode.
* Mounts the runner.app.terminal_ws WebSocket handler at /api/term/ws.
"""
import os
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from routes.deps import get_current_user_and_session, rate_limit_user

router = APIRouter()
log = logging.getLogger("term_proxy")


class TermCreateRequest(BaseModel):
    shell: Optional[str] = "bash"
    cols: Optional[int] = 90
    rows: Optional[int] = 28
    slot: Optional[int] = None
    name: Optional[str] = ""
    persist: Optional[bool] = None
    reuse: Optional[bool] = True


class TermPersistRequest(BaseModel):
    persist: bool = False
    name: Optional[str] = ""
    cmd: Optional[str] = ""


def _mgr():
    from runner import terminal as _term
    return _term.manager


@router.post("/api/terminals")
def create_terminal(payload: TermCreateRequest, authorization: Optional[str] = Header(None)):
    user, _ = get_current_user_and_session(authorization)
    rate_limit_user(user["id"], "exec")

    shell = (payload.shell or "bash").strip().lower()
    if shell not in ("bash", "python", "node"):
        raise HTTPException(400, "shell must be bash / python / node")
    cols = max(40, min(payload.cols or 90, 240))
    rows = max(10, min(payload.rows or 28, 80))
    slot = payload.slot if payload.slot and payload.slot > 0 else None

    try:
        info = _mgr().create(
            user["id"], shell=shell, cols=cols, rows=rows,
            slot=slot, name=(payload.name or ""), persist=payload.persist,
            reuse_existing=bool(payload.reuse if payload.reuse is not None else True),
        )
    except Exception as exc:
        log.exception("Terminal create failed")
        raise HTTPException(500, f"Terminal spawn failed: {exc}")

    return {
        "id": info["id"], "shell": info["shell"], "ticket": info["ticket"],
        "slot": info.get("slot", 1), "name": info.get("name", "Shell 1"),
        "persist": bool(info.get("persist")),
        "ws_url": f"/api/term/ws?ticket={info['ticket']}",
    }


@router.get("/api/terminals")
def list_terminals(authorization: Optional[str] = Header(None)):
    user, _ = get_current_user_and_session(authorization)
    return {"terminals": _mgr().list_for(user["id"])}


@router.patch("/api/terminals/{term_id}")
def update_terminal(term_id: str, payload: TermPersistRequest,
                    authorization: Optional[str] = Header(None)):
    user, _ = get_current_user_and_session(authorization)
    info = _mgr().set_slot_persist(
        term_id, user["id"], bool(payload.persist),
        startup_cmd=(payload.cmd or ""), name=(payload.name or ""),
    )
    if not info:
        raise HTTPException(404, "Terminal not found")
    return info


@router.delete("/api/terminals/{term_id}")
def close_terminal(term_id: str, authorization: Optional[str] = Header(None)):
    user, _ = get_current_user_and_session(authorization)
    ok = _mgr().close(term_id, user["id"])
    return {"closed": ok}


# Mount the WebSocket bridge directly (same manager / same sessions).
from runner.app import terminal_ws as _runner_term_ws_impl  # noqa: E402
router.websocket("/api/term/ws")(_runner_term_ws_impl)
