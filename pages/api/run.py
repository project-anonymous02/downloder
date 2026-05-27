# /api/run.py - Python Native Vercel Function
from http.server import BaseHTTPRequestHandler
import json
import sys
import traceback
from io import StringIO

class handler(BaseHTTPRequestHandler):
    
    def do_POST(self):
        # Baca request body
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        
        try:
            data = json.loads(post_data.decode('utf-8'))
        except:
            data = {}
        
        code = data.get('code', '')
        
        # Validasi
        if not code.strip():
            self._send_json(400, {
                'status': False,
                'error': 'Python code required'
            })
            return
        
        # Capture stdout & stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = mystdout = StringIO()
        sys.stderr = mystderr = StringIO()
        
        try:
            # Execute kode Python user
            exec_globals = {'__builtins__': __builtins__}
            exec(code, exec_globals)
            
            stdout_val = mystdout.getvalue()
            stderr_val = mystderr.getvalue()
            
            self._send_json(200, {
                'status': True,
                'output': stdout_val,
                'stderr': stderr_val or None,
                'code': code
            })
            
        except Exception as e:
            stderr_val = mystderr.getvalue()
            error_traceback = traceback.format_exc()
            
            self._send_json(400, {
                'status': False,
                'error': str(e),
                'detail': error_traceback,
                'stderr': stderr_val or None
            })
            
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
    
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
    
    # Suppress default logging ke stderr
    def log_message(self, format, *args):
        pass
