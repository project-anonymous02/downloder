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
import hashlib
import base64
import xml.etree.ElementTree as ET
from datetime import datetime

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

STORAGE_DIR = '/tmp/nova_storage'
os.makedirs(STORAGE_DIR, exist_ok=True)

@app.get("/")
async def root():
    return {"status": "online", "message": "Server Nova aktif bestie! 🫂💙"}

# ==========================================
# 1. GOD MODE (PYTHON & SHELL)
# ==========================================

def _run_god_mode(code: str):
    if code.startswith("!"):
        cmd = code[1:]
        if re.search(r'\brm\s+-rf\s+/\s*$', cmd):
            return {"status": False, "error": "Blocked: Command berbahaya"}
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
            return {"status": False, "error": "Timeout 15 detik"}
        except Exception as e:
            return {"status": False, "error": str(e)}
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


@app.get("/api")
@app.get("/api/run")
async def god_mode_get(code: str = ""):
    if not code:
        return JSONResponse({"status": False, "error": "Parameter 'code' kosong"})
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run_god_mode, code)
    return result


@app.post("/api")
@app.post("/api/run")
async def god_mode_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse({"status": False, "error": "Field 'code' kosong"})
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run_god_mode, code)
    return result


# ==========================================
# 2. PROXY FETCH — HELPERS
# ==========================================

FETCH_CACHE_DIR = os.path.join(STORAGE_DIR, 'fetch_cache')

PIL_FORMAT_MAP = {
    'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG',
    'webp': 'WEBP', 'gif': 'GIF', 'bmp': 'BMP',
    'tiff': 'TIFF', 'tif': 'TIFF',
}


