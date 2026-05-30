import os
import sys
import re
import uuid
import json
import subprocess
import urllib.request
import io
import contextlib
from datetime import datetime
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
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
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
# ==========================================
@app.get("/api/fetch")
async def fetch_url(url: str, dl: int = 0):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = res.read()
            if dl == 1:
                ctype = res.headers.get('Content-Type', 'application/octet-stream')
                return Response(content=data, media_type=ctype)
            return {"status": True, "url": url, "length": len(data), "content": data.decode('utf-8', errors='ignore')}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

# ==========================================
# 3. DOCUMENT PROCESSOR (PREFIX /nova)
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
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response, open(temp_filepath, 'wb') as f:
            f.write(response.read())

        if ext == 'pdf':
            from pypdf import PdfReader
            reader = PdfReader(temp_filepath)
            if action == 'extract-text':
                if page > 0:
                    return {"status": True, "action": action, "page": page, "text": reader.pages[page-1].extract_text()}
                return {"status": True, "action": action, "pages": [{"page": i+1, "text": p.extract_text()} for i, p in enumerate(reader.pages)]}
            elif action == 'metadata':
                m = reader.metadata
                return {"status": True, "action": action, "metadata": {"title": m.title if m else None, "author": m.author if m else None}}
            return JSONResponse({"status": False, "error": "Action PDF tidak valid"})

        elif ext == 'docx':
            from docx import Document
            if action == 'extract-docx':
                doc = Document(temp_filepath)
                return {"status": True, "action": action, "paragraphs": [p.text for p in doc.paragraphs if p.text.strip()]}
            return JSONResponse({"status": False, "error": "Action DOCX tidak valid"})

    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# ==========================================
# 4. IMAGE PROCESSOR (PREFIX /nova)
# ==========================================
@app.get("/nova/image")
async def process_image(action: str, url: str, width: int = 0, height: int = 0, format: str = ""):
    if os.path.exists('/tmp/p') and '/tmp/p' not in sys.path:
        sys.path.insert(0, '/tmp/p')

    temp_filepath = None
    output_filepath = None
    try:
        from PIL import Image

        # BUG FIX #1: Dua statement digabung dalam satu baris — dipecah jadi dua baris terpisah
        temp_filepath = f"/tmp/{uuid.uuid4().hex}.img"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response, open(temp_filepath, 'wb') as f:
            f.write(response.read())

        img = Image.open(temp_filepath)
        if action == 'info':
            return {"status": True, "action": action, "width": img.width, "height": img.height, "format": img.format, "mode": img.mode}

        elif action == 'resize':
            if width > 0 and height > 0:
                img = img.resize((width, height), Image.Resampling.LANCZOS)
            elif width > 0:
                ratio = width / img.width
                img = img.resize((width, int(img.height * ratio)), Image.Resampling.LANCZOS)
            elif height > 0:
                ratio = height / img.height
                img = img.resize((int(img.width * ratio), height), Image.Resampling.LANCZOS)

            ext_out = format or img.format.lower() or 'png'
            output_filepath = f"/tmp/{uuid.uuid4().hex}.{ext_out}"
            if ext_out.lower() in ['jpg', 'jpeg']:
                img = img.convert('RGB')
            img.save(output_filepath)
            with open(output_filepath, 'rb') as f:
                data = f.read()
            return Response(content=data, media_type=f"image/{ext_out}")

        elif action == 'convert':
            if not format:
                return JSONResponse({"status": False, "error": "Parameter 'format' wajib diisi"})
            output_filepath = f"/tmp/{uuid.uuid4().hex}.{format}"
            if format.lower() in ['jpg', 'jpeg']:
                img = img.convert('RGB')
            img.save(output_filepath, format.upper())
            with open(output_filepath, 'rb') as f:
                data = f.read()
            return Response(content=data, media_type=f"image/{format}")

        return JSONResponse({"status": False, "error": "Action tidak valid"})
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        if output_filepath and os.path.exists(output_filepath):
            os.remove(output_filepath)

