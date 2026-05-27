// /pages/index.js
export default function Home() {
  return (
    <div style={{
      fontFamily: 'system-ui, sans-serif',
      maxWidth: '700px',
      margin: '50px auto',
      padding: '20px',
      color: '#222'
    }}>
      <h1>🌐 Simple API Proxy</h1>
      <p>API proxy ringan buat fetch URL eksternal via server.</p>

      <h3>📌 Endpoint:</h3>
      <code style={{
        background: '#f4f4f4',
        padding: '10px',
        display: 'block',
        borderRadius: '6px',
        wordBreak: 'break-all'
      }}>
        GET /api/fetch?url=TARGET_URL
      </code>

      <h3>🧪 Contoh:</h3>
      <ul>
        <li>
          <a href="/api/fetch?url=https://jsonplaceholder.typicode.com/todos/1">
            /api/fetch?url=https://jsonplaceholder.typicode.com/todos/1
          </a>
        </li>
        <li>
          <a href="/api/fetch?url=https://httpbin.org/html">
            /api/fetch?url=https://httpbin.org/html
          </a>
        </li>
      </ul>

      <h3>⚠️ Error Format:</h3>
      <code style={{
        background: '#fff0f0',
        padding: '10px',
        display: 'block',
        borderRadius: '6px'
      }}>
        {`{ "status": false, "error": "message" }`}
      </code>

      <p style={{ marginTop: '30px', color: '#888' }}>
        Made with 💙 — Deploy-ready di Vercel
      </p>
    </div>
  );
}
