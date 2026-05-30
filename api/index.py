import os
import sys
import re
import uuid
import json
import subprocess
import io
import contextlib
import asyncio
import time
import threading
import hashlib
import base64
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

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

# ── Storage ────────────────────────────────────────────────────────────────────
STORAGE_DIR          = "/tmp/nova_storage"
LOOP_DIR             = os.path.join(STORAGE_DIR, "loops")
FETCH_CACHE_DIR      = os.path.join(STORAGE_DIR, "fetch_cache")
WEBHOOK_DATA_DIR     = os.path.join(STORAGE_DIR, "webhooks")
SHARED_CTX_PATH      = os.path.join(STORAGE_DIR, "shared_context.json")
WEBHOOKS_CONFIG_PATH = os.path.join(STORAGE_DIR, "webhooks_config.json")

for _d in [STORAGE_DIR, LOOP_DIR, FETCH_CACHE_DIR, WEBHOOK_DATA_DIR]:
    os.makedirs(_d, exist_ok=True)

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


# ── Root ───────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "online", "message": "Server Nova aktif bestie! 🫂💙"}


# ── Shared Context (inter-loop cache) ──────────────────────────────────────────
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


# ── God Mode ───────────────────────────────────────────────────────────────────
_BLOCKED_CMDS = [
    r"\brm\s+-rf\s+/\s*$",
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r":\(\)\s*\{",           # fork bomb
]


def _run_god_mode(
    code: str,
    exec_timeout: int = 25,
    shared_ctx: Optional[dict] = None,
) -> dict:
    if code.startswith("!"):
        cmd = code[1:].strip()
        for pat in _BLOCKED_CMDS:
            if re.search(pat, cmd):
                return {"status": False, "error": "Blocked: command berbahaya", "mode": "shell"}
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=min(exec_timeout, 25),
            )
            result = {
                "status":    r.returncode == 0,
                "mode":      "shell",
                "command":   cmd,
                "output":    r.stdout,
                "stderr":    r.stderr,
                "exit_code": r.returncode,
            }
            if not result["status"] and r.stderr:
                result["error"] = r.stderr.strip()
            return result
        except subprocess.TimeoutExpired:
            return {"status": False, "error": f"Shell timeout setelah {exec_timeout}s", "mode": "shell"}
        except Exception as e:
            return {"status": False, "error": f"{type(e).__name__}: {e}", "mode": "shell"}

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
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
            exec(compile(code, "<nova>", "exec"), env)  # noqa: S102
            # Auto-persist shared context if loop code mutated it
            if shared_ctx is not None:
                new_shared = env.get("shared", {})
                if isinstance(new_shared, dict) and new_shared != shared_ctx:
                    _save_shared_context(new_shared)
            return {"status": True, "mode": "python", "output": buf.getvalue()}
        except SyntaxError as e:
            return {"status": False, "mode": "python", "error": f"SyntaxError baris {e.lineno}: {e.msg}"}
        except Exception as e:
            import traceback
            return {
                "status":    False,
                "mode":      "python",
                "error":     f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(limit=5),
            }


@app.get("/api")
@app.get("/api/run")
async def god_mode_get(code: str = "", status: str = "", timeout: int = 25):
    ev = asyncio.get_running_loop()
    if status:
        return await ev.run_in_executor(None, _loop_get_status, status)
    if not code:
        return JSONResponse({"status": False, "error": "Parameter 'code' wajib diisi"})
    shared = await ev.run_in_executor(None, _load_shared_context)
    return await ev.run_in_executor(None, _run_god_mode, code, timeout, shared)


@app.post("/api")
@app.post("/api/run")
async def god_mode_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Body harus JSON yang valid"})

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
            auth_type=body.get("auth_type", ""), auth_token=body.get("auth_token", ""),
            auth_user=body.get("auth_user", ""), auth_pass=body.get("auth_pass", ""),
            parse=body.get("parse", ""), cache=int(body.get("cache", 0)),
            dl=int(body.get("dl", 0)),
        )
        return result if isinstance(result, Response) else result

    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse({"status": False, "error": "Field 'code' atau 'url' wajib diisi"})
    ev     = asyncio.get_running_loop()
    shared = await ev.run_in_executor(None, _load_shared_context)
    return await ev.run_in_executor(None, _run_god_mode, code, int(body.get("timeout", 25)), shared)


