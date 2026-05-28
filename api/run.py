# /api/run.py - Hybrid Executor (Python + Shell Command)
from http.server import BaseHTTPRequestHandler
import json
import sys
import subprocess
import traceback
from io import StringIO
from urllib.parse import urlparse, parse_qs

class handler(BaseHTTPRequestHandler):
    
    # ========================================
    # 🟢 HANDLE GET (LEWAT URL)
    # ========================================
    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)
        
        code_list = query_params.get('code', [])
        code = code_list[0] if code_list else ''
        
        # Cek apakah ada parameter ?sh=1 atau ?cmd=1
        sh_flag = query_params.get('sh', ['0'])[0] == '1' or query_params.get('cmd', ['0'])[0] == '1'
        
        self._execute_and_respond(code, is_shell=sh_flag)

    # ========================================
    # 🔵 HANDLE POST (LEWAT BODY/JSON)
    # ========================================
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        
        try:
            data = json.loads(post_data.decode('utf-8'))
        except:
            data = {}
        
        code = data.get('code', '')
        is_shell = data.get('sh', False) or data.get('cmd', False)
        self._execute_and_respond(code, is_shell=is_shell)

    # ========================================
    # 🧠 SMART ROUTER (Deteksi Python atau Shell)
    # ========================================
    def _execute_and_respond(self, code, is_shell=False):
        if not code.strip():
            self._send_json(400, {'status': False, 'error': 'Code/Command required.'})
            return
                # MAGIC: Kalau kode diawali tanda '!', otomatis jadi Shell Command!
        if code.strip().startswith('!'):
            is_shell = True
            code = code.strip()[1:] # Hapus tanda '!'

        if is_shell:
            self._run_shell_command(code)
        else:
            self._run_python_code(code)

    # ========================================
    # 💀 RUN SHELL COMMAND (BASH/SH)
    # ========================================
    def _run_shell_command(self, cmd):
        try:
            # Jalanin command di shell, cwd=/tmp biar bebas nulis file
            # Timeout 9 detik biar nggak kena 504 Gateway Timeout Vercel (max 10s)
            result = subprocess.run(
                cmd, 
                shell=True, 
                capture_output=True, 
                text=True, 
                timeout=9,
                cwd='/tmp'
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
                'status': False,
                'mode': 'shell',
                'error': 'Timeout! Command kelewat lama (max 9 detik).'
            })
        except Exception as e:
            self._send_json(500, {
                'status': False,
                'mode': 'shell',
                'error': str(e)
            })

    # ========================================
    # 🐍 RUN PYTHON CODE
    # ========================================    def _run_python_code(self, code):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = mystdout = StringIO()
        sys.stderr = mystderr = StringIO()
        
        try:
            exec_globals = {'__builtins__': __builtins__}
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
                'stderr': mystderr.getvalue() or None
            })
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    # ========================================
    # 🛠️ HELPERS (CORS & JSON)
    # ========================================
    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors()
        self.end_headers()
    
    def _set_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
    
    def _send_json(self, status_code, data):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self._set_cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))
    
    def log_message(self, format, *args):
        pass
