import os
import time
import sys
import re
import uuid
import json
import glob
import math
import subprocess
import io
import contextlib
import asyncio
import threading
import hashlib
import base64
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote

import httpx

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from mangum import Mangum

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_DIR          = "/tmp/nova_storage"
LOOP_DIR             = os.path.join(STORAGE_DIR, "loops")
FETCH_CACHE_DIR      = os.path.join(STORAGE_DIR, "fetch_cache")
WEBHOOK_DATA_DIR     = os.path.join(STORAGE_DIR, "webhooks")
EXEC_CACHE_DIR       = os.path.join(STORAGE_DIR, "exec_cache")
COOKIES_DIR          = os.path.join(STORAGE_DIR, "cookies")
SHARED_CTX_PATH      = os.path.join(STORAGE_DIR, "shared_context.json")
WEBHOOKS_CONFIG_PATH = os.path.join(STORAGE_DIR, "webhooks_config.json")

for _d in [STORAGE_DIR, LOOP_DIR, FETCH_CACHE_DIR, WEBHOOK_DATA_DIR, EXEC_CACHE_DIR, COOKIES_DIR]:
    os.makedirs(_d, exist_ok=True)

ACTIVITY_LOG_PATH   = os.path.join(STORAGE_DIR, "activity_logs.json")
ACTIVITY_MAX_LOGS   = 500
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1510176991423369256/ymcTcjGsuaIUeMb54RaObGPyaxV7iG0X9SkcxubOXlAbIaIjVa2P9y_prsltEOBqkhjo"
TELEGRAM_BOT_TOKEN  = "8679170863:AAFmTenZRZMG6AnUvVE9orf45BMkSBapCeQ"
TELEGRAM_CHAT_ID    = "8519461263"
_activity_lock      = threading.Lock()


# ── Activity Logger ────────────────────────────────────────────────────────────
def _activity_append(entry: dict) -> None:
    with _activity_lock:
        try:
            if os.path.exists(ACTIVITY_LOG_PATH):
                with open(ACTIVITY_LOG_PATH, "r") as f:
                    logs = json.load(f)
                if not isinstance(logs, list):
                    logs = []
            else:
                logs = []
            logs.append(entry)
            if len(logs) > ACTIVITY_MAX_LOGS:
                logs = logs[-ACTIVITY_MAX_LOGS:]
            with open(ACTIVITY_LOG_PATH, "w") as f:
                json.dump(logs, f, indent=2)
        except Exception:
            pass


async def _discord_notify(entry: dict) -> None:
    try:
        emoji   = "✅" if entry["status_code"] < 400 else "❌"
        content = (
            f"{emoji} `{entry['method']} {entry['path']}` "
            f"→ **{entry['status_code']}** | "
            f"`{entry['duration_ms']}ms` | "
            f"IP: `{entry['ip']}` | "
            f"UA: `{entry['user_agent'][:60]}`"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(DISCORD_WEBHOOK_URL, json={"content": content})
    except Exception:
        pass


async def _telegram_notify_rum(entry: dict) -> None:
    try:
        rum = entry.get("rum_data", {})
        screen   = rum.get("screen", {})
        viewport = rum.get("viewport", {})
        text = (
            f"📡 *RUM Activity Detected*\n\n"
            f"🌐 URL: `{rum.get('url', '-')}`\n"
            f"🖥 Platform: `{rum.get('platform', '-')}`\n"
            f"🗣 Language: `{rum.get('language', '-')}`\n"
            f"🕐 Timezone: `{rum.get('timezone', '-')}`\n"
            f"📺 Screen: `{screen.get('width', 0)}x{screen.get('height', 0)}`\n"
            f"🪟 Viewport: `{viewport.get('width', 0)}x{viewport.get('height', 0)}`\n"
            f"💻 Cores: `{rum.get('cores', '-')}`\n"
            f"📱 Touch pts: `{rum.get('touch_points', '-')}`\n"
            f"🌍 Online: `{rum.get('online', '-')}`\n"
            f"🕵️ UA: `{str(rum.get('userAgent', '-'))[:100]}`\n"
            f"🕒 Time: `{entry.get('timestamp', '-')}`"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "Markdown",
            })
    except Exception:
        pass


@app.middleware("http")
async def activity_middleware(request: Request, call_next):
    t0 = time.monotonic()

    body_bytes = await request.body()

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    request = Request(request.scope, receive)

    response = await call_next(request)

    duration_ms = round((time.monotonic() - t0) * 1000, 2)

    entry = {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "method":      request.method,
        "path":        request.url.path,
        "query":       str(request.url.query),
        "ip":          request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown"),
        "user_agent":  request.headers.get("user-agent", ""),
        "cookies":     dict(request.cookies),
        "headers":     dict(request.headers),
        "status_code": response.status_code,
        "duration_ms": duration_ms,
    }

    ev = asyncio.get_event_loop()
    await ev.run_in_executor(None, _activity_append, entry)
    await _discord_notify(entry)

    return response


# ── /api/aktivitas ─────────────────────────────────────────────────────────────
@app.get("/api/aktivitas")
async def get_aktivitas(limit: int = 100):
    def _read():
        if not os.path.exists(ACTIVITY_LOG_PATH):
            return []
        try:
            with open(ACTIVITY_LOG_PATH, "r") as f:
                logs = json.load(f)
            if not isinstance(logs, list):
                return []
            return logs
        except Exception:
            return []

    ev   = asyncio.get_running_loop()
    logs = await ev.run_in_executor(None, _read)
    logs = logs[-(min(limit, ACTIVITY_MAX_LOGS)):]
    logs.reverse()
    return {"status": True, "count": len(logs), "logs": logs}

PIL_FORMAT_MAP = {
    "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
    "webp": "WEBP", "gif": "GIF", "bmp": "BMP",
    "tiff": "TIFF", "tif": "TIFF",
}

_context_lock     = threading.Lock()
_webhook_cfg_lock = threading.Lock()
_webhook_cache:      dict  = {}
_webhook_cache_time: float = 0.0
_WEBHOOK_CACHE_TTL:  float = 5.0

_GH_BASE = "https://api.github.com"


# ── Root ───────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "online", "message": "Server Nova aktif bestie! 🫂💙"}


# ── Shared Context ─────────────────────────────────────────────────────────────
def _load_shared_context() -> dict:
    with _context_lock:
        if not os.path.exists(SHARED_CTX_PATH):
            return {}
        try:
            with open(SHARED_CTX_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}


