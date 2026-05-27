// /pages/api/fetch.js
// ✅ Auto-force download buat file binary (gambar, zip, video)
// ✅ Support parameter ?dl=1 buat maksa download apapun
// ✅ Bypass response limit biar bisa download file gede

export const config = {
  api: {
    responseLimit: false, // PENTING: Biar nggak dipotong Vercel pas download file gede
  },
};

export default async function handler(req, res) {
  // CORS Headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') {
    return res.status(405).json({ status: false, error: 'Method not allowed. Gunakan GET.' });
  }

  const { url, dl } = req.query;

  if (!url) {
    return res.status(400).json({ status: false, error: 'Parameter "url" wajib diisi.' });
  }

  let targetUrl;
  try {
    targetUrl = new URL(url);
    if (!['http:', 'https:'].includes(targetUrl.protocol)) throw new Error('Protocol tidak didukung');
  } catch (e) {
    return res.status(400).json({ status: false, error: 'URL tidak valid.' });
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch(targetUrl.toString(), {
      signal: controller.signal,
      headers: { 'User-Agent': 'SimpleProxy/1.0', 'Accept': '*/*' },
      redirect: 'follow'
    });

    clearTimeout(timeoutId);

    const contentType = response.headers.get('content-type') || 'application/octet-stream';
    const contentLength = response.headers.get('content-length');    
    // Deteksi apakah ini text/json atau binary (file)
    const isText = contentType.startsWith('text/') || 
                   contentType.includes('json') || 
                   contentType.includes('xml') || 
                   contentType.includes('javascript');

    // Ekstrak nama file dari URL (biar nama file downloadnya bagus)
    let filename = 'download';
    try {
      const pathParts = targetUrl.pathname.split('/');
      const lastPart = pathParts[pathParts.length - 1];
      if (lastPart && lastPart.includes('.')) {
        filename = decodeURIComponent(lastPart);
      }
    } catch (e) {}

    // Set Headers
    res.setHeader('Content-Type', contentType);
    if (contentLength) res.setHeader('Content-Length', contentLength);
    res.setHeader('Access-Control-Allow-Origin', '*');

    // ========================================
    // 🎯 LOGIKA FORCE DOWNLOAD (MAGICNYA DI SINI)
    // ========================================
    // Kalau user tambahin ?dl=1 ATAU kalau itu bukan text (binary file), PAKSA DOWNLOAD!
    if (dl === '1' || !isText) {
      res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    } else {
      // Kalau text/html/json, biarin inline (ditampilin di browser)
      const originalDisposition = response.headers.get('content-disposition');
      if (originalDisposition) res.setHeader('Content-Disposition', originalDisposition);
    }

    res.status(response.status);

    // Return response
    if (isText && dl !== '1') {
      const text = await response.text();
      return res.send(text);
    } else {
      // Binary / Force Download: baca sebagai buffer dan kirim
      const buffer = await response.arrayBuffer();
      return res.send(Buffer.from(buffer));
    }

  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      return res.status(504).json({ status: false, error: 'Request timeout (10 detik).' });    }
    return res.status(500).json({ status: false, error: `Gagal fetch URL: ${err.message}` });
  }
    }
