# /api/run.py - Ultimate God Mode Executor
from http.server import BaseHTTPRequestHandler
import json
import sys
import os
import subprocess
import traceback
import time
import base64
import re
from io import StringIO
from urllib.parse import urlparse, parse_qs

# ========================================
# 🔒 KONFIGURASI KEAMANAN & STORAGE
# ========================================
API_SECRET = os.environ.get("API_SECRET", "")
STORAGE_DIR = "/tmp/nova_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Blacklist command shell
SHELL_BLACKLIST = [
    r'\brm\s+-rf\s+/', r'\bmkfs\b', r'\bdd\s+if=', r':\(\)\{',
    r'\bapt\b', r'\byum\b', r'\bshutdown\b', r'\breboot\b'
]

class handler(BaseHTTPRequestHandler):

    # ========================================
    # 🚦 ROUTER UTAMA (GET & POST)
    # ========================================
    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)

        # Validasi Auth
        token = self.headers.get('Authorization', '').replace('Bearer ', '') or query_params.get('token', [''])[0]
        if API_SECRET and token != API_SECRET:
            return self._send_json(401, {'status': False, 'error': 'Unauthorized. Token salah atau kosong.'})

        code = query_params.get('code', [''])[0]
        action = query_params.get('action', ['run'])[0]
        sh_flag = query_params.get('sh', ['0'])[0] == '1' or query_params.get('cmd', ['0'])[0] == '1'
        timeout = int(query_params.get('timeout', ['9'])[0])
        payload = query_params.get('payload', [''])[0]

        self._process_request(action, code, sh_flag, timeout, payload)

    def do_POST(self):
        # Validasi Auth        token = self.headers.get('Authorization', '').replace('Bearer ', '')
        if API_SECRET and token != API_SECRET:
            return self._send_json(401, {'status': False, 'error': 'Unauthorized. Token salah atau kosong.'})

        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'

        try:
            data = json.loads(post_data.decode('utf-8'))
        except:
            data = {}

        action = data.get('action', 'run')
        code = data.get('code', '')
        sh_flag = data.get('sh', False) or data.get('cmd', False)
        timeout = int(data.get('timeout', 9))
        payload = data.get('payload', '')

        self._process_request(action, code, sh_flag, timeout, payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors()
        self.end_headers()

    # ========================================
    # 🧠 PUSAT LOGIKA (ACTION ROUTER)
    # ========================================
    def _process_request(self, action, code, sh_flag, timeout, payload):
        start_time = time.time()

        try:
            if action == 'storage':
                self._handle_storage(code, payload)
            elif action == 'batch':
                self._handle_batch(code, timeout)
            elif action == 'run' or action == '':
                if not code.strip():
                    return self._send_json(400, {'status': False, 'error': 'Code/Command required.'})

                # Deteksi Shell Mode otomatis
                if code.strip().startswith('!'):
                    sh_flag = True
                    code = code.strip()[1:]

                if sh_flag:
                    self._run_shell_command(code, timeout)
                else:
                    self._run_python_code(code)
            else:                self._send_json(400, {'status': False, 'error': f'Action "{action}" tidak dikenali. Gunakan: run, batch, storage.'})

        except Exception as e:
            self._send_json(500, {
                'status': False,
                'error': 'Internal Server Error',
                'detail': str(e),
                'duration_ms': round((time.time() - start_time) * 1000, 2)
            })

    # ========================================
    # 💀 1. SHELL COMMAND EXECUTOR
    # ========================================
    def _run_shell_command(self, cmd, timeout):
        # Validasi & Sanitasi
        for pattern in SHELL_BLACKLIST:
            if re.search(pattern, cmd):
                return self._send_json(403, {'status': False, 'mode': 'shell', 'error': f'Command ditolak oleh security policy: {pattern}'})

        try:
            max_timeout = min(timeout, 55)

            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=max_timeout, cwd='/tmp'
            )

            self._send_json(200, {
                'status': True,
                'mode': 'shell',
                'command': cmd,
                'output': result.stdout,
                'stderr': result.stderr or None,
                'exit_code': result.returncode
            })
        except subprocess.TimeoutExpired:
            self._send_json(408, {
                'status': False, 'mode': 'shell',
                'error': f'Timeout! Command kelewat lama (max {max_timeout} detik).'
            })
        except Exception as e:
            self._send_json(500, {'status': False, 'mode': 'shell', 'error': str(e)})

    # ========================================
    # 🐍 2. PYTHON CODE EXECUTOR
    # ========================================
    def _run_python_code(self, code):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = mystdout = StringIO()
        sys.stderr = mystderr = StringIO()
        exec_globals = {
            '__builtins__': __builtins__,
            'os': os,
            'subprocess': subprocess,
            'STORAGE_DIR': STORAGE_DIR
        }

        try:
            exec(code, exec_globals)

            self._send_json(200, {
                'status': True,
                'mode': 'python',
                'output': mystdout.getvalue(),
                'stderr': mystderr.getvalue() or None,
                'code': code
            })
        except Exception as e:
            self._send_json(400, {
                'status': False,
                'mode': 'python',
                'error': str(e),
                'detail': traceback.format_exc(),
                'stderr': mystderr.getvalue() or None,
                'code': code
            })
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    # ========================================
    # 📦 3. PERSISTENT STORAGE HANDLER
    # ========================================
    def _handle_storage(self, cmd, payload):
        if cmd == 'save' and payload:
            try:
                filename, b64content = payload.split(',', 1)
                content = base64.b64decode(b64content)
                filepath = os.path.join(STORAGE_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(content)
                self._send_json(200, {'status': True, 'action': 'save', 'message': f'File {filename} disimpan.', 'path': filepath})
            except Exception as e:
                self._send_json(400, {'status': False, 'error': f'Gagal save: {str(e)}'})

        elif cmd == 'load':
            filepath = os.path.join(STORAGE_DIR, payload)
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    content = base64.b64encode(f.read()).decode('utf-8')                self._send_json(200, {'status': True, 'action': 'load', 'filename': payload, 'content_b64': content})
            else:
                self._send_json(404, {'status': False, 'error': 'File tidak ditemukan di storage.'})

        elif cmd == 'list':
            files = [{'name': f, 'size': os.path.getsize(os.path.join(STORAGE_DIR, f))} for f in os.listdir(STORAGE_DIR)]
            self._send_json(200, {'status': True, 'action': 'list', 'files': files})
        else:
            self._send_json(400, {'status': False, 'error': 'Storage command tidak valid. Gunakan: save, load, list.'})

    # ========================================
    # ⚡ 4. BATCH EXECUTOR
    # ========================================
    def _handle_batch(self, code, timeout):
        try:
            commands = json.loads(code)
            if not isinstance(commands, list):
                raise ValueError("Payload batch harus berupa array/list.")
        except Exception as e:
            return self._send_json(400, {'status': False, 'error': f'Format JSON batch salah: {str(e)}'})

        results = []
        max_timeout_per_cmd = max(1, min(timeout, 55) // len(commands))

        for cmd in commands:
            is_shell = False
            if cmd.strip().startswith('!'):
                is_shell = True
                cmd = cmd.strip()[1:]

            start_t = time.time()

            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = mystdout = StringIO()
            sys.stderr = mystderr = StringIO()

            res = {'command': cmd, 'mode': 'shell' if is_shell else 'python'}

            try:
                if is_shell:
                    blocked = False
                    for pattern in SHELL_BLACKLIST:
                        if re.search(pattern, cmd):
                            res.update({'status': False, 'error': f'Blocked by policy: {pattern}'})
                            blocked = True
                            break
                    if not blocked:
                        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=max_timeout_per_cmd, cwd='/tmp')
                        res.update({'status': True, 'output': r.stdout, 'stderr': r.stderr, 'exit_code': r.returncode})
                else:                    exec(cmd, {'__builtins__': __builtins__, 'os': os, 'subprocess': subprocess, 'STORAGE_DIR': STORAGE_DIR})
                    res.update({'status': True, 'output': mystdout.getvalue(), 'stderr': mystderr.getvalue()})
            except Exception as e:
                res.update({'status': False, 'error': str(e), 'stderr': mystderr.getvalue()})
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

            res['duration_ms'] = round((time.time() - start_t) * 1000, 2)
            results.append(res)

        self._send_json(200, {'status': True, 'mode': 'batch', 'results': results})

    # ========================================
    # 🛠️ HELPERS
    # ========================================
    def _set_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def _send_json(self, status_code, data):
        if self.wfile.closed:
            return

        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self._set_cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

    def log_message(self, format, *args):
        pass
