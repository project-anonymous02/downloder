# api/index.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json, sys, os, subprocess, traceback, time, base64, re
from io import StringIO

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STORAGE_DIR = "/tmp/nova_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)
SHELL_BLACKLIST = [r'\brm\s+-rf\s+/', r'\bmkfs\b', r'\bdd\s+if=', r':\(\)\{', r'\bapt\b', r'\byum\b', r'\bshutdown\b', r'\breboot\b']

def run_shell(cmd, timeout):
    for p in SHELL_BLACKLIST:
        if re.search(p, cmd): return JSONResponse(status_code=403, content={'status': False, 'error': f'Blocked: {p}'})
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=min(timeout, 55), cwd='/tmp')
        return {'status': True, 'mode': 'shell', 'command': cmd, 'output': r.stdout, 'stderr': r.stderr or None, 'exit_code': r.returncode}
    except subprocess.TimeoutExpired: return JSONResponse(status_code=408, content={'status': False, 'error': 'Timeout'})
    except Exception as e: return JSONResponse(status_code=500, content={'status': False, 'error': str(e)})

def run_python(code):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = StringIO(), StringIO()
    try:
        exec(code, {'__builtins__': __builtins__, 'os': os, 'subprocess': subprocess, 'STORAGE_DIR': STORAGE_DIR})
        return {'status': True, 'mode': 'python', 'output': sys.stdout.getvalue(), 'stderr': sys.stderr.getvalue() or None, 'code': code}
    except Exception as e:
        return JSONResponse(status_code=400, content={'status': False, 'mode': 'python', 'error': str(e), 'detail': traceback.format_exc(), 'stderr': sys.stderr.getvalue() or None, 'code': code})
    finally: sys.stdout, sys.stderr = old_out, old_err

@app.api_route("/", methods=["GET", "POST"])
async def god_mode(request: Request, code: str = "", action: str = "run", sh: str = "0", cmd: str = "0", timeout: int = 9, payload: str = ""):
    if request.method == "POST":
        try:
            body = await request.json()
            code, action, sh, cmd, timeout, payload = body.get("code", code), body.get("action", action), str(body.get("sh", sh)), str(body.get("cmd", cmd)), body.get("timeout", timeout), body.get("payload", payload)
        except: pass
    
    sh_flag = sh == '1' or cmd == '1'
    timeout = int(timeout)

    if action == 'storage':
        if code == 'save' and payload:
            try:
                f, b = payload.split(',', 1)
                with open(os.path.join(STORAGE_DIR, f), 'wb') as fp: fp.write(base64.b64decode(b))
                return {'status': True, 'message': f'Saved {f}'}
            except Exception as e: return JSONResponse(status_code=400, content={'status': False, 'error': str(e)})
        elif code == 'load':
            p = os.path.join(STORAGE_DIR, payload)
            if os.path.exists(p):
                with open(p, 'rb') as fp: return {'status': True, 'content_b64': base64.b64encode(fp.read()).decode()}
            return JSONResponse(status_code=404, content={'status': False, 'error': 'Not found'})
        elif code == 'list':
            return {'status': True, 'files': [{'name': f, 'size': os.path.getsize(os.path.join(STORAGE_DIR, f))} for f in os.listdir(STORAGE_DIR)]}
        return JSONResponse(status_code=400, content={'status': False, 'error': 'Invalid storage cmd'})

    elif action == 'batch':
        try:
            cmds = json.loads(code)
            res = []
            max_t = max(1, min(timeout, 55) // max(1, len(cmds)))
            for c in cmds:
                is_sh = c.strip().startswith('!')
                if is_sh: c = c.strip()[1:]
                st = time.time()
                if is_sh:
                    r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=max_t, cwd='/tmp')
                    res.append({'command': c, 'mode': 'shell', 'status': True, 'output': r.stdout, 'stderr': r.stderr, 'exit_code': r.returncode, 'duration_ms': round((time.time()-st)*1000, 2)})
                else:
                    old_out, old_err = sys.stdout, sys.stderr
                    sys.stdout, sys.stderr = StringIO(), StringIO()
                    try:
                        exec(c, {'__builtins__': __builtins__, 'os': os, 'subprocess': subprocess, 'STORAGE_DIR': STORAGE_DIR})
                        res.append({'command': c, 'mode': 'python', 'status': True, 'output': sys.stdout.getvalue(), 'stderr': sys.stderr.getvalue(), 'duration_ms': round((time.time()-st)*1000, 2)})
                    except Exception as e: res.append({'command': c, 'mode': 'python', 'status': False, 'error': str(e), 'duration_ms': round((time.time()-st)*1000, 2)})
                    finally: sys.stdout, sys.stderr = old_out, old_err
            return {'status': True, 'mode': 'batch', 'results': res}
        except Exception as e: return JSONResponse(status_code=400, content={'status': False, 'error': str(e)})

    else:
        if not code.strip(): return JSONResponse(status_code=400, content={'status': False, 'error': 'Code required'})
        if code.strip().startswith('!'): sh_flag, code = True, code.strip()[1:]
        return run_shell(code, timeout) if sh_flag else run_python(code)
