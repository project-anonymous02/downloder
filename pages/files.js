// pages/files.js
import { useEffect, useState } from 'react';

const API_BASE = '/api';

function formatSize(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export default function FileManager() {
  const [storageFiles, setStorageFiles] = useState([]);
  const [tmpFiles, setTmpFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploadingFile, setUploadingFile] = useState(null);

  const loadAll = async () => {
    setLoading(true);
    try {
      // Load persistent storage
      const sRes = await fetch(`${API_BASE}/run?action=storage&code=list`);
      const sData = await sRes.json();
      if (sData.status) setStorageFiles(sData.files || []);

      // Load /tmp via shell
      const tRes = await fetch(`${API_BASE}/run?code=${encodeURIComponent('!ls -la /tmp | tail -n +2')}`);
      const tData = await tRes.json();
      if (tData.status && tData.output) {
        const files = tData.output.trim().split('\n').filter(l => l.trim()).map(line => {
          const parts = line.split(/\s+/);
          return { name: parts.slice(8).join(' '), size: parseInt(parts[4]) || 0 };
        }).filter(f => f.name && !f.name.startsWith('.') && f.name !== 'nova_storage');
        setTmpFiles(files);
      }
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const uploadTo0x0 = async (filename, dir) => {
    setUploadingFile(filename);
    try {
      const readCode = `import os,base64\np=os.path.join('${dir}','${filename}')\nwith open(p,'rb') as f:print(base64.b64encode(f.read()).decode())`;
      const r1 = await fetch(`${API_BASE}/run?code=${encodeURIComponent(readCode)}`);
      const d1 = await r1.json();
      if (!d1.status) throw new Error('Read failed');

      const upCode = `import subprocess,base64,os\nd=base64.b64decode('${d1.output.trim()}')\nt='/tmp/_up_${Date.now()}'\nwith open(t,'wb') as f:f.write(d)\nr=subprocess.run(['curl','-s','-F',f'file=@{t}','https://0x0.st'],capture_output=True,text=True)\nos.remove(t)\nprint(r.stdout.strip())`;      const r2 = await fetch(`${API_BASE}/run?code=${encodeURIComponent(upCode)}`);
      const d2 = await r2.json();
      if (!d2.status) throw new Error('Upload failed');
      window.open(d2.output.trim(), '_blank');
    } catch (e) { alert('❌ Gagal: ' + e.message); }
    setUploadingFile(null);
  };

  useEffect(() => { loadAll(); }, []);

  const FileList = ({ files, dir }) => (
    <ul style={{ listStyle: 'none', padding: 0 }}>
      {files.length === 0 ? <li style={{ textAlign: 'center', color: '#64748b', padding: 30 }}>📭 Kosong</li> :
        files.map(f => (
          <li key={f.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 15px', borderBottom: '1px solid #334155' }}>
            <span style={{ wordBreak: 'break-all' }}>📄 {f.name}</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{formatSize(f.size)}</span>
              <button disabled={uploadingFile === f.name} onClick={() => uploadTo0x0(f.name, dir)}
                style={{ background: uploadingFile === f.name ? '#6d28d9' : '#8b5cf6', color: '#fff', border: 'none', padding: '6px 14px', borderRadius: 6, cursor: 'pointer', fontSize: '0.8rem' }}>
                {uploadingFile === f.name ? '⏳...' : '🔗 Get Link'}
              </button>
            </div>
          </li>
        ))}
    </ul>
  );

  return (
    <div style={{ fontFamily: 'system-ui,sans-serif', background: '#0f172a', color: '#e2e8f0', minHeight: '100vh', padding: 20 }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h1 style={{ textAlign: 'center', marginBottom: 30, fontSize: '2rem', background: 'linear-gradient(135deg,#3b82f6,#8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
          📂 Downloder File Manager
        </h1>
        <button onClick={loadAll} disabled={loading}
          style={{ display: 'block', margin: '0 auto 20px', background: '#10b981', color: '#fff', border: 'none', padding: '10px 30px', borderRadius: 8, cursor: 'pointer', fontSize: '1rem' }}>
          {loading ? '⏳ Loading...' : '🔄 Refresh Files'}
        </button>

        <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, marginBottom: 20, border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.2rem', marginBottom: 15 }}>💾 Persistent Storage <span style={{ background: '#3b82f6', padding: '2px 10px', borderRadius: 20, fontSize: '0.8rem' }}>/tmp/nova_storage</span></h2>
          <FileList files={storageFiles} dir="/tmp/nova_storage" />
        </div>

        <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, marginBottom: 20, border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.2rem', marginBottom: 15 }}>🗂️ Temporary Files <span style={{ background: '#3b82f6', padding: '2px 10px', borderRadius: 20, fontSize: '0.8rem' }}>/tmp</span></h2>
          <FileList files={tmpFiles} dir="/tmp" />
        </div>

        <p style={{ textAlign: 'center', color: '#64748b', fontSize: '0.8rem', marginTop: 20 }}>          ⚠️ Vercel bersifat stateless. File di /tmp bisa hilang kapan saja. Gunakan Persistent Storage untuk file penting.
        </p>
      </div>
    </div>
  );
          }
