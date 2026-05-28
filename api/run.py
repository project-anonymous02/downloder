# api/run.py - Vercel Serverless Function Format
import json
import os
import sys
import subprocess
import traceback
import time
import base64
import re
from io import StringIO

# Folder penyimpanan data
STORAGE_DIR = "/tmp/nova_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Blacklist command berbahaya
SHELL_BLACKLIST = [
    r'\brm\s+-rf\s+/', r'\bmkfs\b', r'\bdd\s+if=', r':\(\)\{',
    r'\bapt\b', r'\byum\b', r'\bshutdown\b', r'\breboot\b'
]

def handler(event, context):
    # 1. Parse query parameters & body
    query = event.get('queryStringParameters', {})
    body = event.get('body', '')
    
    # 2. Deteksi mode (Python/Shell)
    code = query.get('code', body)
    action = query.get('action', 'run')
    sh_flag = query.get('sh', '0') == '1' or query.get('cmd', '0') == '1'
    timeout = int(query.get('timeout', '9'))
    payload = query.get('payload', '')

    # 3. Handle request
    try:
        if action == 'storage':
            return handle_storage(code, payload)
        elif action == 'batch':
            return handle_batch(code, timeout)
        elif action == 'run' or action == '':
            if not code.strip():
                return error_response(400, 'Code/Command required.')
            
            # Deteksi Shell Mode
            if code.strip().startswith('!'):
                sh_flag = True
                code = code.strip()[1:]
                
            if sh_flag:
                return run_shell_command(code, timeout)            else:
                return run_python_code(code)
        else:
            return error_response(400, f'Action "{action}" tidak dikenali.')
            
    except Exception as e:
        return error_response(500, str(e))

# ========================================
# 🐍 PYTHON EXECUTOR
# ========================================
def run_python_code(code):
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
        return success_response({
            'status': True,
            'mode': 'python',
            'output': mystdout.getvalue(),
            'stderr': mystderr.getvalue() or None,
            'code': code
        })
    except Exception as e:
        return error_response(400, str(e), {
            'detail': traceback.format_exc(),
            'stderr': mystderr.getvalue() or None,
            'code': code
        })
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

# ========================================
# 💀 SHELL COMMAND EXECUTOR
# ========================================
def run_shell_command(cmd, timeout):
    # Sanitasi command
    for pattern in SHELL_BLACKLIST:
        if re.search(pattern, cmd):
            return error_response(403, f'Command ditolak: {pattern}')
        try:
        max_timeout = min(timeout, 55)
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=max_timeout,
            cwd='/tmp'
        )
        
        return success_response({
            'status': True,
            'mode': 'shell',
            'command': cmd,
            'output': result.stdout,
            'stderr': result.stderr or None,
            'exit_code': result.returncode
        })
    except subprocess.TimeoutExpired:
        return error_response(408, f'Timeout! Max {max_timeout} detik.')
    except Exception as e:
        return error_response(500, str(e))

# ========================================
# 📦 STORAGE HANDLER
# ========================================
def handle_storage(cmd, payload):
    if cmd == 'save' and payload:
        try:
            filename, b64content = payload.split(',', 1)
            content = base64.b64decode(b64content)
            filepath = os.path.join(STORAGE_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(content)
            return success_response({
                'status': True,
                'action': 'save',
                'message': f'File {filename} disimpan.'
            })
        except Exception as e:
            return error_response(400, str(e))
    
    elif cmd == 'load':
        filepath = os.path.join(STORAGE_DIR, payload)
        if os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                content = base64.b64encode(f.read()).decode('utf-8')
            return success_response({
                'status': True,                'action': 'load',
                'filename': payload,
                'content_b64': content
            })
        return error_response(404, 'File tidak ditemukan.')
    
    elif cmd == 'list':
        files = [{
            'name': f,
            'size': os.path.getsize(os.path.join(STORAGE_DIR, f))
        } for f in os.listdir(STORAGE_DIR)]
        return success_response({
            'status': True,
            'action': 'list',
            'files': files
        })
    
    return error_response(400, 'Command storage tidak valid.')

# ========================================
# ⚡ BATCH EXECUTOR
# ========================================
def handle_batch(code, timeout):
    try:
        commands = json.loads(code)
        if not isinstance(commands, list):
            raise ValueError("Batch harus berupa array")
    except Exception as e:
        return error_response(400, f'Format batch salah: {str(e)}')
    
    results = []
    max_timeout_per_cmd = max(1, min(timeout, 55) // len(commands))
    
    for cmd in commands:
        is_shell = cmd.strip().startswith('!')
        if is_shell: cmd = cmd.strip()[1:]
        
        start_t = time.time()
        
        try:
            if is_shell:
                # Sanitasi
                blocked = any(re.search(p, cmd) for p in SHELL_BLACKLIST)
                if blocked:
                    res = {'status': False, 'error': 'Command diblokir'}
                else:
                    r = subprocess.run(
                        cmd, 
                        shell=True, 
                        capture_output=True,                         text=True, 
                        timeout=max_timeout_per_cmd,
                        cwd='/tmp'
                    )
                    res = {
                        'status': True,
                        'output': r.stdout,
                        'stderr': r.stderr,
                        'exit_code': r.returncode
                    }
            else:
                old_stdout, old_stderr = sys.stdout, sys.stderr
                sys.stdout = mystdout = StringIO()
                sys.stderr = mystderr = StringIO()
                exec(cmd, {'__builtins__': __builtins__, 'os': os, 'subprocess': subprocess})
                res = {
                    'status': True,
                    'output': mystdout.getvalue(),
                    'stderr': mystderr.getvalue()
                }
                sys.stdout, sys.stderr = old_stdout, old_stderr
        except Exception as e:
            res = {'status': False, 'error': str(e)}
        
        res.update({
            'command': cmd,
            'mode': 'shell' if is_shell else 'python',
            'duration_ms': round((time.time() - start_t) * 1000, 2)
        })
        results.append(res)
    
    return success_response({
        'status': True,
        'mode': 'batch',
        'results': results
    })

# ========================================
# 🛠️ HELPERS
# ========================================
def success_response(data):
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(data, indent=2)
    }

def error_response(status, message, extra=None):
    response = {
        'status': False,        'error': message
    }
    if extra:
        response.update(extra)
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(response, indent=2)
}