def _save_shared_context(data: dict) -> None:
    with _context_lock:
        try:
            with open(SHARED_CTX_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass


@app.get("/api/context")
async def get_context():
    ev  = asyncio.get_running_loop()
    ctx = await ev.run_in_executor(None, _load_shared_context)
    return {"status": True, "context": ctx}


@app.post("/api/context")
async def set_context(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

    action = (body.get("action") or "merge").lower()
    data   = body.get("data", {})
    if not isinstance(data, dict):
        return JSONResponse({"status": False, "error": "'data' harus berupa object"})

    ev = asyncio.get_running_loop()
    if action == "replace":
        await ev.run_in_executor(None, _save_shared_context, data)
    elif action == "clear":
        await ev.run_in_executor(None, _save_shared_context, {})
    elif action == "delete":
        ctx = await ev.run_in_executor(None, _load_shared_context)
        for k in (data.get("keys") or list(data.keys())):
            ctx.pop(k, None)
        await ev.run_in_executor(None, _save_shared_context, ctx)
    else:
        ctx = await ev.run_in_executor(None, _load_shared_context)
        ctx.update(data)
        await ev.run_in_executor(None, _save_shared_context, ctx)

    ctx = await ev.run_in_executor(None, _load_shared_context)
    return {"status": True, "action": action, "context": ctx}


# ── Execution Cache ────────────────────────────────────────────────────────────
_EXEC_CACHE_MAX = 20


def _save_exec_result(exec_id: str, data: dict) -> None:
    try:
        existing = sorted(glob.glob(os.path.join(EXEC_CACHE_DIR, "*.json")))
        while len(existing) >= _EXEC_CACHE_MAX:
            try:
                os.remove(existing.pop(0))
            except OSError:
                pass
        with open(os.path.join(EXEC_CACHE_DIR, f"{exec_id}.json"), "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _load_exec_result(exec_id: str) -> Optional[dict]:
    p = os.path.join(EXEC_CACHE_DIR, f"{exec_id}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _list_exec_cache() -> list:
    try:
        files = sorted(glob.glob(os.path.join(EXEC_CACHE_DIR, "*.json")), reverse=True)
        results = []
        for fp in files:
            try:
                with open(fp, "r") as f:
                    d = json.load(f)
                results.append({
                    "execution_id": d.get("execution_id"),
                    "timestamp":    d.get("timestamp"),
                    "duration_ms":  d.get("duration_ms"),
                    "mode":         d.get("mode"),
                    "status":       d.get("status"),
                    "error":        d.get("error"),
                })
            except Exception:
                pass
        return results
    except Exception:
        return []


# ── Error Enhancement ──────────────────────────────────────────────────────────
def _enhance_error(exc_type: str, exc_msg: str, env_keys: list) -> list:
    tips = []
    if exc_type in ("ImportError", "ModuleNotFoundError"):
        m = re.search(r"No module named '([^']+)'", exc_msg)
        if m:
            mod = m.group(1).split(".")[0]
            tips.append(f"Coba install: pip install {mod}")
            tips.append(f"Atau di god mode: !pip install {mod} -q")
    elif exc_type == "NameError":
        m = re.search(r"name '([^']+)' is not defined", exc_msg)
        if m:
            name = m.group(1)
            user_vars = [k for k in env_keys if not k.startswith("_") and k not in (
                "os", "sys", "subprocess", "json", "re", "uuid", "time", "datetime",
                "httpx", "STORAGE_DIR", "shared", "save_shared", "__builtins__",
            )]
            similar = [k for k in user_vars if name.lower() in k.lower() or k.lower().startswith(name[:2].lower())]
            if similar:
                tips.append(f"Variabel mirip di scope: {', '.join(similar[:5])}")
            elif user_vars:
                tips.append(f"Variabel tersedia di scope: {', '.join(user_vars[:8])}")
    elif exc_type == "SyntaxError":
        tips.append("Cek indentasi, tanda kutip, atau kurung yang tidak tertutup")
    elif exc_type == "TypeError":
        tips.append("Cek tipe data argumen — mungkin ada None atau tipe yang tidak cocok")
    elif exc_type == "KeyError":
        m = re.search(r"KeyError: '?([^']+)'?", exc_msg)
        if m:
            tips.append(f"Key '{m.group(1)}' tidak ada. Gunakan .get('{m.group(1)}') untuk default None")
    elif exc_type == "FileNotFoundError":
        tips.append("File tidak ditemukan. Cek path, gunakan STORAGE_DIR untuk simpan file di /tmp/nova_storage")
    return tips


# ── Memory Estimate ────────────────────────────────────────────────────────────
def _estimate_memory(env: dict) -> int:
    try:
        total = 0
        for k, v in env.items():
            if not k.startswith("_"):
                try:
                    total += sys.getsizeof(v)
                except Exception:
                    pass
        return total
    except Exception:
        return 0


# ── God Mode ───────────────────────────────────────────────────────────────────
_BLOCKED_CMDS = [
    r"\brm\s+-rf\s+/\s*$",
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r":\(\)\s*\{",
]


def _normalize_code(code: str) -> str:
    code = code.replace("\\n", "\n").replace("\\t", "\t")
    lines = code.splitlines()
    if not lines:
        return code
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return code
    min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
    if min_indent > 0:
        lines = [l[min_indent:] if len(l) >= min_indent else l for l in lines]
    return "\n".join(lines)


def _run_god_mode(
    code: str,
    exec_timeout: int = 25,
    shared_ctx: Optional[dict] = None,
    save_cache: bool = True,
) -> dict:
    exec_id = uuid.uuid4().hex[:16]
    ts      = _now()
    t0      = time.monotonic()

    code = _normalize_code(code)

    if code.startswith("!"):
        cmd = code[1:].strip()
        for pat in _BLOCKED_CMDS:
            if re.search(pat, cmd):
                result = {
                    "status": False, "error": "Blocked: command berbahaya",
                    "mode": "shell", "execution_id": exec_id, "timestamp": ts,
                }
                if save_cache:
                    _save_exec_result(exec_id, result)
                return result
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=min(exec_timeout, 25),
            )
            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            result = {
                "status":       r.returncode == 0,
                "mode":         "shell",
                "command":      cmd,
                "output":       r.stdout,
                "stderr":       r.stderr,
                "exit_code":    r.returncode,
                "execution_id": exec_id,
                "timestamp":    ts,
                "duration_ms":  duration_ms,
            }
            if not result["status"] and r.stderr:
                result["error"] = r.stderr.strip()
            if save_cache:
                _save_exec_result(exec_id, result)
            return result
        except subprocess.TimeoutExpired:
            result = {
                "status": False, "error": f"Shell timeout setelah {exec_timeout}s",
                "mode": "shell", "execution_id": exec_id, "timestamp": ts,
                "duration_ms": round((time.monotonic() - t0) * 1000, 2),
            }
            if save_cache:
                _save_exec_result(exec_id, result)
            return result
        except Exception as e:
            result = {
                "status": False, "error": f"{type(e).__name__}: {e}",
                "mode": "shell", "execution_id": exec_id, "timestamp": ts,
                "duration_ms": round((time.monotonic() - t0) * 1000, 2),
            }
            if save_cache:
                _save_exec_result(exec_id, result)
            return result

    buf = io.StringIO()
    env: dict = {
        "__builtins__": __builtins__,
        "os":           os,
        "sys":          sys,
        "subprocess":   subprocess,
        "json":         json,
        "re":           re,
        "uuid":         uuid,
        "time":         time,
        "datetime":     datetime,
        "httpx":        httpx,
        "STORAGE_DIR":  STORAGE_DIR,
        "shared":       dict(shared_ctx or {}),
        "save_shared":  _save_shared_context,
    }
    with contextlib.redirect_stdout(buf):
        try:
            exec(compile(code, "<nova>", "exec"), env)
            if shared_ctx is not None:
                new_shared = env.get("shared", {})
                if isinstance(new_shared, dict) and new_shared != shared_ctx:
                    _save_shared_context(new_shared)
            duration_ms  = round((time.monotonic() - t0) * 1000, 2)
            memory_bytes = _estimate_memory(env)
            result = {
                "status":       True,
                "mode":         "python",
                "output":       buf.getvalue(),
                "execution_id": exec_id,
                "timestamp":    ts,
                "duration_ms":  duration_ms,
                "memory_bytes": memory_bytes,
            }
            if save_cache:
                _save_exec_result(exec_id, result)
            return result
        except SyntaxError as e:
            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            result = {
                "status":       False,
                "mode":         "python",
                "error":        f"SyntaxError baris {e.lineno}: {e.msg}",
                "suggestions":  ["Cek indentasi, tanda kutip, atau kurung yang tidak tertutup"],
                "execution_id": exec_id,
                "timestamp":    ts,
                "duration_ms":  duration_ms,
            }
            if save_cache:
                _save_exec_result(exec_id, result)
            return result
        except Exception as e:
            import traceback
            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            exc_type    = type(e).__name__
            tips        = _enhance_error(exc_type, str(e), list(env.keys()))
            result = {
                "status":       False,
                "mode":         "python",
                "error":        f"{exc_type}: {e}",
                "traceback":    traceback.format_exc(limit=5),
                "suggestions":  tips,
                "execution_id": exec_id,
                "timestamp":    ts,
                "duration_ms":  duration_ms,
            }
            if save_cache:
                _save_exec_result(exec_id, result)
            return result


def _extract_file_lines(filepath: str, line_range: str) -> Optional[str]:
    safe_path = os.path.join(STORAGE_DIR, os.path.basename(filepath))
    if not os.path.exists(safe_path):
        if filepath.startswith("/tmp/") and os.path.exists(filepath):
            safe_path = filepath
        else:
            return None
    try:
        with open(safe_path, "r") as f:
            all_lines = f.readlines()
        if line_range and "-" in line_range:
            parts = line_range.split("-")
            start = max(1, int(parts[0])) - 1
            end   = int(parts[1])
            return "".join(all_lines[start:end])
        return "".join(all_lines)
    except Exception:
        return None


@app.get("/api")
@app.get("/api/run")
async def god_mode_get(
    code:    str = "",
    status:  str = "",
    timeout: int = 25,
    exec_id: str = "",
    history: int = 0,
    file:    str = "",
    lines:   str = "",
):
    ev = asyncio.get_running_loop()

    if exec_id:
        cached = await ev.run_in_executor(None, _load_exec_result, exec_id)
        if cached is None:
            return JSONResponse({"status": False, "error": f"exec_id '{exec_id}' tidak ditemukan"})
        cached["from_cache"] = True
        return cached

    if history:
        result = await ev.run_in_executor(None, _list_exec_cache)
        return {"status": True, "count": len(result), "executions": result}

    if status:
        return await ev.run_in_executor(None, _loop_get_status, status)

    if file:
        snippet = await ev.run_in_executor(None, _extract_file_lines, file, lines)
        if snippet is None:
            return JSONResponse({"status": False, "error": f"File '{file}' tidak ditemukan di STORAGE_DIR"})
        code = snippet

    if not code:
        return JSONResponse({"status": False, "error": "Parameter 'code', 'exec_id', atau 'file' wajib diisi"})

    shared = await ev.run_in_executor(None, _load_shared_context)
    return await ev.run_in_executor(None, _run_god_mode, code, timeout, shared, True)


@app.post("/api")
@app.post("/api/run")
async def god_mode_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

    ev = asyncio.get_running_loop()

    exec_id = (body.get("exec_id") or "").strip()
    if exec_id:
        cached = await ev.run_in_executor(None, _load_exec_result, exec_id)
        if cached is None:
            return JSONResponse({"status": False, "error": f"exec_id '{exec_id}' tidak ditemukan"})
        cached["from_cache"] = True
        return cached

    if body.get("history"):
        result = await ev.run_in_executor(None, _list_exec_cache)
        return {"status": True, "count": len(result), "executions": result}

    if "url" in body:
        url = (body.get("url") or "").strip()
        if not url:
            return JSONResponse({"status": False, "error": "Field 'url' kosong"})
        raw_data    = body.get("data") or body.get("body")
        parsed_body: Optional[dict] = None
        if isinstance(raw_data, dict):
            parsed_body = raw_data
        elif isinstance(raw_data, str):
            try:
                parsed_body = json.loads(raw_data)
            except json.JSONDecodeError:
                return JSONResponse({"status": False, "error": "'data' bukan JSON yang valid"})
        result = await _execute_single_fetch(
            url=url, method=(body.get("method") or "GET").upper(),
            req_headers=body.get("headers") or {}, body=parsed_body,
            form_data=body.get("form_data"), timeout=int(body.get("timeout", 15)),
            follow_redirect=bool(body.get("follow_redirect", True)),
            max_redirects=int(body.get("max_redirects", 10)),
            auth_type=body.get("auth_type", ""), auth_token=body.get("auth_token", ""),
            auth_user=body.get("auth_user", ""), auth_pass=body.get("auth_pass", ""),
            parse=body.get("parse", ""), cache=int(body.get("cache", 0)),
            dl=int(body.get("dl", 0)), max_size=body.get("max_size", ""),
            save_cookies=body.get("save_cookies", ""),
            load_cookies=body.get("load_cookies", ""),
            retry=int(body.get("retry", 0)),
        )
        return result if isinstance(result, Response) else result

    file  = (body.get("file") or "").strip()
    lines = (body.get("lines") or "").strip()
    code  = (body.get("code") or "").strip()
    if file and not code:
        snippet = await ev.run_in_executor(None, _extract_file_lines, file, lines)
        if snippet is None:
            return JSONResponse({"status": False, "error": f"File '{file}' tidak ditemukan di STORAGE_DIR"})
        code = snippet

    if not code:
        return JSONResponse({"status": False, "error": "Field 'code', 'exec_id', atau 'file' wajib diisi"})

    shared = await ev.run_in_executor(None, _load_shared_context)
    return await ev.run_in_executor(None, _run_god_mode, code, int(body.get("timeout", 25)), shared, True)


# ── Loop System ────────────────────────────────────────────────────────────────
def _loop_path(loop_id: str) -> str:
    return os.path.join(LOOP_DIR, f"loop_{loop_id}.json")


def _loop_write(loop_id: str, state: dict) -> None:
    try:
        with open(_loop_path(loop_id), "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def _loop_read(loop_id: str) -> Optional[dict]:
    p = _loop_path(loop_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _loop_get_status(loop_id: str) -> dict:
    state = _loop_read(loop_id)
    if state is None:
        return {"status": False, "error": f"Loop '{loop_id}' tidak ditemukan"}
    if state.get("status") == "running" and state.get("iteration", 0) > 0:
        wait = state.get("wait_time", 60)
        last = state.get("last_run_unix", 0)
        nxt  = max(0.0, round(last + wait - time.time(), 1))
        state["next_run_in_s"] = nxt
        state["next_run_at"]   = datetime.fromtimestamp(last + wait, tz=timezone.utc).isoformat()
    return {"status": True, "loop": state}


def _loop_stop(loop_id: str) -> dict:
    state = _loop_read(loop_id)
    if state is None:
        return {"status": False, "error": f"Loop '{loop_id}' tidak ditemukan"}
    if state.get("status") in ("stopped", "completed"):
        return {"status": True, "message": f"Loop sudah {state['status']}", "loop_id": loop_id}
    state.update({"status": "stopped", "stopped_at": _now(), "stopped_reason": "manual"})
    _loop_write(loop_id, state)
    return {"status": True, "message": "Loop dihentikan", "loop_id": loop_id}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_run_condition(run_if: str, state: dict) -> tuple:
    if not run_if:
        return True, ""
    run_if = run_if.strip()

    if run_if == "last_status_success":
        if state.get("iteration", 0) == 0:
            return True, ""
        ok = state.get("last_error") is None
        return ok, ("" if ok else "Kondisi tidak terpenuhi: last_status_success")

    if run_if == "last_status_error":
        if state.get("iteration", 0) == 0:
            return False, "Kondisi last_status_error tidak relevan di iterasi pertama"
        ok = state.get("last_error") is not None
        return ok, ("" if ok else "Kondisi tidak terpenuhi: last_status_error")

    if run_if.startswith("last_output_contains="):
        keyword = run_if.split("=", 1)[1]
        ok = keyword in (state.get("last_output") or "")
        return ok, ("" if ok else f"Output tidak mengandung '{keyword}'")

    if run_if.startswith("last_output_not_contains="):
        keyword = run_if.split("=", 1)[1]
        ok = keyword not in (state.get("last_output") or "")
        return ok, ("" if ok else f"Output mengandung '{keyword}', skip")

    if run_if.startswith("iteration_mod="):
        try:
            mod = int(run_if.split("=", 1)[1])
            ok  = (state.get("iteration", 0) % mod) == 0
            return ok, ("" if ok else f"Iterasi tidak kelipatan {mod}")
        except ValueError:
            return True, ""

    return True, ""


def _loop_create(
    code:           str,
    interval:       int  = 60,
    wait_time:      Optional[int] = None,
    exec_timeout:   int  = 25,
    stop_on_error:  bool = False,
    max_iterations: int  = 0,
    webhook_url:    str  = "",
    notify_on:      Optional[list] = None,
    depends_on:     str  = "",
    description:    str  = "",
    run_if:         str  = "",
    retry_on_error: int  = 0,
    run_now:        bool = True,
) -> dict:
    wt      = max(5, min(wait_time if wait_time is not None else interval, 3600))
    loop_id = uuid.uuid4().hex[:12]
    state   = {
        "loop_id":           loop_id,
        "description":       description,
        "status":            "running",
        "code":              code,
        "interval":          interval,
        "wait_time":         wt,
        "exec_timeout":      min(exec_timeout, 25),
        "stop_on_error":     stop_on_error,
        "max_iterations":    max_iterations,
        "webhook_url":       webhook_url,
        "notify_on":         notify_on or ["error"],
        "depends_on":        depends_on,
        "run_if":            run_if,
        "retry_on_error":    min(retry_on_error, 5),
        "iteration":         0,
        "created_at":        _now(),
        "last_run_unix":     0,
        "last_heartbeat":    None,
        "last_output":       None,
        "last_error":        None,
        "last_exec_time_s":  None,
        "executing":         False,
        "execution_started": None,
        "history":           [],
    }
    _loop_write(loop_id, state)

    result: dict = {
        "status":           True,
        "message":          "Loop created",
        "loop_id":          loop_id,
        "wait_time":        wt,
        "run_if":           run_if or None,
        "retry_on_error":   retry_on_error,
        "note":             "Gunakan /api/run/loop/tick?loop_id=... dari Vercel Cron untuk tiap iterasi",
        "tick_url":         f"/api/run/loop/tick?loop_id={loop_id}",
        "status_url":       f"/api/run/loop?action=status&loop_id={loop_id}",
        "stop_url":         f"/api/run/loop?action=stop&loop_id={loop_id}",
    }

    if run_now:
        result["first_tick"] = _loop_tick(loop_id)

    return result


def _loop_tick(loop_id: str) -> dict:
    state = _loop_read(loop_id)
    if state is None:
        return {"status": False, "error": f"Loop '{loop_id}' tidak ditemukan"}

    if state["status"] != "running":
        return {"status": False, "error": f"Loop tidak aktif (status: {state['status']})", "loop_id": loop_id}

    dep = state.get("depends_on", "")
    if dep:
        dep_state = _loop_read(dep)
        if dep_state and dep_state.get("status") not in ("completed", "stopped"):
            return {
                "status":  False,
                "error":   f"Dependency loop '{dep}' belum selesai (status: {dep_state.get('status')})",
                "loop_id": loop_id,
            }

    now_unix  = time.time()
    last_unix = state.get("last_run_unix", 0)
    wait_time = state.get("wait_time", 60)
    iteration = state.get("iteration", 0)

    if iteration > 0:
        elapsed = now_unix - last_unix
        if elapsed < wait_time:
            return {
                "status":        True,
                "executed":      False,
                "skipped":       True,
                "reason":        "Belum waktunya eksekusi",
                "next_run_in_s": round(wait_time - elapsed, 1),
                "loop_id":       loop_id,
            }

    run_if = state.get("run_if", "")
    should_run, skip_reason = _check_run_condition(run_if, state)
    if not should_run:
        state["last_run_unix"] = time.time()
        _loop_write(loop_id, state)
        return {
            "status":        True,
            "executed":      False,
            "skipped":       True,
            "reason":        skip_reason,
            "next_run_in_s": wait_time,
            "loop_id":       loop_id,
        }

    exec_start_ts = _now()
    state.update({"executing": True, "execution_started": exec_start_ts, "last_heartbeat": exec_start_ts})
    _loop_write(loop_id, state)

    shared         = _load_shared_context()
    max_retries    = state.get("retry_on_error", 0)
    run_result     = None
    retry_attempts = 0
    t0             = time.monotonic()

    for attempt in range(max_retries + 1):
        if attempt > 0:
            delay = 2 ** (attempt - 1)
            time.sleep(delay)
            retry_attempts += 1

        run_result = _run_god_mode(
            state["code"],
            exec_timeout=state.get("exec_timeout", 25),
            shared_ctx=shared,
            save_cache=False,
        )
        if run_result.get("status"):
            break

    exec_time = round(time.monotonic() - t0, 3)
    iteration += 1
    ts         = _now()
    success    = bool(run_result.get("status"))

    history_entry = {
        "iteration":      iteration,
        "timestamp":      ts,
        "output":         run_result.get("output", ""),
        "stderr":         run_result.get("stderr", ""),
        "error":          run_result.get("error"),
        "traceback":      run_result.get("traceback"),
        "suggestions":    run_result.get("suggestions"),
        "exec_time_s":    exec_time,
        "retry_attempts": retry_attempts,
        "ok":             success,
    }

    state.update({
        "iteration":         iteration,
        "last_heartbeat":    ts,
        "last_run_unix":     time.time(),
        "last_output":       run_result.get("output", ""),
        "last_error":        run_result.get("error"),
        "last_exec_time_s":  exec_time,
        "executing":         False,
        "execution_started": None,
        "history":           (state.get("history", []) + [history_entry])[-50:],
    })

    if not success and state.get("stop_on_error"):
        state.update({"status": "stopped", "stopped_reason": "error", "stopped_at": ts})
    elif state.get("max_iterations", 0) > 0 and iteration >= state["max_iterations"]:
        state.update({"status": "completed", "stopped_reason": "max_iterations_reached", "stopped_at": ts})
    else:
        state["status"] = "running"

    _loop_write(loop_id, state)

    webhook_url   = state.get("webhook_url", "")
    notify_on     = state.get("notify_on", ["error"])
    webhook_sent  = False
    webhook_error = None
    if webhook_url:
        should = (
            (not success and "error" in notify_on)
            or (state["status"] == "completed" and "complete" in notify_on)
            or (state["status"] == "stopped"   and "stop"     in notify_on)
            or ("always" in notify_on)
        )
        if should:
            try:
                httpx.post(
                    webhook_url,
                    json={
                        "loop_id":        loop_id,
                        "description":    state.get("description", ""),
                        "event":          "error" if not success else state["status"],
                        "iteration":      iteration,
                        "output":         run_result.get("output", ""),
                        "error":          run_result.get("error"),
                        "exec_time_s":    exec_time,
                        "retry_attempts": retry_attempts,
                    },
                    timeout=5.0,
                )
                webhook_sent = True
            except Exception as e:
                webhook_error = str(e)

    tick_result: dict = {
        "status":         True,
        "executed":       True,
        "loop_id":        loop_id,
        "iteration":      iteration,
        "exec_time_s":    exec_time,
        "output":         run_result.get("output", ""),
        "stderr":         run_result.get("stderr", ""),
        "error":          run_result.get("error"),
        "suggestions":    run_result.get("suggestions"),
        "traceback":      run_result.get("traceback"),
        "retry_attempts": retry_attempts,
        "loop_status":    state["status"],
        "webhook_sent":   webhook_sent,
    }
    if webhook_error:
        tick_result["webhook_error"] = webhook_error
    if state["status"] == "running":
        tick_result["next_run_in_s"] = wait_time
    return tick_result


def _loop_list() -> dict:
    try:
        loops = []
        for fname in os.listdir(LOOP_DIR):
            if not (fname.startswith("loop_") and fname.endswith(".json")):
                continue
            try:
                with open(os.path.join(LOOP_DIR, fname), "r") as f:
                    loops.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
        loops.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"status": True, "count": len(loops), "loops": loops}
    except Exception as e:
        return {"status": False, "error": str(e)}


@app.get("/api/run/loop/tick")
async def loop_tick_get(loop_id: str = ""):
    if not loop_id:
        return JSONResponse({"status": False, "error": "Parameter 'loop_id' wajib diisi"})
    ev = asyncio.get_running_loop()
    return await ev.run_in_executor(None, _loop_tick, loop_id)


@app.post("/api/run/loop/tick")
async def loop_tick_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})
    loop_id = (body.get("loop_id") or "").strip()
    if not loop_id:
        return JSONResponse({"status": False, "error": "Field 'loop_id' wajib diisi"})
    ev = asyncio.get_running_loop()
    return await ev.run_in_executor(None, _loop_tick, loop_id)


@app.get("/api/run/loop")
async def loop_get(
    action:         str  = "",
    loop_id:        str  = "",
    code:           str  = "",
    interval:       int  = 60,
    wait_time:      int  = 0,
    exec_timeout:   int  = 25,
    stop_on_error:  bool = False,
    max_iterations: int  = 0,
    webhook_url:    str  = "",
    description:    str  = "",
    run_if:         str  = "",
    retry_on_error: int  = 0,
    run_now:        bool = True,
):
    ev  = asyncio.get_running_loop()
    act = action.lower()
    if act == "stop"   and loop_id: return await ev.run_in_executor(None, _loop_stop,       loop_id)
    if act == "status" and loop_id: return await ev.run_in_executor(None, _loop_get_status, loop_id)
    if act == "list":               return await ev.run_in_executor(None, _loop_list)
    if act == "tick"   and loop_id: return await ev.run_in_executor(None, _loop_tick,       loop_id)
    if not code:
        return JSONResponse({"status": False, "error": "Parameter 'code' wajib diisi"})
    wt = wait_time if wait_time > 0 else interval
    return await ev.run_in_executor(None, lambda: _loop_create(
        code=code, interval=interval, wait_time=wt, exec_timeout=exec_timeout,
        stop_on_error=stop_on_error, max_iterations=max_iterations,
        webhook_url=webhook_url, description=description,
        run_if=run_if, retry_on_error=retry_on_error, run_now=run_now,
    ))


@app.post("/api/run/loop")
async def loop_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

    act     = (body.get("action") or "").lower()
    loop_id = (body.get("loop_id") or "").strip()
    ev      = asyncio.get_running_loop()

    if act == "stop":   return await ev.run_in_executor(None, _loop_stop,       loop_id)
    if act == "status": return await ev.run_in_executor(None, _loop_get_status, loop_id)
    if act == "list":   return await ev.run_in_executor(None, _loop_list)
    if act == "tick":   return await ev.run_in_executor(None, _loop_tick,       loop_id)

    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse({"status": False, "error": "Field 'code' wajib diisi"})

    interval = int(body.get("interval", 60))
    wt       = int(body.get("wait_time", 0)) or interval
    return await ev.run_in_executor(None, lambda: _loop_create(
        code           = code,
        interval       = interval,
        wait_time      = wt,
        exec_timeout   = int(body.get("exec_timeout", 25)),
        stop_on_error  = bool(body.get("stop_on_error", False)),
        max_iterations = int(body.get("max_iterations", 0)),
        webhook_url    = body.get("webhook_url", ""),
        notify_on      = body.get("notify_on") or ["error"],
        depends_on     = body.get("depends_on", ""),
        description    = body.get("description", ""),
        run_if         = body.get("run_if", ""),
        retry_on_error = int(body.get("retry_on_error", 0)),
        run_now        = bool(body.get("run_now", True)),
    ))


# ── Cookie / Session Management ────────────────────────────────────────────────
def _load_cookies(session_name: str) -> dict:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_name)
    p    = os.path.join(COOKIES_DIR, f"{safe}.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cookies(session_name: str, cookies: dict) -> None:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_name)
    p    = os.path.join(COOKIES_DIR, f"{safe}.json")
    try:
        with open(p, "w") as f:
            json.dump(cookies, f)
    except OSError:
        pass


def _parse_max_size(max_size_str: str) -> int:
    if not max_size_str:
        return 0
    s = str(max_size_str).strip().lower()
    try:
        if s.endswith("gb"):
            return int(float(s[:-2]) * 1024 ** 3)
        if s.endswith("mb"):
            return int(float(s[:-2]) * 1024 ** 2)
        if s.endswith("kb"):
            return int(float(s[:-2]) * 1024)
        return int(s)
    except (ValueError, TypeError):
        return 0


# ── Proxy Fetch ────────────────────────────────────────────────────────────────
def _cache_key(url: str, method: str, body_s: str, hdrs_s: str) -> str:
    raw = f"{method}:{url}:{body_s}:{hdrs_s}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_read(key: str, ttl: int) -> Optional[dict]:
    p = os.path.join(FETCH_CACHE_DIR, f"{key}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r") as f:
            entry = json.load(f)
        if time.time() - entry.get("_cached_at", 0) > ttl:
            try:
                os.remove(p)
            except OSError:
                pass
            return None
        entry.pop("_cached_at", None)
        return entry
    except (json.JSONDecodeError, OSError):
        return None


def _cache_write(key: str, data: dict) -> None:
    try:
        p       = os.path.join(FETCH_CACHE_DIR, f"{key}.json")
        payload = {**data, "_cached_at": time.time()}
        with open(p, "w") as f:
            json.dump(payload, f)
    except OSError:
        pass


def _parse_content(content: str, mode: str) -> dict:
    if mode == "json":
        try:
            return {"parse_mode": "json", "parsed": json.loads(content)}
        except Exception as e:
            return {"parse_mode": "json", "parse_error": str(e)}

    if mode == "html":
        title_m = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
        links   = re.findall(r'href=["\']([^"\']{1,300})["\']', content, re.I)
        images  = re.findall(r'src=["\']([^"\']{1,300})["\']', content, re.I)
        metas   = re.findall(r"<meta[^>]+>", content, re.I)
        clean   = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)).strip()
        return {
            "parse_mode": "html",
            "title":      title_m.group(1).strip() if title_m else None,
            "links":      list(dict.fromkeys(links))[:50],
            "images":     list(dict.fromkeys(images))[:30],
            "meta_tags":  metas[:20],
            "text":       clean[:3000],
        }

    if mode == "xml":
        def _elem(el: ET.Element, depth: int = 0) -> dict:
            if depth > 10:
                return {"tag": el.tag, "truncated": True}
            return {
                "tag":      el.tag,
                "attrib":   dict(el.attrib),
                "text":     (el.text or "").strip() or None,
                "children": [_elem(c, depth + 1) for c in el],
            }
        try:
            return {"parse_mode": "xml", "parsed": _elem(ET.fromstring(content))}
        except Exception as e:
            return {"parse_mode": "xml", "parse_error": str(e)}

    if mode == "auto":
        s = content.strip()
        if s.startswith(("{", "[")):
            return _parse_content(content, "json")
        if re.search(r"<!doctype\s+html|<html", s[:300], re.I):
            return _parse_content(content, "html")
        if s.startswith("<"):
            return _parse_content(content, "xml")
        return {"parse_mode": "auto", "detected": "plain_text", "text": s[:3000]}

    return {}


