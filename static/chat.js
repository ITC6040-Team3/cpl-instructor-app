const SESSION_STORAGE_KEY = "cpl_session_id";
let sessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;

function setStatus(text) {
  const el = document.getElementById("status");
  if (el) el.textContent = text || "";
}

if (sessionId) {
  setStatus(`Session: ${sessionId}`);
}

if (sessionId) {
  refreshUploads();
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
  refreshUploads();
  return sessionId;
}

async function refreshUploads() {
  const listEl = document.getElementById("uploadsList");
  if (!listEl) return;

  try {
    const res = await fetch(`/api/uploads/${sessionId}`);
    const data = await res.json();
    if (!res.ok) {
      listEl.textContent = data?.error
        ? `Error: ${data.error}${data.details ? " - " + data.details : ""}`
        : `Error: ${res.status}`;
      return;
    }

    listEl.textContent = JSON.stringify(data.items || [], null, 2);
  } catch (e) {
    listEl.textContent = `Network error: ${e?.message || e}`;
  }
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

const uploadBtn = document.getElementById("upload");
if (uploadBtn) {
  uploadBtn.addEventListener("click", async () => {
    const out = document.getElementById("out");
    const fileEl = document.getElementById("file");

    try {
      await ensureSession();

      const file = fileEl && fileEl.files && fileEl.files[0];
      if (!file) {
        if (out) out.textContent = "Please select a file first.";
        return;
      }

      if (out) out.textContent = "Uploading...";

      const fd = new FormData();
      fd.append("session_id", sessionId);
      fd.append("file", file);

      const res = await fetch("/api/upload", {
        method: "POST",
        body: fd,
      });

      const text = await res.text();
      let data = null;
      try { data = JSON.parse(text); } catch (_) {}

      if (!res.ok) {
        if (out) {
          out.textContent = data?.error
            ? `Upload error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
            : `Upload error (${res.status}): ${text}`;
        }
        return;
      }

      if (out) {
        out.textContent = `Uploaded: ${data.original_name || file.name} (${data.size_bytes ?? file.size} bytes)`;
      }

      if (fileEl) fileEl.value = "";
      await refreshUploads();

    } catch (e) {
      if (out) out.textContent = `Network error: ${e?.message || e}`;
    }
  });
}