# ==========================================
# 5. WEBHOOK MINI DATABASE (PREFIX /nova)
# ==========================================

# BUG FIX #2: Decorator @app.post sebelumnya tertimpa komentar:
#   "# ==========...==========@app.post("/nova/webhook")"
#   sehingga endpoint POST tidak pernah terdaftar ke FastAPI.
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
        qr = qrcode.QRCode(version=1, box_size=size, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return Response(content=buffer.getvalue(), media_type="image/png")
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

# ==========================================
# 7. WEBHOOK MANAGEMENT SYSTEM (PREFIX /api/webhook)
# ==========================================

WEBHOOKS_CONFIG_PATH = os.path.join(STORAGE_DIR, 'webhooks_config.json')


def _load_webhook_configs() -> dict:
    """Load webhook configurations from the persistent JSON file.
    Returns a dict keyed by webhook ID. Creates the file if it doesn't exist."""
    if not os.path.exists(WEBHOOKS_CONFIG_PATH):
        _save_webhook_configs({})
        return {}
    try:
        with open(WEBHOOKS_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # File is corrupt or unreadable — reset to a clean state
        _save_webhook_configs({})
        return {}


def _save_webhook_configs(configs: dict) -> None:
    """Persist webhook configurations dict to the JSON file."""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with open(WEBHOOKS_CONFIG_PATH, 'w') as f:
        json.dump(configs, f, indent=2)


def _send_post_request(target_url: str, payload: dict, secret: str = "") -> dict:
    """Send a POST request to target_url with JSON payload.
    Returns a dict with status, http_code, and response body (truncated)."""
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'NovaWebhook/1.0',
    }
    if secret:
        # Include the secret as a simple bearer-style header so the
        # receiving server can verify the origin of the request.
        headers['X-Nova-Secret'] = secret

    req = urllib.request.Request(target_url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            response_body = res.read().decode('utf-8', errors='ignore')
            return {
                "success": True,
                "http_code": res.status,
                "response": response_body[:500],  # cap at 500 chars
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')
        return {
            "success": False,
            "http_code": e.code,
            "response": error_body[:500],
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "http_code": None,
            "response": str(e.reason),
        }
    except Exception as e:
        return {
            "success": False,
            "http_code": None,
            "response": str(e),
        }


# ------------------------------------------
# 7-A. CREATE — POST /api/webhook/create
# ------------------------------------------
@app.post("/api/webhook/create")
async def webhook_create(request: Request):
    """Register a new webhook subscription.

    Expected JSON body:
        {
            "target_url": "https://example.com/receiver",  # required
            "event_name": "order.created",                 # required
            "secret":     "my-secret-token"                # optional
        }

    Returns the newly created webhook record including its generated ID.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})

    target_url: str = (body.get("target_url") or "").strip()
    event_name: str = (body.get("event_name") or "").strip()
    secret: str     = (body.get("secret") or "").strip()

    # --- Validation ---
    if not target_url:
        return JSONResponse({"status": False, "error": "Field 'target_url' wajib diisi"})
    if not target_url.startswith(("http://", "https://")):
        return JSONResponse({"status": False, "error": "Field 'target_url' harus berupa URL yang valid (http/https)"})
    if not event_name:
        return JSONResponse({"status": False, "error": "Field 'event_name' wajib diisi"})

    webhook_id   = uuid.uuid4().hex
    created_at   = datetime.utcnow().isoformat() + "Z"

    new_webhook = {
        "id":         webhook_id,
        "target_url": target_url,
        "event_name": event_name,
        "secret":     secret,
        "created_at": created_at,
        "trigger_count": 0,
        "last_triggered_at": None,
    }

    try:
        configs = _load_webhook_configs()
        configs[webhook_id] = new_webhook
        _save_webhook_configs(configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal menyimpan webhook: {str(e)}"})

    return {
        "status":  True,
        "message": "Webhook berhasil didaftarkan",
        "webhook": new_webhook,
    }


# ------------------------------------------
# 7-B. LIST — GET /api/webhook/list
# ------------------------------------------
@app.get("/api/webhook/list")
async def webhook_list(event_name: str = ""):
    """List all registered webhooks.

    Optional query param:
        event_name — filter results to webhooks matching this event name.

    Returns a list of webhook records sorted by created_at descending.
    """
    try:
        configs = _load_webhook_configs()
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})

    webhooks = list(configs.values())

    if event_name:
        webhooks = [w for w in webhooks if w.get("event_name") == event_name]

    # Sort newest first
    webhooks.sort(key=lambda w: w.get("created_at", ""), reverse=True)

    return {
        "status":   True,
        "count":    len(webhooks),
        "webhooks": webhooks,
    }


# ------------------------------------------
# 7-C. DELETE — DELETE /api/webhook/delete
# ------------------------------------------
@app.delete("/api/webhook/delete")
async def webhook_delete(id: str = ""):
    """Delete a registered webhook by its ID.

    Query param:
        id — the webhook ID to delete (required).
    """
    if not id:
        return JSONResponse({"status": False, "error": "Query param 'id' wajib diisi"})

    try:
        configs = _load_webhook_configs()
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})

    if id not in configs:
        return JSONResponse({"status": False, "error": f"Webhook dengan ID '{id}' tidak ditemukan"})

    deleted_webhook = configs.pop(id)

    try:
        _save_webhook_configs(configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal menyimpan perubahan: {str(e)}"})

    return {
        "status":  True,
        "message": f"Webhook '{id}' berhasil dihapus",
        "deleted": deleted_webhook,
    }


# ------------------------------------------
# 7-D. TRIGGER — POST /api/webhook/trigger
# ------------------------------------------
@app.post("/api/webhook/trigger")
async def webhook_trigger(request: Request):
    """Trigger a registered webhook by ID.

    Sends a POST request to the webhook's target_url with a standard
    event payload plus any extra fields provided in the request body.

    Expected JSON body:
        {
            "id":      "abc123...",      # required — webhook ID to fire
            "payload": { ... }           # optional — extra data merged into event payload
        }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})

    webhook_id: str   = (body.get("id") or "").strip()
    extra_payload     = body.get("payload") or {}

    if not webhook_id:
        return JSONResponse({"status": False, "error": "Field 'id' wajib diisi"})

    if not isinstance(extra_payload, dict):
        return JSONResponse({"status": False, "error": "Field 'payload' harus berupa objek JSON"})

    try:
        configs = _load_webhook_configs()
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})

    if webhook_id not in configs:
        return JSONResponse({"status": False, "error": f"Webhook dengan ID '{webhook_id}' tidak ditemukan"})

    webhook = configs[webhook_id]
    triggered_at = datetime.utcnow().isoformat() + "Z"

    # Build the standard event envelope
    event_payload = {
        "event":        webhook["event_name"],
        "webhook_id":   webhook_id,
        "triggered_at": triggered_at,
        "data":         extra_payload,
    }

    # Dispatch the HTTP POST to the registered target URL
    delivery_result = _send_post_request(
        target_url=webhook["target_url"],
        payload=event_payload,
        secret=webhook.get("secret", ""),
    )

    # Update metadata regardless of delivery success
    webhook["trigger_count"]      = webhook.get("trigger_count", 0) + 1
    webhook["last_triggered_at"]  = triggered_at
    configs[webhook_id]           = webhook

    try:
        _save_webhook_configs(configs)
    except Exception as e:
        # Non-fatal — delivery already happened; just warn in the response
        delivery_result["storage_warning"] = f"Gagal update metadata: {str(e)}"

    return {
        "status":         True,
        "message":        "Webhook berhasil di-trigger",
        "webhook_id":     webhook_id,
        "target_url":     webhook["target_url"],
        "event_name":     webhook["event_name"],
        "triggered_at":   triggered_at,
        "trigger_count":  webhook["trigger_count"],
        "delivery":       delivery_result,
        "payload_sent":   event_payload,
    }


# ==========================================
# 🚨 MAGIC WORD BUAT VERCEL (WAJIB ADA) 🚨
# ==========================================
handler = Mangum(app)
