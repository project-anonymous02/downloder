import { loadPyodide } from 'pyodide';

// Cache module-level (dipertahankan Vercel selama instance warm)
let pyodideInstance = null;
let pyodideInitPromise = null;
const installedPkgs = new Set();

async function initPyodide() {
  if (pyodideInstance) return pyodideInstance;
  
  if (!pyodideInitPromise) {
    pyodideInitPromise = (async () => {
      pyodideInstance = await loadPyodide({
        indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.24.1/full/'
      });
      await pyodideInstance.loadPackage('micropip');
      return pyodideInstance;
    })();
  }
  return pyodideInitPromise;
}

export const config = {
  api: {
    bodyParser: { sizeLimit: '2mb' }
  }
};

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();

  // Normalize input (support GET query & POST body)
  const { action, code, package: pkgName } = req.query;
  const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
  const finalAction = body.action || action || 'run';
  const finalCode = body.code || code || '';
  const finalPkg = body.package || pkgName || '';

  try {
    const pyodide = await initPyodide();

    // ========================================
    // 📦 ACTION: INSTALL PACKAGE
    // ========================================
    if (finalAction === 'install') {
      if (!finalPkg) throw new Error('Parameter "package" wajib diisi');      
      if (installedPkgs.has(finalPkg)) {
        return res.status(200).json({
          status: true,
          message: `"${finalPkg}" sudah terinstall di memori instance`,
          package: finalPkg
        });
      }

      const micropip = pyodide.pyimport('micropip');
      await micropip.install(finalPkg);
      installedPkgs.add(finalPkg);

      return res.status(200).json({
        status: true,
        message: `"${finalPkg}" berhasil diinstall`,
        package: finalPkg
      });
    }

    // ========================================
    // 🏃 ACTION: EXECUTE CODE
    // ========================================
    if (finalAction === 'run') {
      if (!finalCode.trim()) throw new Error('Parameter "code" wajib diisi');

      // Redirect stdout/stderr buat capture output
      pyodide.runPython(`
import sys
from io import StringIO
sys.stdout = StringIO()
sys.stderr = StringIO()
      `);

      let returnValue;
      try {
        returnValue = await pyodide.runPythonAsync(finalCode);
      } catch (pyErr) {
        const stderr = pyodide.runPython('sys.stderr.getvalue()');
        return res.status(400).json({
          status: false,
          error: 'Python runtime error',
          stderr: stderr || pyErr.message,
          code: finalCode
        });
      }

      const stdout = pyodide.runPython('sys.stdout.getvalue()');
      const stderr = pyodide.runPython('sys.stderr.getvalue()');
      return res.status(200).json({
        status: true,
        stdout: stdout.trim(),
        stderr: stderr.trim(),
        returnValue: returnValue !== undefined ? String(returnValue) : null,
        code: finalCode
      });
    }

    return res.status(400).json({
      status: false,
      error: 'Action tidak dikenali. Gunakan "run" atau "install".'
    });

  } catch (err) {
    return res.status(500).json({
      status: false,
      error: `Server error: ${err.message}`
    });
  }
            }
