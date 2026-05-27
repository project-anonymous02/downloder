import { loadPyodide } from 'pyodide';

// Cache global
let pyodideInstance = null;
let isInitializing = false;

async function getPyodide() {
  if (pyodideInstance) return pyodideInstance;
  
  if (!isInitializing) {
    isInitializing = true;
    try {
      console.log('⏳ Initializing Pyodide...');
      pyodideInstance = await loadPyodide({
        indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.24.1/full/'
      });
      
      // Pre-load library penting buat downloader biar cepet
      console.log('📦 Pre-loading common packages...');
      await pyodideInstance.loadPackage(['micropip', 'requests', 'beautifulsoup4']);
      
      // Install via micropip biar versi terbaru & kompatibel
      const micropip = pyodideInstance.pyimport('micropip');
      await micropip.install(['requests', 'beautifulsoup4']);
      
      console.log('✅ Pyodide Ready!');
    } catch (err) {
      console.error('Failed to init Pyodide:', err);
      throw err;
    } finally {
      isInitializing = false;
    }
  } else {
    // Tunggu sampai inisialisasi selesai jika ada request lain masuk barengan
    while (!pyodideInstance) {
      await new Promise(r => setTimeout(r, 100));
    }
  }
  return pyodideInstance;
}

export const config = {
  api: {
    bodyParser: { sizeLimit: '2mb' },
    responseLimit: false, // Penting buat response besar
  },
};

export default async function handler(req, res) {
  // CORS Headers  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const pyodide = await getPyodide();

    // Ambil input dari Query (GET) atau Body (POST)
    const { action, code, package: pkgName } = req.query;
    const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
    
    const finalAction = body.action || action || 'run';
    const finalCode = body.code || code || '';
    const finalPkg = body.package || pkgName || '';

    // ========================================
    // 📦 ACTION: INSTALL PACKAGE (Manual)
    // ========================================
    if (finalAction === 'install') {
      if (!finalPkg) throw new Error('Nama package wajib diisi');
      
      const micropip = pyodide.pyimport('micropip');
      await micropip.install(finalPkg);

      return res.status(200).json({
        status: true,
        message: `Package "${finalPkg}" berhasil diinstall session ini`,
        package: finalPkg
      });
    }

    // ========================================
    // 🏃 ACTION: RUN CODE
    // ========================================
    if (finalAction === 'run') {
      if (!finalCode.trim()) throw new Error('Kode Python wajib diisi');

      // Setup stdout/stderr capture
      pyodide.runPython(`
import sys
from io import StringIO
sys.stdout = StringIO()
sys.stderr = StringIO()
      `);

      let result;
      try {
        // Jalankan kode user        result = await pyodide.runPythonAsync(finalCode);
      } catch (pyErr) {
        const stderr = pyodide.runPython('sys.stderr.getvalue()');
        return res.status(400).json({
          status: false,
          error: 'Runtime Error',
          detail: stderr || pyErr.message,
          code: finalCode
        });
      }

      const stdout = pyodide.runPython('sys.stdout.getvalue()');
      const stderr = pyodide.runPython('sys.stderr.getvalue()');

      return res.status(200).json({
        status: true,
        output: stdout, // Gabung stdout biar gampang
        stderr: stderr,
        returnValue: result !== undefined ? String(result) : null,
      });
    }

    return res.status(400).json({
      status: false,
      error: 'Action tidak valid. Gunakan "run" atau "install".'
    });

  } catch (err) {
    console.error(err);
    return res.status(500).json({
      status: false,
      error: 'Server Internal Error: ' + err.message
    });
  }
    }
