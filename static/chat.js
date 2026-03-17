const SESSION_STORAGE_KEY = "cpl_session_id";
let sessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;

function setStatus(text) {
  const el = document.getElementById("status");
  if (el) el.textContent = text || "";
}

function resetSessionView() {
  const msgEl = document.getElementById("msg");
  const outEl = document.getElementById("out");
  const summaryBox = document.getElementById("summaryBox");
  const uploadsList = document.getElementById("uploadsList");
  const evidenceList = document.getElementById("evidenceList");
  const fileEl = document.getElementById("file");

  if (msgEl) msgEl.value = "";
  if (outEl) outEl.textContent = "";
  if (summaryBox) summaryBox.textContent = "No summary yet.";
  if (uploadsList) uploadsList.textContent = "No session.";
  if (fileEl) fileEl.value = "";
  if (evidenceList) {
    evidenceList.innerHTML = `
      <li class="evidenceItem">
        <div class="evidenceDetails">No evidence yet.</div>
      </li>
    `;
  }
}

async function startNewSession() {
  localStorage.removeItem(SESSION_STORAGE_KEY);
  sessionId = null;
  resetSessionView();
  setStatus("");

  try {
    await ensureSession();
  } catch (e) {
    const outEl = document.getElementById("out");
    if (outEl) outEl.textContent = `Network error: ${e?.message || e}`;
  }
}

async function deleteCurrentSession() {
  const outEl = document.getElementById("out");

  if (!sessionId) {
    await startNewSession();
    return;
  }

  try {
    if (outEl) outEl.textContent = "Deleting session...";

    const res = await fetch(`/api/session/${sessionId}`, {
      method: "DELETE",
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (!res.ok) {
      if (outEl) {
        outEl.textContent = data?.error
          ? `Delete session error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
          : `Delete session error (${res.status}): ${text}`;
      }
      return;
    }

    localStorage.removeItem(SESSION_STORAGE_KEY);
    sessionId = null;
    resetSessionView();
    if (outEl) outEl.textContent = "Session deleted.";
    setStatus("");
    await ensureSession();
  } catch (e) {
    if (outEl) outEl.textContent = `Network error: ${e?.message || e}`;
  }
}

if (sessionId) {
  setStatus(`Session: ${sessionId}`);
}

if (sessionId) {
  refreshUploads();
  loadSummary();
  loadEvidence();
}

const newSessionBtn = document.getElementById("newSession");
if (newSessionBtn) {
  newSessionBtn.addEventListener("click", async () => {
    await startNewSession();
  });
}

const deleteSessionBtn = document.getElementById("deleteSession");
if (deleteSessionBtn) {
  deleteSessionBtn.addEventListener("click", async () => {
    await deleteCurrentSession();
  });
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
  loadSummary();
  loadEvidence();
  return sessionId;
}

async function refreshUploads() {
  const listEl = document.getElementById("uploadsList");
  if (!listEl) return;

  if (!sessionId) {
    listEl.textContent = "No session.";
    return;
  }

  try {
    const res = await fetch(`/api/uploads/${sessionId}`);
    const data = await res.json();

    if (!res.ok) {
      listEl.textContent = data?.error
        ? `Error: ${data.error}${data.details ? " - " + data.details : ""}`
        : `Error: ${res.status}`;
      return;
    }

    const items = data.items || [];
    if (items.length === 0) {
      listEl.textContent = "No uploads yet.";
      return;
    }

    listEl.innerHTML = items.map((it) => {
      const name = it.original_name || it.stored_name;
      const dl = it.download_url;
      const id = it.upload_id;
      return `
        <div class="uploadRow">
          <div class="uploadName" title="${name}">${name}</div>
          <a href="${dl}" target="_blank" rel="noopener">Download</a>
          <button class="delUpload" data-upload-id="${id}">Delete</button>
        </div>
      `;
    }).join("");

  } catch (e) {
    listEl.textContent = `Network error: ${e?.message || e}`;
  }
}

async function loadSummary() {
  const box = document.getElementById("summaryBox");
  if (!box) return;

  if (!sessionId) {
    box.textContent = "No summary yet.";
    return;
  }

  try {
    const res = await fetch(`/api/summary/${sessionId}`);
    const data = await res.json();

    if (!res.ok) {
      box.textContent = data?.error
        ? `Error: ${data.error}${data.details ? " - " + data.details : ""}`
        : `Error: ${res.status}`;
      return;
    }

    const summaryText = data?.summary?.summary_text;
    box.textContent = summaryText || "No summary yet.";
  } catch (e) {
    box.textContent = `Network error: ${e?.message || e}`;
  }
}

async function loadEvidence() {
  const list = document.getElementById("evidenceList");
  if (!list) return;

  if (!sessionId) {
    list.innerHTML = `
      <li class="evidenceItem">
        <div class="evidenceDetails">No evidence yet.</div>
      </li>
    `;
    return;
  }

  try {
    const res = await fetch(`/api/evidence/${sessionId}`);
    const data = await res.json();

    if (!res.ok) {
      list.innerHTML = `
        <li class="evidenceItem">
          <div class="evidenceDetails">${data?.error ? `Error: ${data.error}${data.details ? " - " + data.details : ""}` : `Error: ${res.status}`}</div>
        </li>
      `;
      return;
    }

    const items = data.items || [];
    if (items.length === 0) {
      list.innerHTML = `
        <li class="evidenceItem">
          <div class="evidenceDetails">No evidence yet.</div>
        </li>
      `;
      return;
    }

    list.innerHTML = items.map((item) => {
      const kind = item.kind || "unknown";
      const title = item.title || "Untitled";
      const org = item.org || "";
      const dates = [item.start_date, item.end_date].filter(Boolean).join(" → ");
      const details = item.details || "";

      return `
        <li class="evidenceItem">
          <div class="evidenceKind">${kind}</div>
          <div class="evidenceTitle">${title}</div>
          <div class="evidenceMeta">${org || "No organization"}${dates ? ` | ${dates}` : ""}</div>
          <div class="evidenceDetails">${details || "No details."}</div>
        </li>
      `;
    }).join("");
  } catch (e) {
    list.innerHTML = `
      <li class="evidenceItem">
        <div class="evidenceDetails">Network error: ${e?.message || e}</div>
      </li>
    `;
  }
}

document.addEventListener("click", async (ev) => {
  const target = ev.target;
  if (!target || !target.classList || !target.classList.contains("delUpload")) return;

  const uploadId = target.getAttribute("data-upload-id");
  if (!uploadId) return;

  const out = document.getElementById("out");

  try {
    await ensureSession();

    if (out) out.textContent = "Deleting...";

    const res = await fetch(`/api/uploads/${uploadId}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (!res.ok) {
      if (out) {
        out.textContent = data?.error
          ? `Delete error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
          : `Delete error (${res.status}): ${text}`;
      }
      return;
    }

    if (out) out.textContent = "Deleted.";
    await refreshUploads();
    await loadSummary();
    await loadEvidence();

  } catch (e) {
    if (out) out.textContent = `Network error: ${e?.message || e}`;
  }
});

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
    await loadSummary();
    await loadEvidence();
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
      await loadSummary();
      await loadEvidence();

    } catch (e) {
      if (out) out.textContent = `Network error: ${e?.message || e}`;
    }
  });
}