const SESSION_STORAGE_KEY = "cpl_session_id";
let sessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;

function setStatus(text) {
  const el = document.getElementById("status");
  if (el) el.textContent = text || "";
}

if (sessionId) {
  setStatus(`Session: ${sessionId}`);
}

async function ensureSession() {
  if (sessionId) return sessionId;

  setStatus("Creating session...");
  const res = await fetch("/api/sessions", { method: "POST" });
  const data = await res.json();

  if (!res.ok) {
    throw new Error(data?.error || "Failed to create session");
  }

  sessionId = data.session_id;
  localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  setStatus(`Session: ${sessionId}`);
  return sessionId;
}

document.getElementById("send").addEventListener("click", async () => {
  const msg = document.getElementById("msg").value.trim();
  const out = document.getElementById("out");

  if (!msg) {
    out.textContent = "Please type a message first.";
    return;
  }

  out.textContent = "Thinking...";

  try {
    await ensureSession();

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: msg }),
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (!res.ok) {
      out.textContent = data?.error
        ? `Error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
        : `Error (${res.status}): ${text}`;
      return;
    }

    out.textContent = data?.answer ?? text;
  } catch (e) {
    out.textContent = `Network error: ${e?.message || e}`;
  }
});