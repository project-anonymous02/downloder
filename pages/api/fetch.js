// /pages/api/fetch.js
// API Proxy sederhana — fetch URL eksternal dan return response aslinya

export default async function handler(req, res) {
  // ========================================
  // 1. Setup CORS headers (biar bisa dipanggil dari mana aja)
  // ========================================
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  // Handle preflight request
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  // Cuma terima GET
  if (req.method !== 'GET') {
    return res.status(405).json({
      status: false,
      error: 'Method not allowed. Gunakan GET.'
    });
  }

  // ========================================
  // 2. Validasi parameter URL
  // ========================================
  const { url } = req.query;

  if (!url) {
    return res.status(400).json({
      status: false,
      error: 'Parameter "url" wajib diisi. Contoh: /api/fetch?url=https://example.com'
    });
  }

  // Validasi format URL
  let targetUrl;
  try {
    targetUrl = new URL(url);
    // Biar aman, cuma izinin http & https
    if (!['http:', 'https:'].includes(targetUrl.protocol)) {
      throw new Error('Protocol tidak didukung');
    }
  } catch (e) {
    return res.status(400).json({
      status: false,
      error: 'URL tidak valid. Pastikan pakai http:// atau https://'
    });
  }
  // ========================================
  // 3. Fetch dengan timeout 10 detik
  // ========================================
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch(targetUrl.toString(), {
      signal: controller.signal,
      headers: {
        'User-Agent': 'SimpleProxy/1.0',
        'Accept': '*/*'
      },
      redirect: 'follow'
    });

    clearTimeout(timeoutId);

    // ========================================
    // 4. Forward headers penting dari response asli
    // ========================================
    const contentType = response.headers.get('content-type') || 'application/octet-stream';
    const contentLength = response.headers.get('content-length');
    const contentDisposition = response.headers.get('content-disposition');

    res.setHeader('Content-Type', contentType);
    if (contentLength) res.setHeader('Content-Length', contentLength);
    if (contentDisposition) res.setHeader('Content-Disposition', contentDisposition);

    // Tetep pertahanin CORS
    res.setHeader('Access-Control-Allow-Origin', '*');

    // Forward status code asli
    res.status(response.status);

    // ========================================
    // 5. Return response (stream untuk binary, text untuk text-based)
    // ========================================
    const isText = contentType.startsWith('text/') ||
                   contentType.includes('json') ||
                   contentType.includes('xml') ||
                   contentType.includes('javascript');

    if (isText) {
      // Text-based: baca sebagai string
      const text = await response.text();
      return res.send(text);
    } else {
      // Binary (image, file, dll): stream langsung      // Next.js pages/api support Node stream
      const buffer = await response.arrayBuffer();
      return res.send(Buffer.from(buffer));
    }

  } catch (err) {
    clearTimeout(timeoutId);

    // ========================================
    // 6. Error handling
    // ========================================
    if (err.name === 'AbortError') {
      return res.status(504).json({
        status: false,
        error: 'Request timeout (10 detik). URL target tidak merespons.'
      });
    }

    return res.status(500).json({
      status: false,
      error: `Gagal fetch URL: ${err.message}`
    });
  }
}