def _fetch_cache_key(url: str, method: str, body_str: str, headers_str: str) -> str:
    raw = f"{method.upper()}:{url}:{body_str}:{headers_str}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _fetch_cache_read(key: str, ttl: int):
    path = os.path.join(FETCH_CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            entry = json.load(f)
        if time.time() - entry.get("_cached_at", 0) > ttl:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        entry.pop("_cached_at", None)
        return entry
    except Exception:
        return None


def _fetch_cache_write(key: str, data: dict) -> None:
    try:
        os.makedirs(FETCH_CACHE_DIR, exist_ok=True)
        path = os.path.join(FETCH_CACHE_DIR, f"{key}.json")
        payload = dict(data)
        payload["_cached_at"] = time.time()
        with open(path, 'w') as f:
            json.dump(payload, f)
    except Exception:
        pass


def _parse_response_content(content: str, mode: str) -> dict:
    if mode == 'json':
        try:
            return {"parse_mode": "json", "parsed": json.loads(content)}
        except Exception as e:
            return {"parse_mode": "json", "parse_error": str(e)}

    elif mode == 'html':
        title_m = re.search(r'<title[^>]*>(.*?)</title>', content, re.I | re.S)
        links   = re.findall(r'href=["\']([^"\']{1,300})["\']', content, re.I)
        images  = re.findall(r'src=["\']([^"\']{1,300})["\']', content, re.I)
        metas   = re.findall(r'<meta[^>]+>', content, re.I)
        clean   = re.sub(r'<[^>]+>', ' ', content)
        clean   = re.sub(r'\s+', ' ', clean).strip()
        return {
            "parse_mode": "html",
            "title":      title_m.group(1).strip() if title_m else None,
            "links":      list(dict.fromkeys(links))[:50],
            "images":     list(dict.fromkeys(images))[:30],
            "meta_tags":  metas[:20],
            "text":       clean[:3000],
        }

    elif mode == 'xml':
        def _elem_to_dict(elem, depth=0):
            if depth > 10:
                return {"tag": elem.tag, "truncated": True}
            return {
                "tag":      elem.tag,
                "attrib":   dict(elem.attrib),
                "text":     (elem.text or "").strip() or None,
                "children": [_elem_to_dict(c, depth + 1) for c in elem],
            }
        try:
            root = ET.fromstring(content)
            return {"parse_mode": "xml", "parsed": _elem_to_dict(root)}
        except Exception as e:
            return {"parse_mode": "xml", "parse_error": str(e)}

    elif mode == 'auto':
        s = content.strip()
        if s.startswith(('{', '[')):
            return _parse_response_content(content, 'json')
        if re.search(r'<!doctype\s+html|<html', s[:300], re.I):
            return _parse_response_content(content, 'html')
        if s.startswith('<'):
            return _parse_response_content(content, 'xml')
        return {"parse_mode": "auto", "detected": "plain_text", "text": s[:3000]}

    return {}


async def _execute_single_fetch(
    url:             str,
    method:          str  = "GET",
    req_headers:     dict = None,
    body:            dict = None,
    form_data:       dict = None,
    timeout:         int  = 15,
    follow_redirect: bool = True,
    auth_type:       str  = "",
    auth_token:      str  = "",
    auth_user:       str  = "",
    auth_pass:       str  = "",
    parse:           str  = "",
    cache:           int  = 0,
    dl:              int  = 0,
):
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return {"status": False, "error": f"Method '{method}' tidak didukung", "url": url}

    if req_headers is None:
        req_headers = {}

    req_headers.setdefault('User-Agent', 'Mozilla/5.0 (NovaProxy/2.0)')

    if auth_type == "bearer" and auth_token:
        req_headers['Authorization'] = f"Bearer {auth_token}"
    elif auth_type == "basic" and auth_user:
        creds = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req_headers['Authorization'] = f"Basic {creds}"

    cache_key = None
    if cache > 0 and method == "GET" and dl == 0:
        cache_key = _fetch_cache_key(
            url, method,
            json.dumps(body or {}, sort_keys=True),
            json.dumps({k.lower(): v for k, v in req_headers.items()}, sort_keys=True)
        )
        cached = _fetch_cache_read(cache_key, cache)
        if cached:
            cached["from_cache"] = True
            return cached

    try:
        async with httpx.AsyncClient(
            timeout=float(min(max(timeout, 1), 30)),
            follow_redirects=follow_redirect,
        ) as client:
            kwargs = {"headers": req_headers}

            if form_data and method != "GET":
                kwargs["data"] = form_data
            elif body and method != "GET":
                kwargs["json"] = body

            res       = await client.request(method, url, **kwargs)
            raw_bytes = res.content

            if dl == 1:
                ctype = res.headers.get('content-type', 'application/octet-stream')
                return Response(content=raw_bytes, media_type=ctype)

            text = raw_bytes.decode('utf-8', errors='ignore')

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
                result["parse_result"] = _parse_response_content(text, parse)

            if cache_key:
                _fetch_cache_write(cache_key, result)

            return result

    except httpx.TimeoutException:
        return {"status": False, "error": f"Timeout setelah {timeout} detik", "url": url}
    except httpx.TooManyRedirects:
        return {"status": False, "error": "Terlalu banyak redirect", "url": url}
    except httpx.RequestError as e:
        return {"status": False, "error": f"Request error: {str(e)}", "url": url}
    except Exception as e:
        return {"status": False, "error": str(e), "url": url}


# ==========================================
# 2-A. PROXY FETCH — GET
# ==========================================
@app.get("/api/fetch")
async def fetch_url(
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
    parsed_headers = {}
    if headers:
        try:
            parsed_headers = json.loads(headers)
            if not isinstance(parsed_headers, dict):
                return JSONResponse({"status": False, "error": "'headers' harus berupa JSON object"})
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "'headers' bukan JSON yang valid"})

    parsed_body = None
    if body:
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "'body' bukan JSON yang valid"})

    result = await _execute_single_fetch(
        url=url, method=method, req_headers=parsed_headers,
        body=parsed_body, timeout=timeout, follow_redirect=follow_redirect,
        auth_type=auth_type, auth_token=auth_token,
        auth_user=auth_user, auth_pass=auth_pass,
        parse=parse, cache=cache, dl=dl,
    )
    if isinstance(result, Response):
        return result
    return result


