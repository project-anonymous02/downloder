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

STORAGE_DIR = "/tmp/nova_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

SHELL_BLACKLIST = [
    r'\brm\s+-rf\s+/', r'\bmkfs\b', r'\bdd\s+if=', r':\(\)\{',
    r'\bapt\b', r'\byum\b', r'\bshutdown\b', r'\breboot\b'
]

# ⚠️ INI KUNCINYA: HARUS CLASS Bernama "handler" (Bukan def function)
class handler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)

        code = query_params.get('code', [''])[0]
        action = query_params.get('action', ['run'])[0]
        sh_flag = query_params.get('sh', ['0'])[0] == '1' or query_params.get('cmd', ['0'])[0] == '1'
        timeout = int(query_params.get('timeout', ['9'])[0])
        payload = query_params.get('payload', [''])[0]

        self._process_request(action, code, sh_flag, timeout, payload)

    def do_POST(self):
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
                if code.strip().startswith('!'):
                    sh_flag = True
                    code = code.strip()[1:]
                if sh_flag:
                    self._run_shell_command(code, timeout)
                else:
                    self._run_python_code(code)
            else:
                self._send_json(400, {'status': False, 'error': f'Action "{action}" tidak dikenali.'})
        except Exception as e:
            self._send_json(500, {'status': False, 'error': str(e), 'duration_ms': round((time.time() - start_time) * 1000, 2)})

    def _run_shell_command(self, cmd, timeout):
        for pattern in SHELL_BLACKLIST:
            if re.search(pattern, cmd):
                return self._send_json(403, {'status': False, 'mode': 'shell', 'error': f'Blocked: {pattern}'})
        try:
            max_timeout = min(timeout, 55)
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=max_timeout, cwd='/tmp')
            self._send_json(200, {'status': True, 'mode': 'shell', 'command': cmd, 'output': result.stdout, 'stderr': result.stderr or None, 'exit_code': result.returncode})
        except subprocess.TimeoutExpired:
            self._send_json(408, {'status': False, 'mode': 'shell', 'error': f'Timeout {max_timeout}s.'})
        except Exception as e:
            self._send_json(500, {'status': False, 'mode': 'shell', 'error': str(e)})

    def _run_python_code(self, code):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = mystdout = StringIO()
        sys.stderr = mystderr = StringIO()
        exec_globals = {'__builtins__': __builtins__, 'os': os, 'subprocess': subprocess, 'STORAGE_DIR': STORAGE_DIR}
        try:
            exec(code, exec_globals)
            self._send_json(200, {'status': True, 'mode': 'python', 'output': mystdout.getvalue(), 'stderr': mystderr.getvalue() or None, 'code': code})        except Exception as e:
            self._send_json(400, {'status': False, 'mode': 'python', 'error': str(e), 'detail': traceback.format_exc(), 'stderr': mystderr.getvalue() or None, 'code': code})
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def _handle_storage(self, cmd, payload):
        if cmd == 'save' and payload:
            try:
                filename, b64content = payload.split(',', 1)
                content = base64.b64decode(b64content)
                filepath = os.path.join(STORAGE_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(content)
                self._send_json(200, {'status': True, 'action': 'save', 'message': f'Saved {filename}'})
            except Exception as e:
                self._send_json(400, {'status': False, 'error': str(e)})
        elif cmd == 'load':
            filepath = os.path.join(STORAGE_DIR, payload)
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    content = base64.b64encode(f.read()).decode('utf-8')
                self._send_json(200, {'status': True, 'action': 'load', 'filename': payload, 'content_b64': content})
            else:
                self._send_json(404, {'status': False, 'error': 'File not found.'})
        elif cmd == 'list':
            files = [{'name': f, 'size': os.path.getsize(os.path.join(STORAGE_DIR, f))} for f in os.listdir(STORAGE_DIR)]
            self._send_json(200, {'status': True, 'action': 'list', 'files': files})
        else:
            self._send_json(400, {'status': False, 'error': 'Invalid storage cmd.'})

    def _handle_batch(self, code, timeout):
        try:
            commands = json.loads(code)
            if not isinstance(commands, list): raise ValueError("Must be list")
        except Exception as e:
            return self._send_json(400, {'status': False, 'error': str(e)})
        results = []
        max_t = max(1, min(timeout, 55) // max(1, len(commands)))
        for cmd in commands:
            is_shell = cmd.strip().startswith('!')
            if is_shell: cmd = cmd.strip()[1:]
            start_t = time.time()
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = StringIO(), StringIO()
            res = {'command': cmd, 'mode': 'shell' if is_shell else 'python'}
            try:
                if is_shell:
                    blocked = any(re.search(p, cmd) for p in SHELL_BLACKLIST)
                    if blocked:
                        res.update({'status': False, 'error': 'Blocked'})                    else:
                        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=max_t, cwd='/tmp')
                        res.update({'status': True, 'output': r.stdout, 'stderr': r.stderr, 'exit_code': r.returncode})
                else:
                    exec(cmd, {'__builtins__': __builtins__, 'os': os, 'subprocess': subprocess, 'STORAGE_DIR': STORAGE_DIR})
                    res.update({'status': True, 'output': sys.stdout.getvalue(), 'stderr': sys.stderr.getvalue()})
            except Exception as e:
                res.update({'status': False, 'error': str(e)})
            finally:
                sys.stdout, sys.stderr = old_out, old_err
            res['duration_ms'] = round((time.time() - start_t) * 1000, 2)
            results.append(res)
        self._send_json(200, {'status': True, 'mode': 'batch', 'results': results})

    def _set_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_json(self, status_code, data):
        if self.wfile.closed: return
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self._set_cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

    def log_message(self, format, *args):
        pass
