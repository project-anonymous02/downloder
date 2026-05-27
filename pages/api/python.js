// /pages/api/python.js
// ✅ Compatible dengan Next.js Pages Router
// ✅ Dynamic import biar Pyodide nggak di-bundle saat build
// ✅ Tidak pakai server-only (Pages Router udah server-side otomatis)

export const config = {
  api: {
    bodyParser: { sizeLimit: '4mb' },
    responseLimit: false,
  },
};

// Helper: Load Pyodide hanya saat runtime (bukan build time)
async function loadPythonRuntime() {
  // Dynamic import - ini KUNCI biar build nggak error
  const pyodideModule = await import('pyodide');
  const { loadPyodide } = pyodideModule;
  
  console.log('⏳ Loading Pyodide...');
  const pyodide = await loadPyodide({
    indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.24.1/full/',
  });
  
  console.log('📦 Installing packages...');
  await pyodide.loadPackage('micropip');
  const micropip = pyodide.pyimport('micropip');
  await micropip.install(['requests', 'beautifulsoup4']);
  
  console.log('✅ Pyodide ready');
  return pyodide;
}

export default async function handler(req, res) {
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  // Handle preflight
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  // Parse input dari query (GET) atau body (POST)
  const query = req.query || {};
  const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
  
  const action = body.action || query.action || 'run';
  const code = body.code || query.code || '';
  const pkg = body.package || query.package || '';
  try {
    // Load Pyodide saat runtime (bukan build time)
    const pyodide = await loadPythonRuntime();

    // ========================================
    // 📦 ACTION: Install package
    // ========================================
    if (action === 'install') {
      if (!pkg) {
        return res.status(400).json({ 
          status: false, 
          error: 'Package name required' 
        });
      }
      
      const micropip = pyodide.pyimport('micropip');
      await micropip.install(pkg);
      
      return res.status(200).json({ 
        status: true, 
        message: `Installed: ${pkg}`,
        package: pkg 
      });
    }

    // ========================================
    // 🏃 ACTION: Run Python code
    // ========================================
    if (action === 'run') {
      if (!code?.trim()) {
        return res.status(400).json({ 
          status: false, 
          error: 'Python code required' 
        });
      }

      // Setup output capture
      pyodide.runPython(`
import sys
from io import StringIO
sys.stdout = StringIO()
sys.stderr = StringIO()
      `);

      let result;
      try {
        result = await pyodide.runPythonAsync(code);
      } catch (pyErr) {
        const stderr = pyodide.runPython('sys.stderr.getvalue()');        return res.status(400).json({
          status: false,
          error: 'Python Error',
          detail: stderr || pyErr.message,
          code: code.substring(0, 200)
        });
      }

      const stdout = pyodide.runPython('sys.stdout.getvalue()');
      const stderr = pyodide.runPython('sys.stderr.getvalue()');

      return res.status(200).json({
        status: true,
        output: stdout,
        stderr: stderr || null,
        returnValue: result !== undefined ? String(result) : null
      });
    }

    return res.status(400).json({ 
      status: false, 
      error: 'Unknown action. Use "run" or "install"' 
    });

  } catch (err) {
    console.error('[Python API Error]', err);
    return res.status(500).json({
      status: false,
      error: 'Internal Server Error',
      detail: err.message
    });
  }
        }
