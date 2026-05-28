// pages/files.js - FIXED VERSION
import { useEffect, useState } from 'react';

const API_BASE = '/api';

function formatSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export default function FileManager() {
  const [storageFiles, setStorageFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploadingFile, setUploadingFile] = useState(null);
  const [instanceId, setInstanceId] = useState('');

  const loadAll = async () => {
    setLoading(true);
    try {
      // Ambil Instance ID biar kita tau lagi di server mana
      const idRes = await fetch(`${API_BASE}?code=${encodeURIComponent('import os; print(os.getpid())')}`);
      const idData = await idRes.json();
      if (idData.status) setInstanceId(idData.output.trim());

      // Load persistent storage (INI YANG PALING PENTING!)
      const sRes = await fetch(`${API_BASE}?action=storage&code=list`);
      const sData = await sRes.json();
      if (sData.status) setStorageFiles(sData.files || []);
      else setStorageFiles([]);
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const uploadTo0x0 = async (filename) => {
    setUploadingFile(filename);
    try {
      const dir = '/tmp/nova_storage';
      const readCode = `import os,base64\np=os.path.join('${dir}','${filename}')\nwith open(p,'rb') as f:print(base64.b64encode(f.read()).decode())`;
      const r1 = await fetch(`${API_BASE}?code=${encodeURIComponent(readCode)}`);
      const d1 = await r1.json();
      if (!d1.status) throw new Error('Read failed');

      const upCode = `import subprocess,base64,os\nd=base64.b64decode('${d1.output.trim()}')\nt='/tmp/_up_${Date.now()}'\nwith open(t,'wb') as f:f.write(d)\nr=subprocess.run(['curl','-s','-F',f'file=@{t}','https://0x0.st'],capture_output=True,text=True)\nos.remove(t)\nprint(r.stdout.strip())`;
      const r2 = await fetch(`${API_BASE}?code=${encodeURIComponent(upCode)}`);
      const d2 = await r2.json();
      if (!d2.status) throw new Error('Upload failed');
      window.open(d2.output.trim(), '_blank');    } catch (e) { alert('❌ Gagal: ' + e.message); }
    setUploadingFile(null);
  };

  useEffect(() => { loadAll(); }, []);

  return (
    <div style={{ fontFamily: 'system-ui,sans-serif', background: '#0f172a', color: '#e2e8f0', minHeight: '100vh', padding: 20 }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h1 style={{ textAlign: 'center', marginBottom: 10, fontSize: '2rem', background: 'linear-gradient(135deg,#3b82f6,#8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
          📂 Downloder File Manager
        </h1>
        
        {/* INSTANCE INFO */}
        <div style={{ textAlign: 'center', marginBottom: 20, padding: 10, background: '#1e293b', borderRadius: 8, border: '1px solid #334155' }}>
          <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>
            🖥️ Current Instance PID: <strong style={{ color: '#3b82f6' }}>{instanceId || '-'}</strong>
          </span>
          <p style={{ color: '#64748b', fontSize: '0.75rem', marginTop: 5 }}>
            ⚠️ Kalau PID berubah, berarti kamu lagi di server berbeda!
          </p>
        </div>

        <button onClick={loadAll} disabled={loading}
          style={{ display: 'block', margin: '0 auto 20px', background: '#10b981', color: '#fff', border: 'none', padding: '10px 30px', borderRadius: 8, cursor: 'pointer', fontSize: '1rem' }}>
          {loading ? '⏳ Loading...' : '🔄 Refresh Files'}
        </button>

        {/* HANYA TAMPILIN STORAGE DIR */}
        <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, marginBottom: 20, border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.2rem', marginBottom: 15 }}>
            💾 Persistent Storage 
            <span style={{ background: '#3b82f6', padding: '2px 10px', borderRadius: 20, fontSize: '0.8rem', marginLeft: 10 }}>
              /tmp/nova_storage
            </span>
          </h2>
          
          {storageFiles.length === 0 ? (
            <div style={{ textAlign: 'center', color: '#64748b', padding: 30 }}>
              📭 Storage kosong. Download file pakai Python dan simpan ke <code>STORAGE_DIR</code>!
            </div>
          ) : (
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {storageFiles.map(f => (
                <li key={f.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 15px', borderBottom: '1px solid #334155' }}>
                  <span style={{ wordBreak: 'break-all' }}>📄 {f.name}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{formatSize(f.size)}</span>
                    <button disabled={uploadingFile === f.name} onClick={() => uploadTo0x0(f.name)}
                      style={{ background: uploadingFile === f.name ? '#6d28d9' : '#8b5cf6', color: '#fff', border: 'none', padding: '6px 14px', borderRadius: 6, cursor: 'pointer', fontSize: '0.8rem' }}>                      {uploadingFile === f.name ? '⏳...' : '🔗 Get Link'}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div style={{ background: '#7f1d1d', borderRadius: 12, padding: 20, border: '1px solid #991b1b' }}>
          <h3 style={{ color: '#fca5a5', marginBottom: 10 }}>💡 Cara Download yang Benar:</h3>
          <pre style={{ background: '#450a0a', padding: 15, borderRadius: 8, overflowX: 'auto', fontSize: '0.85rem', color: '#fecaca' }}>
{`# JANGAN simpan ke /tmp langsung!
# Pakai STORAGE_DIR yang udah di-inject:

import os
path = os.path.join(STORAGE_DIR, 'file_kamu.txt')
with open(path, 'w') as f:
    f.write("Data penting!")
print("✅ Tersimpan di storage!")`}
          </pre>
        </div>
      </div>
    </div>
  );
          }