# ── Loop System (Tick-based — Vercel-compatible) ───────────────────────────────
#
#  ⚠️  Vercel serverless MEMBUNUH background thread segera setelah response
#      dikembalikan. Solusi: loop hanya menyimpan state di file, eksekusi
#      dipicu lewat endpoint /api/run/loop/tick (panggil dari Vercel Cron
#      atau external scheduler setiap N detik).
#
#  Alur:
#    1. POST /api/run/loop          → buat loop, jalankan iterasi pertama
#    2. GET  /api/run/loop/tick     → jalankan 1 iterasi (kalau sudah waktunya)
#    3. GET  /api/run/loop?action=status&loop_id=xxx
#    4. GET  /api/run/loop?action=stop&loop_id=xxx
#    5. GET  /api/run/loop?action=list

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
        wait  = state.get("wait_time", 60)
        last  = state.get("last_run_unix", 0)
        nxt   = max(0.0, round(last + wait - time.time(), 1))
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
    run_now:        bool = True,
) -> dict:
    wt      = max(5, min(wait_time if wait_time is not None else interval, 3600))
    loop_id = uuid.uuid4().hex[:12]
    state   = {
        "loop_id":            loop_id,
        "description":        description,
        "status":             "running",
        "code":               code,
        "interval":           interval,
        "wait_time":          wt,
        "exec_timeout":       min(exec_timeout, 25),
        "stop_on_error":      stop_on_error,
        "max_iterations":     max_iterations,
        "webhook_url":        webhook_url,
        "notify_on":          notify_on or ["error"],
        "depends_on":         depends_on,
        "iteration":          0,
        "created_at":         _now(),
        "last_run_unix":      0,
        "last_heartbeat":     None,
        "last_output":        None,
        "last_error":         None,
        "last_exec_time_s":   None,
        "executing":          False,
        "execution_started":  None,
        "history":            [],
    }
    _loop_write(loop_id, state)

    result: dict = {
        "status":     True,
        "message":    "Loop created",
        "loop_id":    loop_id,
        "wait_time":  wt,
        "note":       "Gunakan /api/run/loop/tick?loop_id=... dari Vercel Cron untuk tiap iterasi",
        "tick_url":   f"/api/run/loop/tick?loop_id={loop_id}",
        "status_url": f"/api/run/loop?action=status&loop_id={loop_id}",
        "stop_url":   f"/api/run/loop?action=stop&loop_id={loop_id}",
    }

    if run_now:
        result["first_tick"] = _loop_tick(loop_id)

    return result


def _loop_tick(loop_id: str) -> dict:
    """Jalankan 1 iterasi loop. Panggil dari external cron / Vercel Cron."""
    state = _loop_read(loop_id)
    if state is None:
        return {"status": False, "error": f"Loop '{loop_id}' tidak ditemukan"}

    if state["status"] != "running":
        return {"status": False, "error": f"Loop tidak aktif (status: {state['status']})", "loop_id": loop_id}

    # Cek dependency
    dep = state.get("depends_on", "")
    if dep:
        dep_state = _loop_read(dep)
        if dep_state and dep_state.get("status") not in ("completed", "stopped"):
            return {
                "status": False,
                "error":  f"Dependency loop '{dep}' belum selesai (status: {dep_state.get('status')})",
                "loop_id": loop_id,
            }

    # Cek interval — wait_time dihitung SETELAH eksekusi selesai (bukan total time)
    now_unix   = time.time()
    last_unix  = state.get("last_run_unix", 0)
    wait_time  = state.get("wait_time", 60)
    iteration  = state.get("iteration", 0)

    if iteration > 0:
        elapsed = now_unix - last_unix
        if elapsed < wait_time:
            return {
                "status":         True,
                "executed":       False,
                "skipped":        True,
                "reason":         "Belum waktunya eksekusi",
                "next_run_in_s":  round(wait_time - elapsed, 1),
                "loop_id":        loop_id,
            }

    # Tandai sedang executing (heartbeat)
    exec_start_ts = _now()
    state.update({"executing": True, "execution_started": exec_start_ts, "last_heartbeat": exec_start_ts})
    _loop_write(loop_id, state)

    # Jalankan kode
    shared      = _load_shared_context()
    t0          = time.monotonic()
    run_result  = _run_god_mode(state["code"], exec_timeout=state.get("exec_timeout", 25), shared_ctx=shared)
    exec_time   = round(time.monotonic() - t0, 3)

    iteration  += 1
    ts          = _now()
    success     = bool(run_result.get("status"))

    history_entry = {
        "iteration":   iteration,
        "timestamp":   ts,
        "output":      run_result.get("output", ""),
        "stderr":      run_result.get("stderr", ""),
        "error":       run_result.get("error"),
        "traceback":   run_result.get("traceback"),
        "exec_time_s": exec_time,
        "ok":          success,
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

    # Tentukan status baru
    if not success and state.get("stop_on_error"):
        state.update({"status": "stopped", "stopped_reason": "error", "stopped_at": ts})
    elif state.get("max_iterations", 0) > 0 and iteration >= state["max_iterations"]:
        state.update({"status": "completed", "stopped_reason": "max_iterations_reached", "stopped_at": ts})
    else:
        state["status"] = "running"

    _loop_write(loop_id, state)

    # Kirim webhook notifikasi jika dikonfigurasi
    webhook_url = state.get("webhook_url", "")
    notify_on   = state.get("notify_on", ["error"])
    webhook_sent = False
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
                        "loop_id":     loop_id,
                        "description": state.get("description", ""),
                        "event":       "error" if not success else state["status"],
                        "iteration":   iteration,
                        "output":      run_result.get("output", ""),
                        "error":       run_result.get("error"),
                        "exec_time_s": exec_time,
                    },
                    timeout=5.0,
                )
                webhook_sent = True
            except Exception as e:
                webhook_error = str(e)

    tick_result: dict = {
        "status":        True,
        "executed":      True,
        "loop_id":       loop_id,
        "iteration":     iteration,
        "exec_time_s":   exec_time,
        "output":        run_result.get("output", ""),
        "stderr":        run_result.get("stderr", ""),
        "error":         run_result.get("error"),
        "traceback":     run_result.get("traceback"),
        "loop_status":   state["status"],
        "webhook_sent":  webhook_sent,
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
        webhook_url=webhook_url, description=description, run_now=run_now,
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
        run_now        = bool(body.get("run_now", True)),
    ))


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
        p = os.path.join(FETCH_CACHE_DIR, f"{key}.json")
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
        if s.startswith(("{", "[")):            return _parse_content(content, "json")
        if re.search(r"<!doctype\s+html|<html", s[:300], re.I): return _parse_content(content, "html")
        if s.startswith("<"):                   return _parse_content(content, "xml")
        return {"parse_mode": "auto", "detected": "plain_text", "text": s[:3000]}

    return {}


