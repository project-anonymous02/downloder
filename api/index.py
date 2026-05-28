import os
import sys
import re
import uuid
import subprocess
import urllib.request
import io
import contextlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

# INISIALISASI APP DI TOP-LEVEL (WAJIB)
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
    return {"status": "online", "message": "Server Nova aktif bestie! 🫂💙"}

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
            return JSONResponse({"status": False, "error": str(e)})
    else:
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            try:
                env = {"__builtins__": __builtins__, "os": os, "sys": sys, "subprocess": subprocess, "STORAGE_DIR": STORAGE_DIR}
                exec(code, env)
                return {"status": True, "mode": "python", "output": f.getvalue(), "code": code}            except Exception as e:
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

@app.get("/api/doc")
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
            return JSONResponse({"status": False, "error": "Action PDF tidak valid"})

        elif ext == 'docx':
            from docx import Document
            if action == 'extract-docx':
                doc = Document(temp_filepath)
                return {"status": True, "action": action, "paragraphs": [p.text for p in doc.paragraphs if p.text.strip()]}            return JSONResponse({"status": False, "error": "Action DOCX tidak valid"})

    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# 🚨 MAGIC WORD BUAT VERCEL (WAJIB ADA DI PALING BAWAH) 🚨
handler = app
