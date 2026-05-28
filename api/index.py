import os
import sys
import re
import json
import uuid
import subprocess
import urllib.request
import io
import contextlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

# --- INISIALISASI SERVER ---
app = FastAPI()

# Biar website /files atau domain lain bisa akses API ini tanpa kena blokir CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Folder storage semi-permanen
STORAGE_DIR = '/tmp/nova_storage'
os.makedirs(STORAGE_DIR, exist_ok=True)

# --- 1. ROOT ENDPOINT (Health Check) ---
@app.get("/")
async def root():
    return {"status": "online", "message": "Server Nova aktif dan siap dipakai bestie! 🫂💙"}

# --- 2. GOD MODE ENDPOINT (Python & Shell) ---
@app.get("/api")
async def god_mode(code: str = ""):
    if not code:
        return JSONResponse({"status": False, "error": "Parameter 'code' kosong"})

    # MODE SHELL (Diawali tanda !)
    if code.startswith("!"):
        cmd = code[1:]
        # Blacklist simpel biar nggak ngehancurin sistem utama
        if re.search(r'\brm\s+-rf\s+/\s*$', cmd):
            return JSONResponse({"status": False, "error": "Blocked: Command berbahaya terdeteksi"})
        
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            return {
                "status": r.returncode == 0,                 "mode": "shell", 
                "command": cmd, 
                "output": r.stdout, 
                "stderr": r.stderr, 
                "exit_code": r.returncode
            }
        except subprocess.TimeoutExpired:
            return JSONResponse({"status": False, "error": "Timeout: Command jalan lebih dari 15 detik"})
        except Exception as e:
            return JSONResponse({"status": False, "error": str(e)})

    # MODE PYTHON
    else:
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            try:
                # Inject environment biar bisa pakai STORAGE_DIR di script
                env = {"__builtins__": __builtins__, "os": os, "sys": sys, "subprocess": subprocess, "STORAGE_DIR": STORAGE_DIR}
                exec(code, env)
                return {"status": True, "mode": "python", "output": f.getvalue(), "code": code}
            except Exception as e:
                return {"status": False, "mode": "python", "error": str(e), "code": code}

# --- 3. PROXY & FETCH ENDPOINT ---
@app.get("/api/fetch")
async def fetch_url(url: str, dl: int = 0):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = res.read()
            # Kalau dl=1, langsung lempar file mentahan ke browser (buat download)
            if dl == 1:
                ctype = res.headers.get('Content-Type', 'application/octet-stream')
                return Response(content=data, media_type=ctype)
            # Kalau nggak, balikin sebagai teks/JSON
            return {"status": True, "url": url, "length": len(data), "content": data.decode('utf-8', errors='ignore')}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})

# --- 4. DOCUMENT PROCESSOR ENDPOINT ---
@app.get("/api/doc")
async def process_doc(action: str, url: str, page: int = 0):
    # Auto-inject path /tmp/p (jaga-jaga kalau user install library manual di sana)
    pkg_path = '/tmp/p'
    if os.path.exists(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)

    temp_filepath = None
    try:
        ext = url.split('.')[-1].split('?')[0].lower()        if ext not in ['pdf', 'docx']:
            return JSONResponse({"status": False, "error": "Format tidak didukung. Gunakan PDF atau DOCX."})

        # Download file ke /tmp dengan nama random biar nggak bentrok
        temp_filepath = f"/tmp/{uuid.uuid4().hex}.{ext}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response, open(temp_filepath, 'wb') as f:
            f.write(response.read())

        # PROSES PDF
        if ext == 'pdf':
            try:
                from pypdf import PdfReader
            except ImportError:
                return JSONResponse({"status": False, "error": "Library 'pypdf' belum terinstall di Vercel."})
            
            reader = PdfReader(temp_filepath)
            if action == 'extract-text':
                if page > 0:
                    return {"status": True, "action": action, "page": page, "text": reader.pages[page-1].extract_text()}
                return {"status": True, "action": action, "pages": [{"page": i+1, "text": p.extract_text()} for i, p in enumerate(reader.pages)]}
            elif action == 'metadata':
                m = reader.metadata
                return {"status": True, "action": action, "metadata": {"title": m.title if m else None, "author": m.author if m else None}}
            return JSONResponse({"status": False, "error": "Action PDF tidak valid"})

        # PROSES DOCX
        elif ext == 'docx':
            try:
                from docx import Document
            except ImportError:
                return JSONResponse({"status": False, "error": "Library 'python-docx' belum terinstall di Vercel."})
            
            if action == 'extract-docx':
                doc = Document(temp_filepath)
                return {"status": True, "action": action, "paragraphs": [p.text for p in doc.paragraphs if p.text.strip()]}
            return JSONResponse({"status": False, "error": "Action DOCX tidak valid"})

    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
        
    finally:
        # AUTO-CLEANUP: Hapus file dokumen dari /tmp biar nggak nyampah!
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)
