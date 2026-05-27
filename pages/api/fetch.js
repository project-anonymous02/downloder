export const config = {
  api: {
    responseLimit: false,
  },
};

export default async function handler(req, res) {
  if (req.method !== "GET") {
    return res.status(405).json({
      status: false,
      url: null,
      content: null,
      error: "Method not allowed",
    });
  }

  const targetUrl = req.query.url;

  if (!targetUrl) {
    return res.status(400).json({
      status: false,
      url: null,
      content: null,
      error: "Missing url parameter",
    });
  }

  try {
    new URL(targetUrl);

    const controller = new AbortController();

    const timeout = setTimeout(() => {
      controller.abort();
    }, 10000);

    const response = await fetch(targetUrl, {
      method: "GET",
      signal: controller.signal,
      headers: {
        "User-Agent": "Mozilla/5.0",
      },
    });

    clearTimeout(timeout);

    const contentType =
      response.headers.get("content-type") ||
      "application/octet-stream";

    const arrayBuffer = await response.arrayBuffer();

    const buffer = Buffer.from(arrayBuffer);

    // Detect text/json/html
    const isText =
      contentType.includes("text") ||
      contentType.includes("json") ||
      contentType.includes("xml") ||
      contentType.includes("html");

    let content;

    if (isText) {
      content = buffer.toString("utf-8");
    } else {
      // Binary file -> base64
      content = buffer.toString("base64");
    }

    return res.status(200).json({
      status: true,
      url: targetUrl,
      contentType,
      size: buffer.length,
      encoding: isText ? "utf-8" : "base64",
      content,
      error: null,
    });

  } catch (err) {
    return res.status(500).json({
      status: false,
      url: targetUrl || null,
      content: null,
      error:
        err.name === "AbortError"
          ? "Request timeout after 10 seconds"
          : err.message,
    });
  }
}