_RETRY_STATUS_CODES = {500, 502, 503, 504, 429}


async def _execute_single_fetch(
    url:             str,
    method:          str  = "GET",
    req_headers:     Optional[dict] = None,
    body:            Optional[dict] = None,
    form_data:       Optional[dict] = None,
    timeout:         int  = 15,
    follow_redirect: bool = True,
    max_redirects:   int  = 10,
    auth_type:       str  = "",
    auth_token:      str  = "",
    auth_user:       str  = "",
    auth_pass:       str  = "",
    parse:           str  = "",
    cache:           int  = 0,
    dl:              int  = 0,
    max_size:        str  = "",
    save_cookies:    str  = "",
    load_cookies:    str  = "",
    retry:           int  = 0,
) -> "dict | Response":
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return {"status": False, "error": f"Method '{method}' tidak didukung", "url": url}

    max_size_bytes = _parse_max_size(str(max_size))
    max_retries    = min(int(retry), 3)

    hdrs = dict(req_headers or {})
    hdrs.setdefault("User-Agent", "Mozilla/5.0 (NovaProxy/2.0)")
    hdrs.setdefault("Accept-Encoding", "gzip, deflate, br")

    if auth_type == "bearer" and auth_token:
        hdrs["Authorization"] = f"Bearer {auth_token}"
    elif auth_type == "basic" and auth_user:
        creds = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        hdrs["Authorization"] = f"Basic {creds}"
    elif auth_type == "custom" and auth_token:
        hdrs["Authorization"] = auth_token

    session_cookies: dict = {}
    if load_cookies:
        session_cookies = _load_cookies(load_cookies)

    cache_key: Optional[str] = None
    if cache > 0 and method == "GET" and dl == 0:
        cache_key = _cache_key(
            url, method,
            json.dumps(body or {}, sort_keys=True),
            json.dumps({k.lower(): v for k, v in hdrs.items()}, sort_keys=True),
        )
        cached = _cache_read(cache_key, cache)
        if cached:
            cached["from_cache"] = True
            return cached

    last_error: Optional[str] = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            await asyncio.sleep(2 ** (attempt - 1))

        try:
            async with httpx.AsyncClient(
                timeout=float(min(max(timeout, 1), 60)),
                follow_redirects=follow_redirect,
                max_redirects=max(1, min(max_redirects, 20)),
                cookies=session_cookies,
            ) as client:
                kwargs: dict = {"headers": hdrs}
                if form_data and method != "GET":
                    kwargs["data"] = form_data
                elif body and method != "GET":
                    kwargs["json"] = body

                res       = await client.request(method, url, **kwargs)
                raw_bytes = res.content

                if save_cookies and res.cookies:
                    updated = dict(session_cookies)
                    updated.update(dict(res.cookies))
                    _save_cookies(save_cookies, updated)

                truncated = False
                if max_size_bytes > 0 and len(raw_bytes) > max_size_bytes:
                    raw_bytes = raw_bytes[:max_size_bytes]
                    truncated = True

                if dl == 1:
                    ctype = res.headers.get("content-type", "application/octet-stream")
                    return Response(content=raw_bytes, media_type=ctype)

                if res.status_code in _RETRY_STATUS_CODES and attempt < max_retries:
                    last_error = f"HTTP {res.status_code}"
                    continue

                encoding = res.encoding or "utf-8"
                text     = raw_bytes.decode(encoding, errors="ignore")

                result = {
                    "status":           True,
                    "url":              str(res.url),
                    "method":           method,
                    "http_code":        res.status_code,
                    "response_headers": dict(res.headers),
                    "length":           len(raw_bytes),
                    "content":          text,
                    "from_cache":       False,
                    "truncated":        truncated,
                    "attempt":          attempt + 1,
                }
                if truncated:
                    result["truncated_at_bytes"] = max_size_bytes
                if save_cookies:
                    result["cookies_saved"] = save_cookies
                if parse:
                    result["parse_result"] = _parse_content(text, parse)
                if cache_key:
                    _cache_write(cache_key, result)
                return result

        except httpx.TimeoutException:
            last_error = f"Timeout setelah {timeout}s"
            if attempt < max_retries:
                continue
            return {"status": False, "error": last_error, "url": url, "attempt": attempt + 1}
        except httpx.TooManyRedirects:
            return {"status": False, "error": f"Terlalu banyak redirect (limit: {max_redirects})", "url": url}
        except httpx.RequestError as e:
            last_error = f"Request error: {type(e).__name__}: {e}"
            if attempt < max_retries:
                continue
            return {"status": False, "error": last_error, "url": url, "attempt": attempt + 1}
        except Exception as e:
            return {"status": False, "error": str(e), "url": url}

    return {"status": False, "error": last_error or "Unknown error", "url": url, "attempt": max_retries + 1}


