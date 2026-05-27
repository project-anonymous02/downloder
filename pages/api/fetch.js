export default async function handler(req, res) {
  // Hanya izinkan GET
  if (req.method !== "GET") {
    return res.status(405).json({
      status: false,
      url: null,
      content: null,
      error: "Method not allowed",
    });
  }

  const targetUrl = req.query.url;

  // Validasi URL
  if (!targetUrl) {
    return res.status(400).json({
      status: false,
      url: null,
      content: null,
      error: "Missing url parameter",
    });
  }

  try {
    // Validasi format URL
    new URL(targetUrl);

    // Timeout 10 detik
    const controller = new AbortController();

    const timeout = setTimeout(() => {
      controller.abort();
    }, 10000);

    const response = await fetch(targetUrl, {
      method: "GET",
      signal: controller.signal,
      headers: {
        "User-Agent": "Mozilla/5.0 Fetch API",
      },
    });

    clearTimeout(timeout);

    const contentType = response.headers.get("content-type") || "";

    let content;

    // Auto parse JSON
    if (contentType.includes("application/json")) {
      content = await response.json();
    } else {
      content = await response.text();
    }

    return res.status(200).json({
      status: true,
      url: targetUrl,
      content,
      error: null,
    });

  } catch (err) {
    return res.status(500).json({
      status: false,
      url: targetUrl,
      content: null,
      error: err.name === "AbortError"
        ? "Request timeout after 10 seconds"
        : err.message,
    });
  }
}