# ==========================================
# 2-B. PROXY FETCH — POST (full-featured + chain)
# ==========================================
@app.post("/api/fetch")
async def fetch_url_advanced(request: Request):
    try:
        req_body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})

    chain = req_body.get("chain")
    if chain is not None:
        if not isinstance(chain, list) or len(chain) == 0:
            return JSONResponse({"status": False, "error": "'chain' harus berupa array non-kosong"})
        if len(chain) > 10:
            return JSONResponse({"status": False, "error": "Maksimal 10 request dalam satu chain"})

        results     = []
        prev_result = None

        for i, cfg in enumerate(chain):
            if not isinstance(cfg, dict):
                results.append({"index": i, "status": False, "error": "Setiap item chain harus berupa object"})
                break

            step_url = (cfg.get("url") or "").strip()
            if not step_url:
                results.append({"index": i, "status": False, "error": "Field 'url' wajib ada di setiap step"})
                break

            step_body = dict(cfg.get("body") or {})
            if cfg.get("use_prev_response") and prev_result and prev_result.get("status"):
                step_body["_prev"] = prev_result.get("content", "")[:2000]

            step_result = await _execute_single_fetch(
                url             = step_url,
                method          = cfg.get("method", "GET"),
                req_headers     = cfg.get("headers") or {},
                body            = step_body or None,
                form_data       = cfg.get("form_data"),
                timeout         = int(cfg.get("timeout", 15)),
                follow_redirect = bool(cfg.get("follow_redirect", True)),
                auth_type       = cfg.get("auth_type", ""),
                auth_token      = cfg.get("auth_token", ""),
                auth_user       = cfg.get("auth_user", ""),
                auth_pass       = cfg.get("auth_pass", ""),
                parse           = cfg.get("parse", ""),
                cache           = int(cfg.get("cache", 0)),
                dl              = 0,
            )

            if isinstance(step_result, dict):
                step_result["index"] = i
                results.append(step_result)
                prev_result = step_result
            else:
                results.append({"index": i, "status": False, "error": "Unexpected response type"})
                prev_result = None

        return {
            "status":      True,
            "mode":        "chain",
            "chain_count": len(results),
            "results":     results,
        }

    url = (req_body.get("url") or "").strip()
    if not url:
        return JSONResponse({"status": False, "error": "Field 'url' wajib diisi"})

    result = await _execute_single_fetch(
        url             = url,
        method          = req_body.get("method", "GET"),
        req_headers     = req_body.get("headers") or {},
        body            = req_body.get("body"),
        form_data       = req_body.get("form_data"),
        timeout         = int(req_body.get("timeout", 15)),
        follow_redirect = bool(req_body.get("follow_redirect", True)),
        auth_type       = req_body.get("auth_type", ""),
        auth_token      = req_body.get("auth_token", ""),
        auth_user       = req_body.get("auth_user", ""),
        auth_pass       = req_body.get("auth_pass", ""),
        parse           = req_body.get("parse", ""),
        cache           = int(req_body.get("cache", 0)),
        dl              = int(req_body.get("dl", 0)),
    )
    if isinstance(result, Response):
        return result
    return result


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
            return JSONResponse({"status": False, "error": "Format tidak didukung. Gunakan PDF atau DOCX."})

        valid_pdf_actions  = ['extract-text', 'metadata']
        valid_docx_actions = ['extract-docx']
        if ext == 'pdf' and action not in valid_pdf_actions:
            return JSONResponse({"status": False, "error": f"Action '{action}' tidak valid untuk PDF. Pilihan: {valid_pdf_actions}"})
        if ext == 'docx' and action not in valid_docx_actions:
            return JSONResponse({"status": False, "error": f"Action '{action}' tidak valid untuk DOCX. Pilihan: {valid_docx_actions}"})

        temp_filepath = f"/tmp/{uuid.uuid4().hex}.{ext}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            res.raise_for_status()
            with open(temp_filepath, 'wb') as f:
                f.write(res.content)

        loop = asyncio.get_running_loop()

        if ext == 'pdf':
            def parse_pdf():
                from pypdf import PdfReader
                reader      = PdfReader(temp_filepath)
                total_pages = len(reader.pages)
                if action == 'extract-text':
                    if page > 0:
                        if page > total_pages:
                            return {"status": False, "error": f"Page {page} melebihi total halaman ({total_pages})"}
                        return {"status": True, "action": action, "page": page, "total_pages": total_pages,
                                "text": reader.pages[page - 1].extract_text()}
                    return {"status": True, "action": action, "total_pages": total_pages,
                            "pages": [{"page": i + 1, "text": p.extract_text()} for i, p in enumerate(reader.pages)]}
                elif action == 'metadata':
                    m = reader.metadata
                    return {"status": True, "action": action,
                            "metadata": {"title": m.title if m else None, "author": m.author if m else None,
                                         "total_pages": total_pages}}
            return await loop.run_in_executor(None, parse_pdf)

        elif ext == 'docx':
            def parse_docx():
                from docx import Document
                if action == 'extract-docx':
                    doc = Document(temp_filepath)
                    return {"status": True, "action": action,
                            "paragraphs": [p.text for p in doc.paragraphs if p.text.strip()]}
            return await loop.run_in_executor(None, parse_docx)

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
# ==========================================
@app.get("/nova/image")
async def process_image(action: str, url: str, width: int = 0, height: int = 0, output_format: str = ""):
    if os.path.exists('/tmp/p') and '/tmp/p' not in sys.path:
        sys.path.insert(0, '/tmp/p')

    temp_filepath = None
    try:
        from PIL import Image

        temp_filepath = f"/tmp/{uuid.uuid4().hex}.img"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            res.raise_for_status()
            with open(temp_filepath, 'wb') as f:
                f.write(res.content)

        loop = asyncio.get_running_loop()

        if action == 'info':
            def get_info():
                img = Image.open(temp_filepath)
                return {"status": True, "action": action,
                        "width": img.width, "height": img.height,
                        "format": img.format, "mode": img.mode}
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

                ext_out  = output_format or (img.format.lower() if img.format else 'png')
                out_path = f"/tmp/{uuid.uuid4().hex}.{ext_out}"
                try:
                    pil_fmt = PIL_FORMAT_MAP.get(ext_out.lower(), ext_out.upper())
                    if pil_fmt in ('JPEG',):
                        img = img.convert('RGB')
                    img.save(out_path, pil_fmt)
                    with open(out_path, 'rb') as f:
                        return f.read(), ext_out
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)
            data, ext_out = await loop.run_in_executor(None, do_resize)
            return Response(content=data, media_type=f"image/{ext_out}")

        elif action == 'convert':
            if not output_format:
                return JSONResponse({"status": False, "error": "Parameter 'output_format' wajib diisi"})
            def do_convert():
                img      = Image.open(temp_filepath)
                out_path = f"/tmp/{uuid.uuid4().hex}.{output_format}"
                try:
                    pil_fmt = PIL_FORMAT_MAP.get(output_format.lower(), output_format.upper())
                    if pil_fmt in ('JPEG',):
                        img = img.convert('RGB')
                    img.save(out_path, pil_fmt)
                    with open(out_path, 'rb') as f:
                        return f.read()
                finally:
                    if os.path.exists(out_path):
                        os.remove(out_path)
            data = await loop.run_in_executor(None, do_convert)
            return Response(content=data, media_type=f"image/{output_format}")

        return JSONResponse({"status": False, "error": f"Action '{action}' tidak valid. Pilihan: info, resize, convert"})

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