async def _execute_single_fetch(
    url:             str,
    method:          str  = "GET",
    req_headers:     Optional[dict] = None,
    body:            Optional[dict] = None,
    form_data:       Optional[dict] = None,
    timeout:         int  = 15,
    follow_redirect: bool = True,
    auth_type:       str  = "",
    auth_token:      str  = "",
    auth_user:       str  = "",
    auth_pass:       str  = "",
    parse:           str  = "",
    cache:           int  = 0,
    dl:              int  = 0,
) -> dict | Response:
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return {"status": False, "error": f"Method '{method}' tidak didukung", "url": url}

    hdrs = dict(req_headers or {})
    hdrs.setdefault("User-Agent", "Mozilla/5.0 (NovaProxy/2.0)")

    if auth_type == "bearer" and auth_token:
        hdrs["Authorization"] = f"Bearer {auth_token}"
    elif auth_type == "basic" and auth_user:
        creds = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        hdrs["Authorization"] = f"Basic {creds}"

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

    try:
        async with httpx.AsyncClient(
            timeout=float(min(max(timeout, 1), 60)),
            follow_redirects=follow_redirect,
        ) as client:
            kwargs: dict = {"headers": hdrs}
            if form_data and method != "GET":
                kwargs["data"] = form_data
            elif body and method != "GET":
                kwargs["json"] = body

            res       = await client.request(method, url, **kwargs)
            raw_bytes = res.content

            if dl == 1:
                ctype = res.headers.get("content-type", "application/octet-stream")
                return Response(content=raw_bytes, media_type=ctype)

            text   = raw_bytes.decode("utf-8", errors="ignore")
            result = {
                "status":           True,
                "url":              str(res.url),
                "method":           method,
                "http_code":        res.status_code,
                "response_headers": dict(res.headers),
                "length":           len(raw_bytes),
                "content":          text,
                "from_cache":       False,
            }
            if parse:
                result["parse_result"] = _parse_content(text, parse)
            if cache_key:
                _cache_write(cache_key, result)
            return result

    except httpx.TimeoutException:
        return {"status": False, "error": f"Timeout setelah {timeout}s", "url": url}
    except httpx.TooManyRedirects:
        return {"status": False, "error": "Terlalu banyak redirect", "url": url}
    except httpx.RequestError as e:
        return {"status": False, "error": f"Request error: {type(e).__name__}: {e}", "url": url}
    except Exception as e:
        return {"status": False, "error": str(e), "url": url}