@app.get("/api/fetch")
async def fetch_get(
    url:             str,
    dl:              int  = 0,
    method:          str  = "GET",
    headers:         str  = "",
    body:            str  = "",
    timeout:         int  = 15,
    follow_redirect: bool = True,
    max_redirects:   int  = 10,
    auth_type:       str  = "",
    auth_token:      str  = "",
    auth_user:       str  = "",
    auth_pass:       str  = "",
    parse:           str  = "",
    cache:           int  = 0,
    max_size:        str  = "",
    save_cookies:    str  = "",
    load_cookies:    str  = "",
    retry:           int  = 0,
):
    parsed_hdrs: dict = {}
    if headers:
        try:
            parsed_hdrs = json.loads(headers)
            if not isinstance(parsed_hdrs, dict):
                return JSONResponse({"status": False, "error": "'headers' harus JSON object"})
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "'headers' bukan JSON yang valid"})

    parsed_body: Optional[dict] = None
    if body:
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "'body' bukan JSON yang valid"})

    result = await _execute_single_fetch(
        url=url, method=method, req_headers=parsed_hdrs, body=parsed_body,
        timeout=timeout, follow_redirect=follow_redirect, max_redirects=max_redirects,
        auth_type=auth_type, auth_token=auth_token,
        auth_user=auth_user, auth_pass=auth_pass,
        parse=parse, cache=cache, dl=dl,
        max_size=max_size, save_cookies=save_cookies,
        load_cookies=load_cookies, retry=retry,
    )
    return result if isinstance(result, Response) else result


@app.post("/api/fetch")
async def fetch_post(request: Request):
    try:
        req_body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

    chain = req_body.get("chain")
    if chain is not None:
        if not isinstance(chain, list) or len(chain) == 0:
            return JSONResponse({"status": False, "error": "'chain' harus array non-kosong"})
        if len(chain) > 10:
            return JSONResponse({"status": False, "error": "Maks 10 request per chain"})
        results: list     = []
        prev_result: dict = {}
        for i, cfg in enumerate(chain):
            if not isinstance(cfg, dict):
                results.append({"index": i, "status": False, "error": "Setiap item harus berupa object"})
                break
            step_url = (cfg.get("url") or "").strip()
            if not step_url:
                results.append({"index": i, "status": False, "error": "'url' wajib ada di setiap step"})
                break
            step_body = dict(cfg.get("body") or {})
            if cfg.get("use_prev_response") and prev_result.get("status"):
                step_body["_prev"] = prev_result.get("content", "")[:2000]
            step_result = await _execute_single_fetch(
                url=step_url, method=cfg.get("method", "GET"),
                req_headers=cfg.get("headers") or {}, body=step_body or None,
                form_data=cfg.get("form_data"), timeout=int(cfg.get("timeout", 15)),
                follow_redirect=bool(cfg.get("follow_redirect", True)),
                max_redirects=int(cfg.get("max_redirects", 10)),
                auth_type=cfg.get("auth_type", ""), auth_token=cfg.get("auth_token", ""),
                auth_user=cfg.get("auth_user", ""), auth_pass=cfg.get("auth_pass", ""),
                parse=cfg.get("parse", ""), cache=int(cfg.get("cache", 0)), dl=0,
                max_size=cfg.get("max_size", ""),
                save_cookies=cfg.get("save_cookies", ""),
                load_cookies=cfg.get("load_cookies", ""),
                retry=int(cfg.get("retry", 0)),
            )
            if isinstance(step_result, dict):
                step_result["index"] = i
                results.append(step_result)
                prev_result = step_result
            else:
                results.append({"index": i, "status": False, "error": "Unexpected response type"})
                prev_result = {}
        return {"status": True, "mode": "chain", "chain_count": len(results), "results": results}

    url = (req_body.get("url") or "").strip()
    if not url:
        return JSONResponse({"status": False, "error": "Field 'url' wajib diisi"})
    result = await _execute_single_fetch(
        url=url, method=req_body.get("method", "GET"),
        req_headers=req_body.get("headers") or {},
        body=req_body.get("body") or req_body.get("data"),
        form_data=req_body.get("form_data"), timeout=int(req_body.get("timeout", 15)),
        follow_redirect=bool(req_body.get("follow_redirect", True)),
        max_redirects=int(req_body.get("max_redirects", 10)),
        auth_type=req_body.get("auth_type", ""), auth_token=req_body.get("auth_token", ""),
        auth_user=req_body.get("auth_user", ""), auth_pass=req_body.get("auth_pass", ""),
        parse=req_body.get("parse", ""), cache=int(req_body.get("cache", 0)),
        dl=int(req_body.get("dl", 0)), max_size=req_body.get("max_size", ""),
        save_cookies=req_body.get("save_cookies", ""),
        load_cookies=req_body.get("load_cookies", ""),
        retry=int(req_body.get("retry", 0)),
    )
    return result if isinstance(result, Response) else result


# ── Document Processor ─────────────────────────────────────────────────────────
@app.get("/nova/doc")
async def process_doc(action: str, url: str, page: int = 0):
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "docx"):
        return JSONResponse({"status": False, "error": "Format tidak didukung. Gunakan PDF atau DOCX."})

    valid = {"pdf": ["extract-text", "metadata"], "docx": ["extract-docx"]}
    if action not in valid[ext]:
        return JSONResponse({"status": False, "error": f"Action '{action}' tidak valid untuk {ext.upper()}. Pilihan: {valid[ext]}"})

    tmp = f"/tmp/{uuid.uuid4().hex}.{ext}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            with open(tmp, "wb") as f:
                f.write(res.content)

        ev = asyncio.get_running_loop()

        if ext == "pdf":
            def parse_pdf() -> dict:
                from pypdf import PdfReader
                reader      = PdfReader(tmp)
                total_pages = len(reader.pages)
                if action == "metadata":
                    m = reader.metadata
                    return {
                        "status": True, "action": action,
                        "metadata": {
                            "title":       m.title  if m else None,
                            "author":      m.author if m else None,
                            "total_pages": total_pages,
                        },
                    }
                if page > 0:
                    if page > total_pages:
                        return {"status": False, "error": f"Page {page} melebihi total ({total_pages})"}
                    return {
                        "status": True, "action": action, "page": page,
                        "total_pages": total_pages,
                        "text": reader.pages[page - 1].extract_text(),
                    }
                return {
                    "status": True, "action": action, "total_pages": total_pages,
                    "pages": [{"page": i + 1, "text": p.extract_text()} for i, p in enumerate(reader.pages)],
                }
            return await ev.run_in_executor(None, parse_pdf)

        def parse_docx() -> dict:
            from docx import Document
            doc = Document(tmp)
            return {
                "status":     True,
                "action":     action,
                "paragraphs": [p.text for p in doc.paragraphs if p.text.strip()],
            }
        return await ev.run_in_executor(None, parse_docx)

    except httpx.HTTPStatusError as e:
        return JSONResponse({"status": False, "error": f"HTTP {e.response.status_code} saat download"})
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": "Timeout saat download file"})
    except ImportError as e:
        return JSONResponse({"status": False, "error": f"Library tidak tersedia: {e}. Pastikan pypdf dan python-docx ada di requirements.txt"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Image Processor ────────────────────────────────────────────────────────────
@app.get("/nova/image")
async def process_image(action: str, url: str, width: int = 0, height: int = 0, output_format: str = ""):
    tmp = f"/tmp/{uuid.uuid4().hex}.img"
    try:
        from PIL import Image

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            with open(tmp, "wb") as f:
                f.write(res.content)

        ev = asyncio.get_running_loop()

        if action == "info":
            def get_info() -> dict:
                img = Image.open(tmp)
                return {
                    "status": True, "action": action,
                    "width": img.width, "height": img.height,
                    "format": img.format, "mode": img.mode,
                }
            return await ev.run_in_executor(None, get_info)

        if action == "resize":
            def do_resize() -> tuple:
                img = Image.open(tmp)
                if width > 0 and height > 0:
                    img = img.resize((width, height), Image.Resampling.LANCZOS)
                elif width > 0:
                    img = img.resize((width, int(img.height * width / img.width)), Image.Resampling.LANCZOS)
                elif height > 0:
                    img = img.resize((int(img.width * height / img.height), height), Image.Resampling.LANCZOS)
                ext_out  = output_format or (img.format or "png").lower()
                out_path = f"/tmp/{uuid.uuid4().hex}.{ext_out}"
                try:
                    pil_fmt = PIL_FORMAT_MAP.get(ext_out.lower(), ext_out.upper())
                    if pil_fmt == "JPEG":
                        img = img.convert("RGB")
                    img.save(out_path, pil_fmt)
                    with open(out_path, "rb") as f:
                        return f.read(), ext_out
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)
            data, fmt = await ev.run_in_executor(None, do_resize)
            return Response(content=data, media_type=f"image/{fmt}")

        if action == "convert":
            if not output_format:
                return JSONResponse({"status": False, "error": "'output_format' wajib diisi"})
            def do_convert() -> bytes:
                img      = Image.open(tmp)
                out_path = f"/tmp/{uuid.uuid4().hex}.{output_format}"
                try:
                    pil_fmt = PIL_FORMAT_MAP.get(output_format.lower(), output_format.upper())
                    if pil_fmt == "JPEG":
                        img = img.convert("RGB")
                    img.save(out_path, pil_fmt)
                    with open(out_path, "rb") as f:
                        return f.read()
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)
            data = await ev.run_in_executor(None, do_convert)
            return Response(content=data, media_type=f"image/{output_format}")

        return JSONResponse({"status": False, "error": f"Action '{action}' tidak valid. Pilihan: info, resize, convert"})

    except httpx.HTTPStatusError as e:
        return JSONResponse({"status": False, "error": f"HTTP {e.response.status_code} saat download"})
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": "Timeout saat download gambar"})
    except ImportError:
        return JSONResponse({"status": False, "error": "Pillow tidak terinstall. Tambahkan Pillow ke requirements.txt"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Webhook Mini DB ────────────────────────────────────────────────────────────
def _nova_webhook_save(token: str, data: dict) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{token or 'default'}_{ts}_{uuid.uuid4().hex[:8]}.json"
    filepath = os.path.join(WEBHOOK_DATA_DIR, filename)
    with open(filepath, "w") as f:
        json.dump({"timestamp": ts, "token": token, "data": data}, f, indent=2)
    return filename


@app.post("/nova/webhook")
async def nova_webhook_post(request: Request, token: str = ""):
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"status": False, "error": f"JSON parse error: {e}"})
    try:
        ev       = asyncio.get_running_loop()
        filename = await ev.run_in_executor(None, _nova_webhook_save, token, data)
        return {"status": True, "message": "Webhook received", "method": "POST", "file": filename}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