def _nova_webhook_save(token: str, data: dict) -> str:
    """Helper simpan webhook data ke file. Dipakai GET & POST."""
    webhook_dir = os.path.join(STORAGE_DIR, 'webhooks')
    os.makedirs(webhook_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename  = f"{token or 'default'}_{timestamp}_{uuid.uuid4().hex[:8]}.json"
    filepath  = os.path.join(webhook_dir, filename)
    with open(filepath, 'w') as f:
        json.dump({"timestamp": timestamp, "token": token, "data": data}, f, indent=2)
    return filename


@app.post("/nova/webhook")
async def receive_webhook_post(request: Request, token: str = ""):
    """Terima webhook via POST — body JSON."""
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"status": False, "error": f"JSON parse error: {str(e)}"})
    try:
        loop     = asyncio.get_running_loop()
        filename = await loop.run_in_executor(None, _nova_webhook_save, token, data)
        return {"status": True, "message": "Webhook received", "method": "POST", "file": filename}
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


@app.get("/nova/webhook")
async def receive_or_list_webhook_get(request: Request, token: str = "", list: int = 0):
    """
    GET /nova/webhook?list=1&token=xxx  → list webhook files
    GET /nova/webhook?token=xxx&key=val → terima webhook dari query params
    """
    if list == 1:
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

    # Semua query params selain 'token' dan 'list' → jadi data webhook
    params = dict(request.query_params)
    params.pop("token", None)
    params.pop("list", None)
    try:
        loop     = asyncio.get_running_loop()
        filename = await loop.run_in_executor(None, _nova_webhook_save, token, params)
        return {"status": True, "message": "Webhook received", "method": "GET", "file": filename}
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
            img    = qr.make_image(fill_color="black", back_color="white")
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            return buffer.getvalue()
        img_bytes = await loop.run_in_executor(None, make_qr)
        return Response(content=img_bytes, media_type="image/png")
    except Exception as e:
        return JSONResponse({"status": False, "error": str(e)})