@app.get("/api/fetch")
async def fetch_get(
    url:             str,
    dl:              int  = 0,
    method:          str  = "GET",
    headers:         str  = "",
    body:            str  = "",
    timeout:         int  = 15,
    follow_redirect: bool = True,
    auth_type:       str  = "",
    auth_token:      str  = "",
    auth_user:       str  = "",
    auth_pass:       str  = "",
    parse:           str  = "",
    cache:           int  = 0,
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
        timeout=timeout, follow_redirect=follow_redirect,
        auth_type=auth_type, auth_token=auth_token,
        auth_user=auth_user, auth_pass=auth_pass,
        parse=parse, cache=cache, dl=dl,
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
                auth_type=cfg.get("auth_type", ""), auth_token=cfg.get("auth_token", ""),
                auth_user=cfg.get("auth_user", ""), auth_pass=cfg.get("auth_pass", ""),
                parse=cfg.get("parse", ""), cache=int(cfg.get("cache", 0)), dl=0,
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
        auth_type=req_body.get("auth_type", ""), auth_token=req_body.get("auth_token", ""),
        auth_user=req_body.get("auth_user", ""), auth_pass=req_body.get("auth_pass", ""),
        parse=req_body.get("parse", ""), cache=int(req_body.get("cache", 0)),
        dl=int(req_body.get("dl", 0)),
    )
    return result if isinstance(result, Response) else result


# ── Document Processor ─────────────────────────────────────────────────────────
@app.get("/nova/doc")
async def process_doc(action: str, url: str, page: int = 0):
    if os.path.exists("/tmp/p") and "/tmp/p" not in sys.path:
        sys.path.insert(0, "/tmp/p")

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
                # extract-text
                if page > 0:
                    if page > total_pages:
                        return {"status": False, "error": f"Page {page} melebihi total ({total_pages})"}
                    return {"status": True, "action": action, "page": page,
                            "total_pages": total_pages,
                            "text": reader.pages[page - 1].extract_text()}
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
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Image Processor ────────────────────────────────────────────────────────────
@app.get("/nova/image")
async def process_image(action: str, url: str, width: int = 0, height: int = 0, output_format: str = ""):
    if os.path.exists("/tmp/p") and "/tmp/p" not in sys.path:
        sys.path.insert(0, "/tmp/p")

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
                return {"status": True, "action": action,
                        "width": img.width, "height": img.height,
                        "format": img.format, "mode": img.mode}
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
        return JSONResponse({"status": False, "error": "Pillow tidak terinstall"})
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
            return {"status": True, "url": target_url, "method": method,
                    "http_code": res.status_code, "success": res.is_success, "response": res.text[:1000]}
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": f"Timeout setelah {timeout}s"})
    except httpx.RequestError as e:
        return JSONResponse({"status": False, "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


@app.get("/nova/webhook/send")
async def nova_webhook_send_get(url: str, data: str = "", secret: str = "", method: str = "POST", timeout: int = 15):
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
            return {"status": True, "url": url, "method": method,
                    "http_code": res.status_code, "success": res.is_success, "response": res.text[:1000]}
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": f"Timeout setelah {timeout}s"})
    except httpx.RequestError as e:
        return JSONResponse({"status": False, "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


# ── QR Generator ───────────────────────────────────────────────────────────────
@app.get("/nova/qr")
async def generate_qr(data: str, size: int = 10):
    if os.path.exists("/tmp/p") and "/tmp/p" not in sys.path:
        sys.path.insert(0, "/tmp/p")
    try:
        import qrcode  # noqa: PLC0415
        ev = asyncio.get_running_loop()
        def make_qr() -> bytes:
            qr = qrcode.QRCode(version=1, box_size=max(1, min(size, 20)), border=4)
            qr.add_data(data)
            qr.make(fit=True)
            img    = qr.make_image(fill_color="black", back_color="white")
            buf    = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.getvalue()
        img_bytes = await ev.run_in_executor(None, make_qr)
        return Response(content=img_bytes, media_type="image/png")
    except ImportError:
        return JSONResponse({"status": False, "error": "qrcode tidak terinstall"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


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
        "id": wh_id, "target_url": target_url, "event_name": event_name,
        "secret": secret, "created_at": _now(),
        "trigger_count": 0, "last_triggered_at": None,
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
    payload      = {"event": wh["event_name"], "webhook_id": wh_id, "triggered_at": triggered_at, "data": extra_payload}
    delivery     = await _send_webhook(wh["target_url"], payload, wh.get("secret", ""))
    wh["trigger_count"]     = wh.get("trigger_count", 0) + 1
    wh["last_triggered_at"] = triggered_at
    configs[wh_id]          = wh
    try:
        await ev.run_in_executor(None, _wh_save, configs)
    except Exception as e:
        delivery["storage_warning"] = str(e)
    return {
        "status": True, "webhook_id": wh_id, "target_url": wh["target_url"],
        "event_name": wh["event_name"], "triggered_at": triggered_at,
        "trigger_count": wh["trigger_count"], "delivery": delivery, "payload_sent": payload,
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


# ── Mangum handler (Vercel/Lambda) ─────────────────────────────────────────────
handler = Mangum(app)