@app.get("/nova/webhook")
async def nova_webhook_get(request: Request, token: str = "", show_list: int = 0):
    if show_list == 1:
        try:
            files = [f for f in os.listdir(WEBHOOK_DATA_DIR) if f.endswith(".json")]
            if token:
                files = [f for f in files if f.startswith(f"{token}_")]
            files.sort(reverse=True)
            return {"status": True, "webhooks": files, "count": len(files)}
        except Exception as e:
            return JSONResponse({"status": False, "error": str(e)})
    params = {k: v for k, v in request.query_params.items() if k not in ("token", "show_list")}
    try:
        ev       = asyncio.get_running_loop()
        filename = await ev.run_in_executor(None, _nova_webhook_save, token, params)
        return {"status": True, "message": "Webhook received", "method": "GET", "file": filename}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


async def _send_webhook(target_url: str, payload: dict, secret: str = "") -> dict:
    hdrs = {"Content-Type": "application/json", "User-Agent": "NovaWebhook/1.0"}
    if secret:
        hdrs["X-Nova-Secret"] = secret
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(target_url, json=payload, headers=hdrs)
            return {"success": res.is_success, "http_code": res.status_code, "response": res.text[:500]}
    except httpx.TimeoutException:
        return {"success": False, "http_code": None, "response": "Request timeout"}
    except httpx.RequestError as e:
        return {"success": False, "http_code": None, "response": f"{type(e).__name__}: {e}"}
    except Exception as e:
        return {"success": False, "http_code": None, "response": str(e)}


@app.post("/nova/webhook/send")
async def nova_webhook_send_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})
    target_url = (body.get("url") or "").strip()
    if not target_url or not target_url.startswith(("http://", "https://")):
        return JSONResponse({"status": False, "error": "'url' wajib diisi dan harus http/https"})
    payload    = body.get("data") or body.get("payload") or {}
    secret     = (body.get("secret") or "").strip()
    method     = (body.get("method") or "POST").upper()
    timeout    = int(body.get("timeout", 15))
    extra_hdrs = body.get("headers") or {}
    hdrs       = {"Content-Type": "application/json", "User-Agent": "NovaWebhook/1.0", **extra_hdrs}
    if secret:
        hdrs["X-Nova-Secret"] = secret
    try:
        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            if method == "GET":
                res = await client.get(target_url, params=payload if isinstance(payload, dict) else {}, headers=hdrs)
            else:
                res = await client.request(method, target_url, json=payload, headers=hdrs)
            return {
                "status": True, "url": target_url, "method": method,
                "http_code": res.status_code, "success": res.is_success,
                "response": res.text[:1000],
            }
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": f"Timeout setelah {timeout}s"})
    except httpx.RequestError as e:
        return JSONResponse({"status": False, "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


@app.get("/nova/webhook/send")
async def nova_webhook_send_get(
    url:     str,
    data:    str = "",
    secret:  str = "",
    method:  str = "POST",
    timeout: int = 15,
):
    if not url or not url.startswith(("http://", "https://")):
        return JSONResponse({"status": False, "error": "'url' wajib diisi dan harus http/https"})
    payload: dict = {}
    if data:
        try:
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                return JSONResponse({"status": False, "error": "'data' harus JSON object"})
            payload = parsed
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "'data' bukan JSON yang valid"})
    hdrs = {"Content-Type": "application/json", "User-Agent": "NovaWebhook/1.0"}
    if secret:
        hdrs["X-Nova-Secret"] = secret
    method = method.upper()
    try:
        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            if method == "GET":
                res = await client.get(url, params=payload, headers=hdrs)
            else:
                res = await client.request(method, url, json=payload, headers=hdrs)
            return {
                "status": True, "url": url, "method": method,
                "http_code": res.status_code, "success": res.is_success,
                "response": res.text[:1000],
            }
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": f"Timeout setelah {timeout}s"})
    except httpx.RequestError as e:
        return JSONResponse({"status": False, "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


# ── QR Generator ───────────────────────────────────────────────────────────────
@app.get("/nova/qr")
async def generate_qr(data: str, size: int = 10):
    try:
        import qrcode
        ev = asyncio.get_running_loop()
        def make_qr() -> bytes:
            qr = qrcode.QRCode(version=1, box_size=max(1, min(size, 20)), border=4)
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.getvalue()
        img_bytes = await ev.run_in_executor(None, make_qr)
        return Response(content=img_bytes, media_type="image/png")
    except ImportError:
        return JSONResponse({"status": False, "error": "qrcode tidak terinstall. Tambahkan qrcode[pil] ke requirements.txt"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


# ── GitHub Helper ──────────────────────────────────────────────────────────────
def _gh_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    hdrs = {
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":           "NovaGit/1.0",
    }
    if token:
        hdrs["Authorization"] = f"token {token}"
    else:
        hdrs["X-Nova-Warning"] = "GITHUB_TOKEN env tidak diset, rate limit sangat terbatas"
    return hdrs


async def _gh(
    method:  str,
    path:    str,
    body:    Optional[dict] = None,
    params:  Optional[dict] = None,
    timeout: int = 15,
) -> dict:
    url = f"{_GH_BASE}{path}"
    if not os.environ.get("GITHUB_TOKEN"):
        return {
            "status": False,
            "error":  "GITHUB_TOKEN tidak diset di environment variables. Tambahkan di Vercel project settings.",
        }
    try:
        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            kwargs: dict = {"headers": _gh_headers()}
            if params:
                kwargs["params"] = {k: v for k, v in params.items() if v is not None}
            if body and method.upper() != "GET":
                kwargs["json"] = body
            res = await client.request(method.upper(), url, **kwargs)
            try:
                data = res.json()
            except Exception:
                data = {"raw": res.text[:2000]}
            if res.is_success:
                return {"status": True,  "http_code": res.status_code, "data": data}
            return     {"status": False, "http_code": res.status_code,
                        "error": data.get("message", str(data))[:500], "detail": data}
    except httpx.TimeoutException:
        return {"status": False, "error": f"Timeout setelah {timeout}s"}
    except Exception as e:
        return {"status": False, "error": f"{type(e).__name__}: {e}"}


def _gh_parse_body(body: dict, required: list) -> Optional[str]:
    for f in required:
        if not body.get(f):
            return f"'{f}' wajib diisi"
    return None


# ── /git/user ──────────────────────────────────────────────────────────────────
@app.get("/git/user")
async def git_user_get():
    return await _gh("GET", "/user")

@app.post("/git/user")
async def git_user_post():
    return await _gh("GET", "/user")


# ── /git/repos ─────────────────────────────────────────────────────────────────
@app.get("/git/repos")
async def git_list_repos_get(
    type:     str = "all",
    sort:     str = "updated",
    per_page: int = 30,
    page:     int = 1,
):
    return await _gh("GET", "/user/repos", params={
        "type": type, "sort": sort,
        "per_page": min(per_page, 100), "page": page,
    })

@app.post("/git/repos")
async def git_list_repos_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    owner = (body.get("owner") or "").strip()
    if owner:
        return await _gh("GET", f"/users/{owner}/repos", params={
            "type":     body.get("type", "all"),
            "sort":     body.get("sort", "updated"),
            "per_page": min(int(body.get("per_page", 30)), 100),
            "page":     int(body.get("page", 1)),
        })
    return await _gh("GET", "/user/repos", params={
        "type":     body.get("type", "all"),
        "sort":     body.get("sort", "updated"),
        "per_page": min(int(body.get("per_page", 30)), 100),
        "page":     int(body.get("page", 1)),
    })

@app.get("/git/repos/{owner}")
async def git_list_user_repos_get(
    owner:    str,
    type:     str = "all",
    sort:     str = "updated",
    per_page: int = 30,
    page:     int = 1,
):
    return await _gh("GET", f"/users/{owner}/repos", params={
        "type": type, "sort": sort,
        "per_page": min(per_page, 100), "page": page,
    })

@app.post("/git/repos/{owner}")
async def git_list_user_repos_post(owner: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await _gh("GET", f"/users/{owner}/repos", params={
        "type":     body.get("type", "all"),
        "sort":     body.get("sort", "updated"),
        "per_page": min(int(body.get("per_page", 30)), 100),
        "page":     int(body.get("page", 1)),
    })


# ── /git/repo ──────────────────────────────────────────────────────────────────
@app.get("/git/repo")
async def git_repo_info_get(owner: str, repo: str):
    return await _gh("GET", f"/repos/{owner}/{repo}")

@app.post("/git/repo")
async def git_repo_info_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}")

@app.post("/git/repo/create")
async def git_repo_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("name"):
        return JSONResponse({"status": False, "error": "'name' wajib diisi"})
    payload = {
        "name":        body["name"],
        "description": body.get("description", ""),
        "private":     bool(body.get("private", False)),
        "auto_init":   bool(body.get("auto_init", False)),
    }
    return await _gh("POST", "/user/repos", body=payload)

@app.post("/git/repo/edit")
async def git_repo_edit(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    allowed = {"name","description","private","homepage","has_issues",
               "has_projects","has_wiki","default_branch","archived","visibility"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if not payload:
        return JSONResponse({"status": False, "error": "Tidak ada field valid yang mau diedit"})
    return await _gh("PATCH", f"/repos/{body['owner']}/{body['repo']}", body=payload)

@app.post("/git/repo/delete")
async def git_repo_delete(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    result = await _gh("DELETE", f"/repos/{body['owner']}/{body['repo']}")
    if result.get("http_code") == 204:
        return {"status": True, "message": f"Repo '{body['owner']}/{body['repo']}' berhasil dihapus"}
    return result

@app.post("/git/repo/fork")
async def git_repo_fork(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload = {}
    if body.get("organization"):
        payload["organization"] = body["organization"]
    return await _gh("POST", f"/repos/{body['owner']}/{body['repo']}/forks", body=payload)


# ── /git/file ──────────────────────────────────────────────────────────────────
@app.get("/git/file")
async def git_file_get_endpoint(owner: str, repo: str, path: str, ref: str = ""):
    if not path:
        return JSONResponse({"status": False, "error": "'path' wajib diisi"})
    params = {"ref": ref} if ref else {}
    result = await _gh("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)
    if result.get("status") and isinstance(result.get("data"), dict):
        d = result["data"]
        if d.get("encoding") == "base64" and d.get("content"):
            try:
                result["decoded"] = base64.b64decode(d["content"]).decode("utf-8", errors="replace")
            except Exception:
                pass
    return result

@app.post("/git/file")
async def git_file_post_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "path"])
    if err:
        return JSONResponse({"status": False, "error": err})
    params = {"ref": body["ref"]} if body.get("ref") else {}
    result = await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/contents/{body['path']}", params=params)
    if result.get("status") and isinstance(result.get("data"), dict):
        d = result["data"]
        if d.get("encoding") == "base64" and d.get("content"):
            try:
                result["decoded"] = base64.b64decode(d["content"]).decode("utf-8", errors="replace")
            except Exception:
                pass
    return result

@app.get("/git/file/list")
async def git_file_list_get(owner: str, repo: str, path: str = "", ref: str = ""):
    params = {"ref": ref} if ref else {}
    return await _gh("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)

@app.post("/git/file/list")
async def git_file_list_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    params = {"ref": body["ref"]} if body.get("ref") else {}
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/contents/{body.get('path', '')}", params=params)

@app.post("/git/file/create")
async def git_file_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "path", "content", "message"])
    if err:
        return JSONResponse({"status": False, "error": err})
    encoded  = base64.b64encode(body["content"].encode()).decode()
    payload: dict = {"message": body["message"], "content": encoded}
    if body.get("branch"):
        payload["branch"] = body["branch"]
    return await _gh("PUT", f"/repos/{body['owner']}/{body['repo']}/contents/{body['path']}", body=payload)

@app.post("/git/file/edit")
async def git_file_edit(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "path", "content", "message", "sha"])
    if err:
        return JSONResponse({"status": False, "error": err})
    encoded  = base64.b64encode(body["content"].encode()).decode()
    payload: dict = {"message": body["message"], "content": encoded, "sha": body["sha"]}
    if body.get("branch"):
        payload["branch"] = body["branch"]
    return await _gh("PUT", f"/repos/{body['owner']}/{body['repo']}/contents/{body['path']}", body=payload)

@app.post("/git/file/delete")
async def git_file_delete(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "path", "message", "sha"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload: dict = {"message": body["message"], "sha": body["sha"]}
    if body.get("branch"):
        payload["branch"] = body["branch"]
    return await _gh("DELETE", f"/repos/{body['owner']}/{body['repo']}/contents/{body['path']}", body=payload)


# ── /git/commits ───────────────────────────────────────────────────────────────
@app.get("/git/commits")
async def git_commits_get(
    owner:    str,
    repo:     str,
    branch:   str = "",
    path:     str = "",
    per_page: int = 20,
    page:     int = 1,
):
    return await _gh("GET", f"/repos/{owner}/{repo}/commits", params={
        "sha":      branch or None,
        "path":     path   or None,
        "per_page": min(per_page, 100),
        "page":     page,
    })

@app.post("/git/commits")
async def git_commits_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/commits", params={
        "sha":      body.get("branch") or None,
        "path":     body.get("path")   or None,
        "per_page": min(int(body.get("per_page", 20)), 100),
        "page":     int(body.get("page", 1)),
    })

@app.get("/git/commit")
async def git_commit_detail_get(owner: str, repo: str, sha: str):
    return await _gh("GET", f"/repos/{owner}/{repo}/commits/{sha}")

@app.post("/git/commit")
async def git_commit_detail_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "sha"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/commits/{body['sha']}")


# ── /git/branches ──────────────────────────────────────────────────────────────
@app.get("/git/branches")
async def git_branches_get(owner: str, repo: str, per_page: int = 30):
    return await _gh("GET", f"/repos/{owner}/{repo}/branches",
                     params={"per_page": min(per_page, 100)})

@app.post("/git/branches")
async def git_branches_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/branches",
                     params={"per_page": min(int(body.get("per_page", 30)), 100)})

@app.post("/git/branch/create")
async def git_branch_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "branch"])
    if err:
        return JSONResponse({"status": False, "error": err})
    from_branch = body.get("from_branch", "")
    if from_branch:
        ref_result = await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/git/ref/heads/{from_branch}")
    else:
        repo_info  = await _gh("GET", f"/repos/{body['owner']}/{body['repo']}")
        default_br = (repo_info.get("data") or {}).get("default_branch", "main")
        ref_result = await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/git/ref/heads/{default_br}")
    if not ref_result.get("status"):
        return JSONResponse({"status": False, "error": "Gagal ambil SHA branch sumber", "detail": ref_result})
    sha = ref_result["data"]["object"]["sha"]
    return await _gh("POST", f"/repos/{body['owner']}/{body['repo']}/git/refs", body={
        "ref": f"refs/heads/{body['branch']}", "sha": sha,
    })

@app.post("/git/branch/delete")
async def git_branch_delete(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "branch"])
    if err:
        return JSONResponse({"status": False, "error": err})
    result = await _gh("DELETE", f"/repos/{body['owner']}/{body['repo']}/git/refs/heads/{body['branch']}")
    if result.get("http_code") == 204:
        return {"status": True, "message": f"Branch '{body['branch']}' berhasil dihapus"}
    return result