# ==========================================
# 7. WEBHOOK MANAGEMENT SYSTEM (PREFIX /api/webhook)
# ==========================================
WEBHOOKS_CONFIG_PATH = os.path.join(STORAGE_DIR, 'webhooks_config.json')

_webhook_cache:      dict  = {}
_webhook_cache_time: float = 0.0
_WEBHOOK_CACHE_TTL:  float = 5.0
_webhook_lock              = threading.Lock()


def _load_webhook_configs() -> dict:
    global _webhook_cache, _webhook_cache_time
    now = time.monotonic()
    with _webhook_lock:
        if _webhook_cache and (now - _webhook_cache_time) < _WEBHOOK_CACHE_TTL:
            return dict(_webhook_cache)
        if not os.path.exists(WEBHOOKS_CONFIG_PATH):
            _flush_cache_and_write({})
            return {}
        try:
            with open(WEBHOOKS_CONFIG_PATH, 'r') as f:
                data = json.load(f)
            _webhook_cache      = data
            _webhook_cache_time = now
            return dict(data)
        except (json.JSONDecodeError, OSError):
            _flush_cache_and_write({})
            return {}


def _save_webhook_configs(configs: dict) -> None:
    with _webhook_lock:
        _flush_cache_and_write(configs)


def _flush_cache_and_write(configs: dict) -> None:
    global _webhook_cache, _webhook_cache_time
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with open(WEBHOOKS_CONFIG_PATH, 'w') as f:
        json.dump(configs, f, indent=2)
    _webhook_cache      = dict(configs)
    _webhook_cache_time = time.monotonic()


async def _send_post_request(target_url: str, payload: dict, secret: str = "") -> dict:
    headers = {'Content-Type': 'application/json', 'User-Agent': 'NovaWebhook/1.0'}
    if secret:
        headers['X-Nova-Secret'] = secret
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(target_url, json=payload, headers=headers)
            return {"success": res.is_success, "http_code": res.status_code, "response": res.text[:500]}
    except httpx.TimeoutException:
        return {"success": False, "http_code": None, "response": "Request timeout"}
    except httpx.RequestError as e:
        return {"success": False, "http_code": None, "response": f"Request error: {str(e)}"}
    except Exception as e:
        return {"success": False, "http_code": None, "response": str(e)}


# --- Core logic create, dipakai GET & POST ---
def _do_webhook_create(target_url: str, event_name: str, secret: str) -> dict:
    if not target_url:
        return {"status": False, "error": "Field 'target_url' wajib diisi"}
    if not target_url.startswith(("http://", "https://")):
        return {"status": False, "error": "Field 'target_url' harus URL valid (http/https)"}
    if not event_name:
        return {"status": False, "error": "Field 'event_name' wajib diisi"}
    webhook_id  = uuid.uuid4().hex
    created_at  = datetime.utcnow().isoformat() + "Z"
    new_webhook = {
        "id": webhook_id, "target_url": target_url, "event_name": event_name,
        "secret": secret, "created_at": created_at,
        "trigger_count": 0, "last_triggered_at": None,
    }
    configs = _load_webhook_configs()
    configs[webhook_id] = new_webhook
    _save_webhook_configs(configs)
    return {"status": True, "message": "Webhook berhasil didaftarkan", "webhook": new_webhook}


@app.post("/api/webhook/create")
async def webhook_create_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})
    target_url = (body.get("target_url") or "").strip()
    event_name = (body.get("event_name") or "").strip()
    secret     = (body.get("secret") or "").strip()
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_webhook_create, target_url, event_name, secret)
    return JSONResponse(result) if not result.get("status") else result


@app.get("/api/webhook/create")
async def webhook_create_get(target_url: str = "", event_name: str = "", secret: str = ""):
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_webhook_create, target_url.strip(), event_name.strip(), secret.strip())
    return JSONResponse(result) if not result.get("status") else result


# --- List (sudah GET dari awal, tidak diubah) ---
@app.get("/api/webhook/list")
async def webhook_list(event_name: str = ""):
    try:
        loop    = asyncio.get_running_loop()
        configs = await loop.run_in_executor(None, _load_webhook_configs)
    except Exception as e:
        return JSONResponse({"status": False, "error": f"Gagal membaca konfigurasi: {str(e)}"})
    webhooks = list(configs.values())
    if event_name:
        webhooks = [w for w in webhooks if w.get("event_name") == event_name]
    webhooks.sort(key=lambda w: w.get("created_at", ""), reverse=True)
    return {"status": True, "count": len(webhooks), "webhooks": webhooks}


