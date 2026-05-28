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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_DIR = '/tmp/nova_storage'
os.makedirs(STORAGE_DIR, exist_ok=True)

@app.get("/")
async def root():
    return {
        "status": "online", 
        "message": "Server Nova aktif bestie! 🫂💙",
        "endpoints": {
            "legacy": ["/api", "/api/fetch"],
            "nova": ["/nova/doc", "/nova/image", "/nova/webhook", "/nova/qr"]
        }
    }

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
            return {"status": r.returncode == 0, "mode": "shell", "command": cmd, "output": r.stdout, "stderr": r.stderr, "exit_code": r.returncode}
        except Exception as e:
            return JSONResponse({"status": False, "error": str(e)})    else:
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            try:
                env = {"__builtins__": __builtins__, "os": os, "sys": sys, "subprocess": subprocess, "STORAGE_DIR": STORAGE_DIR, "json": json, "datetime": datetime}
                exec(code, env)
                return {"status": True, "mode": "python", "output": f.getvalue(), "code": code}
            except Exception as e:
                return {"status": False, "mode": "python", "error": str(e), "code": code}

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

# === FITUR BARU DENGAN PREFIX /nova/ ===

@app.get("/nova/doc")
async def process_doc(action: str, url: str, page: int = 0):
    pkg_path = '/tmp/p'
    if os.path.exists(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)
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
            return JSONResponse({"status": False, "error": "Action PDF tidak valid"})        elif ext == 'docx':
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

@app.get("/nova/image")
async def process_image(action: str, url: str, width: int = 0, height: int = 0, format: str = ""):
    pkg_path = '/tmp/p'
    if os.path.exists(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)
    temp_filepath = None
    output_filepath = None
    try:
        from PIL import Image
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
                return JSONResponse({"status": False, "error": "Parameter 'format' wajib diisi (webp, png, jpg)"})
            output_filepath = f"/tmp/{uuid.uuid4().hex}.{format}"
            if format.lower() in ['jpg', 'jpeg']:                img = img.convert('RGB')
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
        return {"status": True, "message": "Webhook received", "file": filename, "path": filepath}
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

@app.get("/nova/qr")
async def generate_qr(data: str, size: int = 10):
    pkg_path = '/tmp/p'
    if os.path.exists(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)
    try:
        import qrcode        qr = qrcode.QRCode(version=1, box_size=size, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return Response(content=buffer.getvalue(), media_type="image/png")
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

handler = app