# ── /git/tags ──────────────────────────────────────────────────────────────────
@app.get("/git/tags")
async def git_tags_get(owner: str, repo: str, per_page: int = 30):
    return await _gh("GET", f"/repos/{owner}/{repo}/tags",
                     params={"per_page": min(per_page, 100)})

@app.post("/git/tags")
async def git_tags_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/tags",
                     params={"per_page": min(int(body.get("per_page", 30)), 100)})


# ── /git/issues ────────────────────────────────────────────────────────────────
@app.get("/git/issues")
async def git_issues_get(
    owner:    str,
    repo:     str,
    state:    str = "open",
    per_page: int = 20,
    page:     int = 1,
):
    return await _gh("GET", f"/repos/{owner}/{repo}/issues", params={
        "state": state, "per_page": min(per_page, 100), "page": page,
    })

@app.post("/git/issues")
async def git_issues_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/issues", params={
        "state":    body.get("state", "open"),
        "per_page": min(int(body.get("per_page", 20)), 100),
        "page":     int(body.get("page", 1)),
    })

@app.get("/git/issue")
async def git_issue_detail_get(owner: str, repo: str, number: int):
    return await _gh("GET", f"/repos/{owner}/{repo}/issues/{number}")

@app.post("/git/issue")
async def git_issue_detail_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "number"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/issues/{body['number']}")

@app.post("/git/issue/create")
async def git_issue_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "title"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload: dict = {"title": body["title"]}
    if body.get("body"):       payload["body"]      = body["body"]
    if body.get("labels"):     payload["labels"]    = body["labels"]
    if body.get("assignees"):  payload["assignees"] = body["assignees"]
    return await _gh("POST", f"/repos/{body['owner']}/{body['repo']}/issues", body=payload)

@app.post("/git/issue/edit")
async def git_issue_edit(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "number"])
    if err:
        return JSONResponse({"status": False, "error": err})
    allowed  = {"title","body","state","labels","assignees","milestone"}
    payload  = {k: v for k, v in body.items() if k in allowed}
    if not payload:
        return JSONResponse({"status": False, "error": "Tidak ada field valid"})
    return await _gh("PATCH", f"/repos/{body['owner']}/{body['repo']}/issues/{body['number']}", body=payload)

@app.post("/git/issue/comment")
async def git_issue_comment(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "number", "body"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("POST", f"/repos/{body['owner']}/{body['repo']}/issues/{body['number']}/comments",
                     body={"body": body["body"]})


# ── /git/pulls ─────────────────────────────────────────────────────────────────
@app.get("/git/pulls")
async def git_pulls_get(
    owner:    str,
    repo:     str,
    state:    str = "open",
    per_page: int = 20,
    page:     int = 1,
):
    return await _gh("GET", f"/repos/{owner}/{repo}/pulls", params={
        "state": state, "per_page": min(per_page, 100), "page": page,
    })

@app.post("/git/pulls")
async def git_pulls_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/pulls", params={
        "state":    body.get("state", "open"),
        "per_page": min(int(body.get("per_page", 20)), 100),
        "page":     int(body.get("page", 1)),
    })

@app.post("/git/pull/create")
async def git_pull_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "title", "head", "base"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload: dict = {"title": body["title"], "head": body["head"], "base": body["base"]}
    if body.get("body"): payload["body"] = body["body"]
    return await _gh("POST", f"/repos/{body['owner']}/{body['repo']}/pulls", body=payload)

@app.post("/git/pull/merge")
async def git_pull_merge(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "number"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload: dict = {"merge_method": body.get("merge_method", "merge")}
    if body.get("commit_message"): payload["commit_message"] = body["commit_message"]
    return await _gh("PUT", f"/repos/{body['owner']}/{body['repo']}/pulls/{body['number']}/merge", body=payload)


# ── /git/releases ──────────────────────────────────────────────────────────────
@app.get("/git/releases")
async def git_releases_get(owner: str, repo: str, per_page: int = 10):
    return await _gh("GET", f"/repos/{owner}/{repo}/releases",
                     params={"per_page": min(per_page, 100)})

@app.post("/git/releases")
async def git_releases_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/releases",
                     params={"per_page": min(int(body.get("per_page", 10)), 100)})

@app.get("/git/release/latest")
async def git_release_latest_get(owner: str, repo: str):
    return await _gh("GET", f"/repos/{owner}/{repo}/releases/latest")

@app.post("/git/release/latest")
async def git_release_latest_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/releases/latest")

@app.post("/git/release/create")
async def git_release_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "tag_name"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload: dict = {"tag_name": body["tag_name"]}
    for k in ("name","body","target_commitish"):
        if body.get(k): payload[k] = body[k]
    payload["draft"]      = bool(body.get("draft", False))
    payload["prerelease"] = bool(body.get("prerelease", False))
    return await _gh("POST", f"/repos/{body['owner']}/{body['repo']}/releases", body=payload)


# ── /git/search ────────────────────────────────────────────────────────────────
@app.get("/git/search/repos")
async def git_search_repos_get(q: str, sort: str = "stars", order: str = "desc", per_page: int = 10):
    if not q:
        return JSONResponse({"status": False, "error": "'q' wajib diisi"})
    return await _gh("GET", "/search/repositories", params={
        "q": q, "sort": sort, "order": order, "per_page": min(per_page, 30),
    })

@app.post("/git/search/repos")
async def git_search_repos_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("q"):
        return JSONResponse({"status": False, "error": "'q' wajib diisi"})
    return await _gh("GET", "/search/repositories", params={
        "q":        body["q"],
        "sort":     body.get("sort", "stars"),
        "order":    body.get("order", "desc"),
        "per_page": min(int(body.get("per_page", 10)), 30),
    })

@app.get("/git/search/code")
async def git_search_code_get(q: str, per_page: int = 10):
    if not q:
        return JSONResponse({"status": False, "error": "'q' wajib diisi"})
    return await _gh("GET", "/search/code", params={"q": q, "per_page": min(per_page, 30)})

@app.post("/git/search/code")
async def git_search_code_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("q"):
        return JSONResponse({"status": False, "error": "'q' wajib diisi"})
    return await _gh("GET", "/search/code", params={
        "q": body["q"], "per_page": min(int(body.get("per_page", 10)), 30),
    })

@app.get("/git/search/issues")
async def git_search_issues_get(q: str, sort: str = "created", per_page: int = 10):
    if not q:
        return JSONResponse({"status": False, "error": "'q' wajib diisi"})
    return await _gh("GET", "/search/issues", params={
        "q": q, "sort": sort, "per_page": min(per_page, 30),
    })

@app.post("/git/search/issues")
async def git_search_issues_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("q"):
        return JSONResponse({"status": False, "error": "'q' wajib diisi"})
    return await _gh("GET", "/search/issues", params={
        "q":        body["q"],
        "sort":     body.get("sort", "created"),
        "per_page": min(int(body.get("per_page", 10)), 30),
    })


# ── /git/actions ───────────────────────────────────────────────────────────────
@app.get("/git/actions/runs")
async def git_actions_runs_get(owner: str, repo: str, per_page: int = 10):
    return await _gh("GET", f"/repos/{owner}/{repo}/actions/runs",
                     params={"per_page": min(per_page, 100)})

@app.post("/git/actions/runs")
async def git_actions_runs_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/actions/runs",
                     params={"per_page": min(int(body.get("per_page", 10)), 100)})