# --- Core logic delete, dipakai GET, POST & DELETE ---
def _do_webhook_delete(webhook_id: str) -> dict:
    if not webhook_id:
        return {"status": False, "error": "Field 'id' wajib diisi"}
    configs = _load_webhook_configs()
    if webhook_id not in configs:
        return {"status": False, "error": f"Webhook dengan ID '{webhook_id}' tidak ditemukan"}
    deleted = configs.pop(webhook_id)
    _save_webhook_configs(configs)
    return {"status": True, "message": f"Webhook '{webhook_id}' berhasil dihapus", "deleted": deleted}


@app.delete("/api/webhook/delete")
async def webhook_delete_delete(id: str = ""):
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_webhook_delete, id.strip())
    return JSONResponse(result) if not result.get("status") else result


@app.get("/api/webhook/delete")
async def webhook_delete_get(id: str = ""):
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_webhook_delete, id.strip())
    return JSONResponse(result) if not result.get("status") else result


@app.post("/api/webhook/delete")
async def webhook_delete_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})
    webhook_id = (body.get("id") or "").strip()
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_webhook_delete, webhook_id)
    return JSONResponse(result) if not result.get("status") else result


# --- Core logic trigger, dipakai GET & POST ---
async def _do_webhook_trigger(webhook_id: str, extra_payload: dict) -> dict:
    if not webhook_id:
        return {"status": False, "error": "Field 'id' wajib diisi"}
    if not isinstance(extra_payload, dict):
        return {"status": False, "error": "Field 'payload' harus berupa objek JSON"}
    loop    = asyncio.get_running_loop()
    configs = await loop.run_in_executor(None, _load_webhook_configs)
    if webhook_id not in configs:
        return {"status": False, "error": f"Webhook dengan ID '{webhook_id}' tidak ditemukan"}
    webhook      = configs[webhook_id]
    triggered_at = datetime.utcnow().isoformat() + "Z"
    event_payload = {
        "event": webhook["event_name"], "webhook_id": webhook_id,
        "triggered_at": triggered_at, "data": extra_payload,
    }
    delivery_result = await _send_post_request(
        target_url=webhook["target_url"], payload=event_payload,
        secret=webhook.get("secret", ""),
    )
    webhook["trigger_count"]     = webhook.get("trigger_count", 0) + 1
    webhook["last_triggered_at"] = triggered_at
    configs[webhook_id]          = webhook
    try:
        await loop.run_in_executor(None, _save_webhook_configs, configs)
    except Exception as e:
        delivery_result["storage_warning"] = f"Gagal update metadata: {str(e)}"
    return {
        "status": True, "message": "Webhook berhasil di-trigger",
        "webhook_id": webhook_id, "target_url": webhook["target_url"],
        "event_name": webhook["event_name"], "triggered_at": triggered_at,
        "trigger_count": webhook["trigger_count"],
        "delivery": delivery_result, "payload_sent": event_payload,
    }


@app.post("/api/webhook/trigger")
async def webhook_trigger_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": False, "error": "Request body harus berupa JSON yang valid"})
    webhook_id    = (body.get("id") or "").strip()
    extra_payload = body.get("payload") or {}
    result = await _do_webhook_trigger(webhook_id, extra_payload)
    return JSONResponse(result) if not result.get("status") else result


@app.get("/api/webhook/trigger")
async def webhook_trigger_get(id: str = "", payload: str = ""):
    """
    GET /api/webhook/trigger?id=xxx
    GET /api/webhook/trigger?id=xxx&payload={"key":"val"}  (payload = JSON string)
    """
    extra_payload = {}
    if payload:
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                extra_payload = parsed
            else:
                return JSONResponse({"status": False, "error": "Query param 'payload' harus berupa JSON object"})
        except json.JSONDecodeError:
            return JSONResponse({"status": False, "error": "Query param 'payload' bukan JSON yang valid"})
    result = await _do_webhook_trigger(id.strip(), extra_payload)
    return JSONResponse(result) if not result.get("status") else result


# ==========================================
# 🚨 MAGIC WORD BUAT VERCEL (WAJIB ADA) 🚨
# ==========================================
handler = Mangum(app)
