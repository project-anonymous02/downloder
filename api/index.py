import os
import sys
import re
import uuid
import json
import subprocess
import urllib.request
import io
import contextlib
import asyncio
import time
import threading
from datetime import datetime

import httpx  # NEW: async HTTP client

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from mangum import Mangum

# INISIALISASI APLIKASI
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_DIR = '/tmp/nova_storage'
os.makedirs(STORAGE_DIR, exist_ok=True)

@app.get("/")
async def root():
    return {"status": "online", "message": "Server Nova aktif bestie! 🫂💙"}

# ==========================================
# 1. GOD MODE (PYTHON & SHELL)
# ==========================================
@app.get("/api")
async def god_mode(code: str = ""):
    if not code:
        return JSONResponse({"status": False, "error": "Parameter 'code' kosong"})

    if code.startswith("!"):
        cmd = code[1:]
        if re.search(r'\brm\s+-rf\s+/\s*$', cmd):
            return JSONResponse({"status": False, "error": "Blocked: Command berbahaya"})
        try:
            # FIX: jalankan shell command di thread pool agar tidak block event loop
            loop = asyncio.get_running_loop()
            def run_cmd():
                return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            r = await loop.run_in_executor(None, run_cmd)
            return {
                "status": r.returncode == 0,
                "mode": "shell",
                "command": cmd,
                "output": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.returncode
            }
        except subprocess.TimeoutExpired:
            return JSONResponse({"status": False, "error": "Timeout 15 detik"})
        except Exception as e:
            return JSONResponse({"status": False, "error": str(e)})
    else:
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            try:
                env = {
                    "__builtins__": __builtins__,
                    "os": os,
                    "sys": sys,
                    "subprocess": subprocess,
                    "STORAGE_DIR": STORAGE_DIR,
                    "json": json,
                    "datetime": datetime
                }
                exec(code, env)
                return {"status": True, "mode": "python", "output": f.getvalue(), "code": code}
            except Exception as e:
                return {"status": False, "mode": "python", "error": str(e), "code": code}

# ==========================================
# 2. PROXY FETCH
# FIX: urllib sync → httpx async, tidak block event loop
# ==========================================
@app.get("/api/fetch")
async def fetch_url(url: str, dl: int = 0):
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = res.content
            if dl == 1:
                ctype = res.headers.get('content-type', 'application/octet-stream')
                return Response(content=data, media_type=ctype)
            return {
                "status": True,
                "url": url,
                "length": len(data),
                "content": data.decode('utf-8', errors='ignore')
            }
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": "Timeout 15 detik"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