@app.post("/git/actions/trigger")
async def git_actions_trigger(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "workflow_id", "ref"])
    if err:
        return JSONResponse({"status": False, "error": err})
    payload: dict = {"ref": body["ref"]}
    if body.get("inputs"): payload["inputs"] = body["inputs"]
    result = await _gh("POST",
        f"/repos/{body['owner']}/{body['repo']}/actions/workflows/{body['workflow_id']}/dispatches",
        body=payload)
    if result.get("http_code") == 204:
        return {"status": True, "message": "Workflow berhasil di-trigger"}
    return result


# ── /git/stars ─────────────────────────────────────────────────────────────────
@app.get("/git/stars")
async def git_stars_get(owner: str = "", repo: str = "", per_page: int = 30):
    if owner and repo:
        return await _gh("GET", f"/repos/{owner}/{repo}/stargazers",
                         params={"per_page": min(per_page, 100)})
    return await _gh("GET", "/user/starred", params={"per_page": min(per_page, 100)})

@app.post("/git/stars")
async def git_stars_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    owner = (body.get("owner") or "").strip()
    repo  = (body.get("repo")  or "").strip()
    if owner and repo:
        return await _gh("GET", f"/repos/{owner}/{repo}/stargazers",
                         params={"per_page": min(int(body.get("per_page", 30)), 100)})
    return await _gh("GET", "/user/starred",
                     params={"per_page": min(int(body.get("per_page", 30)), 100)})

@app.post("/git/star")
async def git_star(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    act    = (body.get("action") or "star").lower()
    method = "PUT" if act == "star" else "DELETE"
    result = await _gh(method, f"/user/starred/{body['owner']}/{body['repo']}")
    if result.get("http_code") in (204, 304):
        return {"status": True, "message": f"Repo berhasil di-{'star' if act == 'star' else 'unstar'}"}
    return result


# ── /git/collaborators ─────────────────────────────────────────────────────────
@app.get("/git/collaborators")
async def git_collaborators_get(owner: str, repo: str, per_page: int = 30):
    return await _gh("GET", f"/repos/{owner}/{repo}/collaborators",
                     params={"per_page": min(per_page, 100)})

@app.post("/git/collaborators")
async def git_collaborators_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo"])
    if err:
        return JSONResponse({"status": False, "error": err})
    return await _gh("GET", f"/repos/{body['owner']}/{body['repo']}/collaborators",
                     params={"per_page": min(int(body.get("per_page", 30)), 100)})

@app.post("/git/collaborator/add")
async def git_collaborator_add(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "username"])
    if err:
        return JSONResponse({"status": False, "error": err})
    result = await _gh("PUT",
        f"/repos/{body['owner']}/{body['repo']}/collaborators/{body['username']}",
        body={"permission": body.get("permission", "push")})
    if result.get("http_code") in (201, 204):
        return {"status": True, "message": f"'{body['username']}' berhasil ditambahkan"}
    return result

@app.post("/git/collaborator/remove")
async def git_collaborator_remove(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    err = _gh_parse_body(body, ["owner", "repo", "username"])
    if err:
        return JSONResponse({"status": False, "error": err})
    result = await _gh("DELETE",
        f"/repos/{body['owner']}/{body['repo']}/collaborators/{body['username']}")
    if result.get("http_code") == 204:
        return {"status": True, "message": f"'{body['username']}' berhasil dihapus"}
    return result


# ── /git/gists ─────────────────────────────────────────────────────────────────
@app.get("/git/gists")
async def git_gists_get(per_page: int = 20):
    return await _gh("GET", "/gists", params={"per_page": min(per_page, 100)})

@app.post("/git/gists")
async def git_gists_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await _gh("GET", "/gists",
                     params={"per_page": min(int(body.get("per_page", 20)), 100)})

@app.post("/git/gist/create")
async def git_gist_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("files"):
        return JSONResponse({"status": False, "error": "'files' wajib diisi"})
    payload = {
        "description": body.get("description", ""),
        "public":      bool(body.get("public", False)),
        "files":       body["files"],
    }
    return await _gh("POST", "/gists", body=payload)

@app.post("/git/gist/edit")
async def git_gist_edit(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("gist_id"):
        return JSONResponse({"status": False, "error": "'gist_id' wajib diisi"})
    payload = {}
    if body.get("description"): payload["description"] = body["description"]
    if body.get("files"):       payload["files"]       = body["files"]
    return await _gh("PATCH", f"/gists/{body['gist_id']}", body=payload)

@app.post("/git/gist/delete")
async def git_gist_delete(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON valid"})
    if not body.get("gist_id"):
        return JSONResponse({"status": False, "error": "'gist_id' wajib diisi"})
    result = await _gh("DELETE", f"/gists/{body['gist_id']}")
    if result.get("http_code") == 204:
        return {"status": True, "message": "Gist berhasil dihapus"}
    return result


# ── Webhook Management (/wh/) ──────────────────────────────────────────────────
def _wh_load() -> dict:
    global _webhook_cache, _webhook_cache_time
    now = time.monotonic()
    with _webhook_cfg_lock:
        if _webhook_cache and (now - _webhook_cache_time) < _WEBHOOK_CACHE_TTL:
            return dict(_webhook_cache)
        if not os.path.exists(WEBHOOKS_CONFIG_PATH):
            _wh_write_locked({})
            return {}
        try:
            with open(WEBHOOKS_CONFIG_PATH, "r") as f:
                data = json.load(f)
            _webhook_cache      = data
            _webhook_cache_time = now
            return dict(data)
        except (json.JSONDecodeError, OSError):
            _wh_write_locked({})
            return {}


def _wh_save(configs: dict) -> None:
    with _webhook_cfg_lock:
        _wh_write_locked(configs)


def _wh_write_locked(configs: dict) -> None:
    global _webhook_cache, _webhook_cache_time
    with open(WEBHOOKS_CONFIG_PATH, "w") as f:
        json.dump(configs, f, indent=2)
    _webhook_cache      = dict(configs)
    _webhook_cache_time = time.monotonic()


def _wh_create(target_url: str, event_name: str, secret: str) -> dict:
    if not target_url or not target_url.startswith(("http://", "https://")):
        return {"status": False, "error": "'target_url' wajib diisi dan harus http/https"}
    if not event_name:
        return {"status": False, "error": "'event_name' wajib diisi"}
    wh_id = uuid.uuid4().hex
    wh    = {
        "id":               wh_id,
        "target_url":       target_url,
        "event_name":       event_name,
        "secret":           secret,
        "created_at":       _now(),
        "trigger_count":    0,
        "last_triggered_at": None,
    }
    configs = _wh_load()
    configs[wh_id] = wh
    _wh_save(configs)
    return {"status": True, "message": "Webhook berhasil didaftarkan", "webhook": wh}


def _wh_delete(wh_id: str) -> dict:
    if not wh_id:
        return {"status": False, "error": "'id' wajib diisi"}
    configs = _wh_load()
    if wh_id not in configs:
        return {"status": False, "error": f"Webhook '{wh_id}' tidak ditemukan"}
    deleted = configs.pop(wh_id)
    _wh_save(configs)
    return {"status": True, "message": f"Webhook '{wh_id}' dihapus", "deleted": deleted}


async def _wh_trigger(wh_id: str, extra_payload: dict) -> dict:
    if not wh_id:
        return {"status": False, "error": "'id' wajib diisi"}
    if not isinstance(extra_payload, dict):
        return {"status": False, "error": "'payload' harus berupa object"}
    ev      = asyncio.get_running_loop()
    configs = await ev.run_in_executor(None, _wh_load)
    if wh_id not in configs:
        return {"status": False, "error": f"Webhook '{wh_id}' tidak ditemukan"}
    wh           = configs[wh_id]
    triggered_at = _now()
    payload      = {
        "event":        wh["event_name"],
        "webhook_id":   wh_id,
        "triggered_at": triggered_at,
        "data":         extra_payload,
    }
    delivery = await _send_webhook(wh["target_url"], payload, wh.get("secret", ""))
    wh["trigger_count"]      = wh.get("trigger_count", 0) + 1
    wh["last_triggered_at"]  = triggered_at
    configs[wh_id]           = wh
    try:
        await ev.run_in_executor(None, _wh_save, configs)
    except Exception as e:
        delivery["storage_warning"] = str(e)
    return {
        "status":        True,
        "webhook_id":    wh_id,
        "target_url":    wh["target_url"],
        "event_name":    wh["event_name"],
        "triggered_at":  triggered_at,
        "trigger_count": wh["trigger_count"],
        "delivery":      delivery,
        "payload_sent":  payload,
    }


@app.get("/wh/create")
async def wh_create_get(target_url: str = "", event_name: str = "", secret: str = ""):
    ev = asyncio.get_running_loop()
    return await ev.run_in_executor(None, _wh_create, target_url.strip(), event_name.strip(), secret.strip())

@app.post("/wh/create")
async def wh_create_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})
    ev = asyncio.get_running_loop()
    return await ev.run_in_executor(
        None, _wh_create,
        (body.get("target_url") or "").strip(),
        (body.get("event_name") or "").strip(),
        (body.get("secret") or "").strip(),
    )

@app.get("/wh/list")
@app.get("/api/webhook/list")
async def wh_list(event_name: str = ""):
    ev      = asyncio.get_running_loop()
    configs = await ev.run_in_executor(None, _wh_load)
    whs     = list(configs.values())
    if event_name:
        whs = [w for w in whs if w.get("event_name") == event_name]
    whs.sort(key=lambda w: w.get("created_at", ""), reverse=True)
    return {"status": True, "count": len(whs), "webhooks": whs}

@app.get("/wh/delete")
async def wh_delete_get(id: str = ""):
    ev = asyncio.get_running_loop()
    return await ev.run_in_executor(None, _wh_delete, id.strip())

@app.post("/wh/delete")
async def wh_delete_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})
    ev = asyncio.get_running_loop()
    return await ev.run_in_executor(None, _wh_delete, (body.get("id") or "").strip())

@app.get("/wh/trigger")
async def wh_trigger_get(id: str = "", payload: str = ""):
    extra: dict = {}
    if payload:
        try:
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                return JSONResponse({"status": False, "error": "'payload' harus JSON object"})
            extra = parsed
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "'payload' bukan JSON yang valid"})
    return await _wh_trigger(id.strip(), extra)

@app.post("/wh/trigger")
async def wh_trigger_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})
    return await _wh_trigger((body.get("id") or "").strip(), body.get("payload") or {})


# ── Search Endpoint ────────────────────────────────────────────────────────────

async def _search_fetch_ddg(query: str) -> list[str]:
    search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    hdrs = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            res  = await client.get(search_url, headers=hdrs)
            html = res.text
            seen: set  = set()
            urls: list = []
            for raw in re.findall(r'uddg=(https?[^&"\'>\s]+)', html, re.I):
                try:
                    u = unquote(raw).strip().rstrip("/")
                except Exception:
                    continue
                if u and u not in seen and "duckduckgo.com" not in u:
                    seen.add(u)
                    urls.append(u)
                if len(urls) >= 25:
                    break
            if not urls:
                for u in re.findall(r'href=["\']?(https?://(?!duckduckgo\.com)[^"\'>\s]+)', html, re.I):
                    u = u.strip().rstrip("/")
                    if u and u not in seen:
                        seen.add(u)
                        urls.append(u)
                    if len(urls) >= 25:
                        break
            return urls[:25]
    except Exception:
        return []


async def _search_fetch_page(url: str) -> dict:
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=7.0,
            follow_redirects=True,
            max_redirects=3,
        ) as client:
            try:
                res = await client.get(url, headers=hdrs)
            except (httpx.TimeoutException, httpx.TooManyRedirects, httpx.RequestError):
                return {"url": url, "title": "", "text": "", "ok": False}

            if not res.is_success:
                return {"url": url, "title": "", "text": "", "ok": False}

            ctype = res.headers.get("content-type", "")
            if "text/html" not in ctype and "xml" not in ctype:
                return {"url": url, "title": "", "text": "", "ok": False}

            html = res.text

            title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            title   = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""

            clean = re.sub(r"<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>",
                           " ", html, flags=re.I | re.S)
            clean = re.sub(r"<(br|p|div|li|h[1-6]|tr|td|th)[^>]*>", " ", clean, flags=re.I)
            clean = re.sub(r"<[^>]+>", "", clean)
            clean = re.sub(r"[ \t]+", " ", clean)
            clean = re.sub(r"\n\s*\n+", "\n", clean).strip()

            return {"url": url, "title": title, "text": clean[:8000], "ok": True}

    except Exception:
        return {"url": url, "title": "", "text": "", "ok": False}


def _search_score(query: str, title: str, text: str) -> float:
    try:
        keywords    = [w.lower() for w in re.split(r"\W+", query) if len(w) > 1]
        if not keywords:
            return 0.0
        title_lower = title.lower()
        text_lower  = text.lower()
        score       = sum(title_lower.count(k) * 3.0 + min(text_lower.count(k), 20) for k in keywords)
        if all(k in title_lower for k in keywords):
            score += 10.0
        score += math.log1p(max(len(text_lower), 1)) * 0.05
        return score
    except Exception:
        return 0.0


def _search_summary(text: str, query: str, max_chars: int = 1000) -> str:
    try:
        if not text:
            return ""
        keywords  = [w.lower() for w in re.split(r"\W+", query) if len(w) > 1]
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if len(s.strip()) > 30]
        if not sentences:
            return text[:max_chars]
        scored = []
        for idx, sent in enumerate(sentences):
            sl   = sent.lower()
            hits = sum(sl.count(k) for k in keywords)
            scored.append((hits + max(0, 5 - idx) * 0.3, idx, sent))
        scored.sort(key=lambda x: (-x[0], x[1]))
        selected: list = []
        total = 0
        for _, idx, sent in scored:
            if total + len(sent) + 1 > max_chars:
                rem = max_chars - total - 1
                if rem > 40:
                    selected.append((idx, sent[:rem].rsplit(" ", 1)[0] + "…"))
                break
            selected.append((idx, sent))
            total += len(sent) + 1
        selected.sort(key=lambda x: x[0])
        result = " ".join(s for _, s in selected).strip()
        return result[:max_chars] if result else text[:max_chars]
    except Exception:
        return text[:max_chars] if text else ""


async def _search_core(query: str) -> dict:
    try:
        empty = {
            "query":              query,
            "search_url":         [],
            "search_recommended": [],
            "search_data":        [],
        }

        search_urls = await _search_fetch_ddg(query)
        if not search_urls:
            return empty

        pages_raw = await asyncio.gather(
            *[_search_fetch_page(u) for u in search_urls],
            return_exceptions=True,
        )
        pages: list[dict] = [
            p if isinstance(p, dict) else {"url": "", "title": "", "text": "", "ok": False}
            for p in pages_raw
        ]

        scored: list[tuple[float, dict]] = []
        for page in pages:
            if page.get("ok") and page.get("url"):
                scored.append((_search_score(query, page.get("title",""), page.get("text","")), page))
            else:
                scored.append((0.0, page))
        scored.sort(key=lambda x: -x[0])

        url_to_idx = {u: i + 1 for i, u in enumerate(search_urls)}
        recommended: list[int]  = []
        search_data: list[dict] = []

        for _, page in scored:
            if len(recommended) >= 5:
                break
            url = page.get("url", "")
            if not url or not page.get("ok"):
                continue
            idx = url_to_idx.get(url)
            if idx is None:
                continue
            recommended.append(idx)
            search_data.append({
                "url":     url,
                "title":   page.get("title") or url,
                "summary": _search_summary(page.get("text", ""), query),
            })

        return {
            "query":              query,
            "search_url":         search_urls,
            "search_recommended": recommended,
            "search_data":        search_data,
        }
    except Exception:
        return {
            "query":              query,
            "search_url":         [],
            "search_recommended": [],
            "search_data":        [],
        }


@app.get("/api/search")
async def search_get(summary: str = ""):
    query = summary.strip()
    if not query:
        return JSONResponse({
            "status":             False,
            "error":              "Parameter 'summary' wajib diisi",
            "query":              "",
            "search_url":         [],
            "search_recommended": [],
            "search_data":        [],
        })
    return await _search_core(query)


@app.post("/api/search")
async def search_post(request: Request):
    try:
        body  = await request.json()
        query = (body.get("summary") or body.get("query") or "").strip()
    except Exception:
        query = ""
    if not query:
        return JSONResponse({
            "status":             False,
            "error":              "Field 'summary' atau 'query' wajib diisi",
            "query":              "",
            "search_url":         [],
            "search_recommended": [],
            "search_data":        [],
        })
    return await _search_core(query)


# ── Multiple Endpoint ──────────────────────────────────────────────────────────

_MULTIPLE_MAX_TASKS = 25


async def _run_single_task(
    task_id:  int,
    endpoint: str,
    params:   dict,
    method:   str,
) -> dict:
    t0 = time.monotonic()
    try:
        if not endpoint or not endpoint.startswith("/"):
            return {
                "task_id":    task_id,
                "endpoint":   endpoint or "",
                "status":     False,
                "error":      "Endpoint tidak valid, harus diawali dengan '/'",
                "result":     None,
                "duration_ms": 0,
            }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=30.0,
        ) as client:
            if method == "POST":
                resp = await client.post(endpoint, json=params or {})
            else:
                if params:
                    resp = await client.get(endpoint, params=params)
                else:
                    resp = await client.get(endpoint)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)

        try:
            result_data = resp.json()
        except Exception:
            result_data = {"raw": resp.text[:2000]}

        task_status = result_data.get("status") if isinstance(result_data, dict) else None
        if not isinstance(task_status, bool):
            task_status = resp.is_success

        return {
            "task_id":    task_id,
            "endpoint":   endpoint,
            "status":     task_status,
            "http_code":  resp.status_code,
            "result":     result_data,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        return {
            "task_id":    task_id,
            "endpoint":   endpoint,
            "status":     False,
            "error":      f"{type(e).__name__}: {e}",
            "result":     None,
            "duration_ms": round((time.monotonic() - t0) * 1000, 2),
        }


def _multiple_build_summary(results: list) -> tuple:
    success = sum(1 for r in results if r.get("status") is True)
    failed  = len(results) - success
    return success, failed


@app.get("/api/multiple")
async def multiple_get(tasks: str = ""):
    t_start = time.monotonic()

    tasks = tasks.strip()
    if not tasks:
        return JSONResponse({
            "status": False,
            "error":  "Parameter 'tasks' wajib diisi. Pisahkan beberapa endpoint dengan ';'",
        })

    raw_tasks = [t.strip() for t in tasks.split(";") if t.strip()]
    if not raw_tasks:
        return JSONResponse({"status": False, "error": "Tidak ada task valid ditemukan"})

    if len(raw_tasks) > _MULTIPLE_MAX_TASKS:
        return JSONResponse({
            "status": False,
            "error":  f"Maksimal {_MULTIPLE_MAX_TASKS} task per request. Dikirim: {len(raw_tasks)}",
        })

    coros = [
        _run_single_task(i + 1, task, {}, "GET")
        for i, task in enumerate(raw_tasks)
    ]
    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    results: list = []
    for i, r in enumerate(raw_results):
        if isinstance(r, Exception):
            results.append({
                "task_id":    i + 1,
                "endpoint":   raw_tasks[i],
                "status":     False,
                "error":      f"{type(r).__name__}: {r}",
                "result":     None,
                "duration_ms": 0,
            })
        else:
            results.append(r)

    success_count, failed_count = _multiple_build_summary(results)

    return {
        "status":        True,
        "parallel":      True,
        "total_tasks":   len(results),
        "success_count": success_count,
        "failed_count":  failed_count,
        "duration_ms":   round((time.monotonic() - t_start) * 1000, 2),
        "results":       results,
    }


@app.post("/api/multiple")
async def multiple_post(request: Request):
    t_start = time.monotonic()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

    tasks_raw = body.get("tasks", [])
    parallel  = bool(body.get("parallel", True))

    if not isinstance(tasks_raw, list) or not tasks_raw:
        return JSONResponse({"status": False, "error": "'tasks' harus berupa array non-kosong"})

    if len(tasks_raw) > _MULTIPLE_MAX_TASKS:
        return JSONResponse({
            "status": False,
            "error":  f"Maksimal {_MULTIPLE_MAX_TASKS} task per request. Dikirim: {len(tasks_raw)}",
        })

    prepared: list = []
    for i, task in enumerate(tasks_raw):
        tid = i + 1
        if not isinstance(task, dict):
            prepared.append({
                "task_id":    tid,
                "endpoint":   "",
                "status":     False,
                "error":      f"Task #{tid}: harus berupa object, bukan {type(task).__name__}",
                "result":     None,
                "duration_ms": 0,
            })
            continue

        endpoint = (task.get("endpoint") or "").strip()
        if not endpoint:
            prepared.append({
                "task_id":    tid,
                "endpoint":   "",
                "status":     False,
                "error":      f"Task #{tid}: field 'endpoint' wajib diisi",
                "result":     None,
                "duration_ms": 0,
            })
            continue

        params = task.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        method = (task.get("method") or "POST").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            method = "POST"

        prepared.append(("ok", tid, endpoint, params, method))

    async def _dispatch(item) -> dict:
        if isinstance(item, dict):
            return item
        _, tid, endpoint, params, method = item
        return await _run_single_task(tid, endpoint, params, method)

    if parallel:
        raw_results = await asyncio.gather(
            *[_dispatch(t) for t in prepared],
            return_exceptions=True,
        )
    else:
        raw_results = []
        for t in prepared:
            try:
                raw_results.append(await _dispatch(t))
            except Exception as e:
                tid      = t[1] if isinstance(t, tuple) else t.get("task_id", 0)
                endpoint = t[2] if isinstance(t, tuple) else t.get("endpoint", "")
                raw_results.append({
                    "task_id":    tid,
                    "endpoint":   endpoint,
                    "status":     False,
                    "error":      f"{type(e).__name__}: {e}",
                    "result":     None,
                    "duration_ms": 0,
                })

    results: list = []
    for i, r in enumerate(raw_results):
        if isinstance(r, Exception):
            item     = prepared[i]
            tid      = item[1] if isinstance(item, tuple) else item.get("task_id", i + 1)
            endpoint = item[2] if isinstance(item, tuple) else item.get("endpoint", "")
            results.append({
                "task_id":    tid,
                "endpoint":   endpoint,
                "status":     False,
                "error":      f"{type(r).__name__}: {r}",
                "result":     None,
                "duration_ms": 0,
            })
        else:
            results.append(r)

    results.sort(key=lambda x: x.get("task_id", 0))

    success_count, failed_count = _multiple_build_summary(results)

    return {
        "status":        True,
        "parallel":      parallel,
        "total_tasks":   len(results),
        "success_count": success_count,
        "failed_count":  failed_count,
        "duration_ms":   round((time.monotonic() - t_start) * 1000, 2),
        "results":       results,
    }


# ── RUM Monitoring Endpoint ────────────────────────────────────────────────────

@app.get("/api/activity/user")
async def rum_activity_get(
    url:             str = "",
    userAgent:       str = "",
    screen_width:    int = 0,
    screen_height:   int = 0,
    viewport_width:  int = 0,
    viewport_height: int = 0,
    platform:        str = "",
    language:        str = "",
    user_timezone:   str = "",
    cores:           int = 0,
    touch_points:    int = 0,
    online:          str = "",
    timestamp:       str = "",
):
    now_ts = timestamp or datetime.now(timezone.utc).isoformat()
    entry = {
        "timestamp": now_ts,
        "method":    "GET",
        "rum_data":  {
            "url":          url,
            "userAgent":    userAgent,
            "screen":       {"width": screen_width, "height": screen_height},
            "viewport":     {"width": viewport_width, "height": viewport_height},
            "platform":     platform,
            "language":     language,
            "timezone":     user_timezone,
            "cores":        cores,
            "touch_points": touch_points,
            "online":       online == "true",
        },
    }

    ev = asyncio.get_event_loop()
    await ev.run_in_executor(None, _activity_append, entry)
    await _telegram_notify_rum(entry)

    return {
        "status":  True,
        "message": "RUM activity recorded",
        "data":    entry,
    }


@app.post("/api/activity/user")
async def rum_activity_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

    now_ts = body.get("timestamp") or datetime.now(timezone.utc).isoformat()
    entry = {
        "timestamp": now_ts,
        "method":    "POST",
        "rum_data":  {
            "url":           body.get("url", ""),
            "userAgent":     body.get("userAgent", ""),
            "screen": {
                "width":      body.get("screen_width", 0),
                "height":     body.get("screen_height", 0),
                "colorDepth": body.get("color_depth", 0),
            },
            "viewport": {
                "width":  body.get("viewport_width", 0),
                "height": body.get("viewport_height", 0),
            },
            "platform":      body.get("platform", ""),
            "language":      body.get("language", ""),
            "timezone":      body.get("timezone", ""),
            "cores":         body.get("cores", 0),
            "touch_points":  body.get("touch_points", 0),
            "device_memory": body.get("device_memory", ""),
            "online":        body.get("online", False),
            "connection":    body.get("connection"),
            "storage":       body.get("storage"),
            "features":      body.get("features"),
        },
    }

    ev = asyncio.get_event_loop()
    await ev.run_in_executor(None, _activity_append, entry)
    await _telegram_notify_rum(entry)

    return {
        "status":  True,
        "message": "RUM activity recorded",
        "data":    entry,
    }


# ── Mangum handler (Vercel/Lambda) ─────────────────────────────────────────────
handler = Mangum(app, lifespan="off")