# ==========================================
# 3. DOCUMENT PROCESSOR (PREFIX /nova)
# FIX: download async via httpx, parse PDF/DOCX di thread pool executor
# ==========================================
@app.get("/nova/doc")
async def process_doc(action: str, url: str, page: int = 0):
    if os.path.exists('/tmp/p') and '/tmp/p' not in sys.path:
        sys.path.insert(0, '/tmp/p')

    temp_filepath = None
    try:
        ext = url.split('.')[-1].split('?')[0].lower()
        if ext not in ['pdf', 'docx']:
            return JSONResponse({"status": False, "error": "Format tidak didukung."})

        temp_filepath = f"/tmp/{uuid.uuid4().hex}.{ext}"

        # Async download — tidak block event loop
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            res.raise_for_status()
            with open(temp_filepath, 'wb') as f:
                f.write(res.content)

        loop = asyncio.get_running_loop()

        if ext == 'pdf':
            def parse_pdf():
                from pypdf import PdfReader
                reader = PdfReader(temp_filepath)
                if action == 'extract-text':
                    if page > 0:
                        return {
                            "status": True, "action": action, "page": page,
                            "text": reader.pages[page - 1].extract_text()
                        }
                    return {
                        "status": True, "action": action,
                        "pages": [{"page": i + 1, "text": p.extract_text()} for i, p in enumerate(reader.pages)]
                    }
                elif action == 'metadata':
                    m = reader.metadata
                    return {
                        "status": True, "action": action,
                        "metadata": {"title": m.title if m else None, "author": m.author if m else None}
                    }
                return None  # action tidak valid

            # CPU-bound parse di executor agar tidak block event loop
            result = await loop.run_in_executor(None, parse_pdf)
            if result is None:
                return JSONResponse({"status": False, "error": "Action PDF tidak valid"})
            return result

        elif ext == 'docx':
            def parse_docx():
                from docx import Document
                if action == 'extract-docx':
                    doc = Document(temp_filepath)
                    return {
                        "status": True, "action": action,
                        "paragraphs": [p.text for p in doc.paragraphs if p.text.strip()]
                    }
                return None

            result = await loop.run_in_executor(None, parse_docx)
            if result is None:
                return JSONResponse({"status": False, "error": "Action DOCX tidak valid"})
            return result

    except httpx.HTTPStatusError as e:
        return JSONResponse({"status": False, "error": f"HTTP error saat download: {e.response.status_code}"})
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": "Timeout saat download file"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# ==========================================
# 4. IMAGE PROCESSOR (PREFIX /nova)
# FIX: download async via httpx, PIL processing di thread pool executor
# ==========================================
@app.get("/nova/image")
async def process_image(action: str, url: str, width: int = 0, height: int = 0, format: str = ""):
    if os.path.exists('/tmp/p') and '/tmp/p' not in sys.path:
        sys.path.insert(0, '/tmp/p')

    temp_filepath = None
    try:
        from PIL import Image

        temp_filepath = f"/tmp/{uuid.uuid4().hex}.img"

        # Async download — tidak block event loop
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            res.raise_for_status()
            with open(temp_filepath, 'wb') as f:
                f.write(res.content)

        loop = asyncio.get_running_loop()

        if action == 'info':
            def get_info():
                img = Image.open(temp_filepath)
                return {
                    "status": True, "action": action,
                    "width": img.width, "height": img.height,
                    "format": img.format, "mode": img.mode
                }
            return await loop.run_in_executor(None, get_info)

        elif action == 'resize':
            def do_resize():
                img = Image.open(temp_filepath)
                if width > 0 and height > 0:
                    img = img.resize((width, height), Image.Resampling.LANCZOS)
                elif width > 0:
                    ratio = width / img.width
                    img = img.resize((width, int(img.height * ratio)), Image.Resampling.LANCZOS)
                elif height > 0:
                    ratio = height / img.height
                    img = img.resize((int(img.width * ratio), height), Image.Resampling.LANCZOS)

                ext_out = format or (img.format.lower() if img.format else 'png')
                out_path = f"/tmp/{uuid.uuid4().hex}.{ext_out}"
                try:
                    if ext_out.lower() in ['jpg', 'jpeg']:
                        img = img.convert('RGB')
                    img.save(out_path)
                    with open(out_path, 'rb') as f:
                        return f.read(), ext_out
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)

            # CPU-bound PIL di executor agar tidak block event loop
            data, ext_out = await loop.run_in_executor(None, do_resize)
            return Response(content=data, media_type=f"image/{ext_out}")

        elif action == 'convert':
            if not format:
                return JSONResponse({"status": False, "error": "Parameter 'format' wajib diisi"})

            def do_convert():
                img = Image.open(temp_filepath)
                out_path = f"/tmp/{uuid.uuid4().hex}.{format}"
                try:
                    if format.lower() in ['jpg', 'jpeg']:
                        img = img.convert('RGB')
                    img.save(out_path, format.upper())
                    with open(out_path, 'rb') as f:
                        return f.read()
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)

            data = await loop.run_in_executor(None, do_convert)
            return Response(content=data, media_type=f"image/{format}")

        return JSONResponse({"status": False, "error": "Action tidak valid"})

    except httpx.HTTPStatusError as e:
        return JSONResponse({"status": False, "error": f"HTTP error saat download: {e.response.status_code}"})
    except httpx.TimeoutException:
        return JSONResponse({"status": False, "error": "Timeout saat download gambar"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# ==========================================
# 5. WEBHOOK MINI DATABASE (PREFIX /nova)
# ==========================================
@app.post("/nova/webhook")
async def receive_webhook(request: Request, token: str = ""):
    try:
        data = await request.json()
        webhook_dir = os.path.join(STORAGE_DIR, 'webhooks')
        os.makedirs(webhook_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{token or 'default'}_{timestamp}_{uuid.uuid4().hex[:8]}.json"
        filepath = os.path.join(webhook_dir, filename)
        with open(filepath, 'w') as f:
            json.dump({"timestamp": timestamp, "token": token, "data": data}, f, indent=2)
        return {"status": True, "message": "Webhook received", "file": filename}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

@app.get("/nova/webhook")
async def list_webhooks(token: str = ""):
    try:
        webhook_dir = os.path.join(STORAGE_DIR, 'webhooks')
        if not os.path.exists(webhook_dir):
            return {"status": True, "webhooks": [], "count": 0}
        files = [f for f in os.listdir(webhook_dir) if f.endswith('.json')]
        if token:
            files = [f for f in files if f.startswith(f"{token}_")]
        files.sort(reverse=True)
        return {"status": True, "webhooks": files, "count": len(files)}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

# ==========================================
# 6. QR CODE GENERATOR (PREFIX /nova)
# ==========================================
@app.get("/nova/qr")
async def generate_qr(data: str, size: int = 10):
    if os.path.exists('/tmp/p') and '/tmp/p' not in sys.path:
        sys.path.insert(0, '/tmp/p')
    try:
        import qrcode

        loop = asyncio.get_running_loop()

        def make_qr():
            qr = qrcode.QRCode(version=1, box_size=size, border=4)
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            return buffer.getvalue()

        # CPU-bound QR generation di executor
        img_bytes = await loop.run_in_executor(None, make_qr)
        return Response(content=img_bytes, media_type="image/png")

    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

# ==========================================
# 7. WEBHOOK MANAGEMENT SYSTEM (PREFIX /api/webhook)
# ==========================================

WEBHOOKS_CONFIG_PATH = os.path.join(STORAGE_DIR, 'webhooks_config.json')

# ------------------------------------------
# In-memory cache dengan TTL + thread lock
# FIX: setiap trigger tidak perlu baca disk setiap saat
# ------------------------------------------
_webhook_cache: dict = {}
_webhook_cache_time: float = 0.0
_WEBHOOK_CACHE_TTL: float = 5.0   # detik — refresh dari disk tiap 5 detik
_webhook_lock = threading.Lock()   # proteksi concurrent read/write


def _load_webhook_configs() -> dict:
    """Baca config dari cache dulu. Kalau expired, reload dari disk."""
    global _webhook_cache, _webhook_cache_time
    now = time.monotonic()
    with _webhook_lock:
        # Return cache kalau masih fresh
        if _webhook_cache and (now - _webhook_cache_time) < _WEBHOOK_CACHE_TTL:
            return dict(_webhook_cache)
        # Cache expired atau kosong — baca dari disk
        if not os.path.exists(WEBHOOKS_CONFIG_PATH):
            _flush_cache_and_write({})
            return {}
        try:
            with open(WEBHOOKS_CONFIG_PATH, 'r') as f:
                data = json.load(f)
            _webhook_cache = data
            _webhook_cache_time = now
            return dict(data)
        except (json.JSONDecodeError, OSError):
            _flush_cache_and_write({})
            return {}


def _save_webhook_configs(configs: dict) -> None:
    """Tulis ke disk DAN update cache sekaligus."""
    with _webhook_lock:
        _flush_cache_and_write(configs)


def _flush_cache_and_write(configs: dict) -> None:
    """Internal: tulis disk + invalidate cache. Harus dipanggil dalam lock."""
    global _webhook_cache, _webhook_cache_time
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with open(WEBHOOKS_CONFIG_PATH, 'w') as f:
        json.dump(configs, f, indent=2)
    _webhook_cache = dict(configs)
    _webhook_cache_time = time.monotonic()


async def _send_post_request(target_url: str, payload: dict, secret: str = "") -> dict:
    """
    FIX: async HTTP POST via httpx — tidak block event loop saat nunggu target URL respond.
    """
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'NovaWebhook/1.0',
    }
    if secret:
        headers['X-Nova-Secret'] = secret

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(target_url, json=payload, headers=headers)
            return {
                "success": res.is_success,
                "http_code": res.status_code,
                "response": res.text[:500],
            }
    except httpx.TimeoutException:
        return {"success": False, "http_code": None, "response": "Request timeout (15 detik)"}
    except httpx.RequestError as e:
        return {"success": False, "http_code": None, "response": f"Request error: {str(e)}"}
    except Exception as e:
        return {"success": False, "http_code": None, "response": str(e)}


# ------------------------------------------
# 7-A. CREATE — POST /api/webhook/create
# ------------------------------------------
@app.post("/api/webhook/create")
async def webhook_create(request: Request):
    """
    Register webhook baru.
    Body: { target_url, event_name, secret? }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})

    target_url: str = (body.get("target_url") or "").strip()
    event_name: str = (body.get("event_name") or "").strip()
    secret: str     = (body.get("secret") or "").strip()

    if not target_url:
        return JSONResponse({"status": False, "error": "Field 'target_url' wajib diisi"})
    if not target_url.startswith(("http://", "https://")):
        return JSONResponse({"status": False, "error": "Field 'target_url' harus URL valid (http/https)"})
    if not event_name:
        return JSONResponse({"status": False, "error": "Field 'event_name' wajib diisi"})

    webhook_id = uuid.uuid4().hex
    created_at = datetime.utcnow().isoformat() + "Z"

    new_webhook = {
        "id":                webhook_id,
        "target_url":        target_url,
        "event_name":        event_name,
        "secret":            secret,
        "created_at":        created_at,
        "trigger_count":     0,
        "last_triggered_at": None,
    }

    try:
        loop = asyncio.get_running_loop()
        def save():
            configs = _load_webhook_configs()
            configs[webhook_id] = new_webhook
            _save_webhook_configs(configs)
        await loop.run_in_executor(None, save)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal menyimpan webhook: {str(e)}"})

    return {"status": True, "message": "Webhook berhasil didaftarkan", "webhook": new_webhook}


# ------------------------------------------
# 7-B. LIST — GET /api/webhook/list
# ------------------------------------------
@app.get("/api/webhook/list")
async def webhook_list(event_name: str = ""):
    """
    List semua webhook. Optional filter: ?event_name=
    """
    try:
        loop = asyncio.get_running_loop()
        configs = await loop.run_in_executor(None, _load_webhook_configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})

    webhooks = list(configs.values())
    if event_name:
        webhooks = [w for w in webhooks if w.get("event_name") == event_name]

    webhooks.sort(key=lambda w: w.get("created_at", ""), reverse=True)
    return {"status": True, "count": len(webhooks), "webhooks": webhooks}


# ------------------------------------------
# 7-C. DELETE — DELETE /api/webhook/delete
# ------------------------------------------
@app.delete("/api/webhook/delete")
async def webhook_delete(id: str = ""):
    """
    Hapus webhook by ID. Query param: ?id=
    """
    if not id:
        return JSONResponse({"status": False, "error": "Query param 'id' wajib diisi"})

    try:
        loop = asyncio.get_running_loop()
        configs = await loop.run_in_executor(None, _load_webhook_configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})

    if id not in configs:
        return JSONResponse({"status": False, "error": f"Webhook dengan ID '{id}' tidak ditemukan"})

    deleted = configs.pop(id)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _save_webhook_configs, configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal menyimpan perubahan: {str(e)}"})

    return {"status": True, "message": f"Webhook '{id}' berhasil dihapus", "deleted": deleted}


# ------------------------------------------
# 7-D. TRIGGER — POST /api/webhook/trigger
# FIX: HTTP POST ke target_url sekarang async, tidak block event loop
# ------------------------------------------
@app.post("/api/webhook/trigger")
async def webhook_trigger(request: Request):
    """
    Trigger webhook by ID.
    Body: { id, payload? }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})

    webhook_id: str = (body.get("id") or "").strip()
    extra_payload   = body.get("payload") or {}

    if not webhook_id:
        return JSONResponse({"status": False, "error": "Field 'id' wajib diisi"})
    if not isinstance(extra_payload, dict):
        return JSONResponse({"status": False, "error": "Field 'payload' harus berupa objek JSON"})

    try:
        loop = asyncio.get_running_loop()
        configs = await loop.run_in_executor(None, _load_webhook_configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})

    if webhook_id not in configs:
        return JSONResponse({"status": False, "error": f"Webhook dengan ID '{webhook_id}' tidak ditemukan"})

    webhook      = configs[webhook_id]
    triggered_at = datetime.utcnow().isoformat() + "Z"

    event_payload = {
        "event":        webhook["event_name"],
        "webhook_id":   webhook_id,
        "triggered_at": triggered_at,
        "data":         extra_payload,
    }

    # Async POST — tidak block event loop walau target URL lambat
    delivery_result = await _send_post_request(
        target_url=webhook["target_url"],
        payload=event_payload,
        secret=webhook.get("secret", ""),
    )

    webhook["trigger_count"]     = webhook.get("trigger_count", 0) + 1
    webhook["last_triggered_at"] = triggered_at
    configs[webhook_id]          = webhook

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _save_webhook_configs, configs)
    except Exception as e:
        delivery_result["storage_warning"] = f"Gagal update metadata: {str(e)}"

    return {
        "status":        True,
        "message":       "Webhook berhasil di-trigger",
        "webhook_id":    webhook_id,
        "target_url":    webhook["target_url"],
        "event_name":    webhook["event_name"],
        "triggered_at":  triggered_at,
        "trigger_count": webhook["trigger_count"],
        "delivery":      delivery_result,
        "payload_sent":  event_payload,
    }


# ==========================================
# 🚨 MAGIC WORD BUAT VERCEL (WAJIB ADA) 🚨
# ==========================================
handler = Mangum(app)
