const SESSION_STORAGE_KEY = "cpl_session_id";
let sessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;

function setStatus(text) {
  const el = document.getElementById("status");
  if (el) el.textContent = text || "";
}

function getChatMessagesEl() {
  return document.getElementById("chatMessages");
}

function clearChatMessages() {
  const box = getChatMessagesEl();
  if (!box) return;
  box.innerHTML = `
    <div class="emptyState">
      Start a message to begin your certification waiver intake request.
    </div>
  `;
}

function removeEmptyState() {
  const box = getChatMessagesEl();
  if (!box) return;
  const empty = box.querySelector(".emptyState");
  if (empty) empty.remove();
}

function scrollChatToBottom() {
  const box = getChatMessagesEl();
  if (!box) return;
  box.scrollTop = box.scrollHeight;
}

function appendMessage(role, text) {
  const box = getChatMessagesEl();
  if (!box) return;

  removeEmptyState();

  const row = document.createElement("div");
  row.className = `messageRow ${role}`;

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text || "";

  row.appendChild(bubble);
  box.appendChild(row);
  scrollChatToBottom();
}

function appendTemporaryThinking() {
  const box = getChatMessagesEl();
  if (!box) return null;

  removeEmptyState();

  const row = document.createElement("div");
  row.className = "messageRow bot";
  row.dataset.temp = "thinking";

  const bubble = document.createElement("div");
  bubble.className = "bubble bot";
  bubble.textContent = "Thinking...";

  row.appendChild(bubble);
  box.appendChild(row);
  scrollChatToBottom();

  return row;
}

function removeTemporaryThinking() {
  const box = getChatMessagesEl();
  if (!box) return;
  const temp = box.querySelector('[data-temp="thinking"]');
  if (temp) temp.remove();
}

function resetSessionView() {
  const msgEl = document.getElementById("msg");
  const summaryBox = document.getElementById("summaryBox");
  const uploadsList = document.getElementById("uploadsList");
  const evidenceList = document.getElementById("evidenceList");
  const fileEl = document.getElementById("file");

  if (msgEl) msgEl.value = "";
  if (summaryBox) summaryBox.textContent = "No summary yet.";
  if (uploadsList) uploadsList.textContent = "No session.";
  if (fileEl) fileEl.value = "";

  clearChatMessages();

  if (evidenceList) {
    evidenceList.innerHTML = `
      <li class="evidenceItem">
        <div class="evidenceDetails">No evidence yet.</div>
      </li>
    `;
  }
}

function showResumeModal() {
  const modal = document.getElementById("resumeModal");
  if (!modal) return;
  modal.classList.add("show");
  modal.setAttribute("aria-hidden", "false");
}

function hideResumeModal() {
  const modal = document.getElementById("resumeModal");
  if (!modal) return;
  modal.classList.remove("show");
  modal.setAttribute("aria-hidden", "true");
}

async function startNewSession() {
  localStorage.removeItem(SESSION_STORAGE_KEY);
  sessionId = null;
  resetSessionView();
  setStatus("");

  try {
    await ensureSession();
  } catch (e) {
    appendMessage("bot", `Network error: ${e?.message || e}`);
  }
}

async function deleteCurrentSession() {
  if (!sessionId) {
    await startNewSession();
    return;
  }

  try {
    appendMessage("bot", "Deleting session...");

    const res = await fetch(`/api/session/${sessionId}`, {
      method: "DELETE",
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (!res.ok) {
      appendMessage(
        "bot",
        data?.error
          ? `Delete session error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
          : `Delete session error (${res.status}): ${text}`
      );
      return;
    }

    localStorage.removeItem(SESSION_STORAGE_KEY);
    sessionId = null;
    resetSessionView();
    setStatus("");
    await ensureSession();
    appendMessage("bot", "Session deleted. A new session is ready.");
  } catch (e) {
    appendMessage("bot", `Network error: ${e?.message || e}`);
  }
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

async function fetchUploadsData() {
  if (!sessionId) return { items: [] };

  const res = await fetch(`/api/uploads/${sessionId}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error || `Uploads error: ${res.status}`);
  return data;
}

async function fetchSummaryData() {
  if (!sessionId) return { summary: null };

  const res = await fetch(`/api/summary/${sessionId}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error || `Summary error: ${res.status}`);
  return data;
}

async function fetchEvidenceData() {
  if (!sessionId) return { items: [] };

  const res = await fetch(`/api/evidence/${sessionId}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error || `Evidence error: ${res.status}`);
  return data;
}

async function sessionHasMeaningfulContent() {
  if (!sessionId) return false;

  try {
    const [summaryData, evidenceData, uploadsData] = await Promise.all([
      fetchSummaryData(),
      fetchEvidenceData(),
      fetchUploadsData(),
    ]);

    const summaryText = summaryData?.summary?.summary_text?.trim();
    const hasSummary = !!summaryText && summaryText !== "No summary yet.";

    const evidenceItems = evidenceData?.items || [];
    const hasEvidence = evidenceItems.length > 0;

    const uploadItems = uploadsData?.items || [];
    const hasUploads = uploadItems.length > 0;

    return hasSummary || hasEvidence || hasUploads;
  } catch (e) {
    console.warn("Failed to inspect existing session content:", e);
    return false;
  }
}

async function refreshUploads() {
  const listEl = document.getElementById("uploadsList");
  if (!listEl) return;

  if (!sessionId) {
    listEl.textContent = "No session.";
    return;
  }

  try {
    const data = await fetchUploadsData();

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
          <div class="uploadActions">
            <a href="${dl}" target="_blank" rel="noopener">Download</a>
            <button class="delUpload" data-upload-id="${id}">Delete</button>
          </div>
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
    const data = await fetchSummaryData();
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
    const data = await fetchEvidenceData();
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

  try {
    await ensureSession();

    const res = await fetch(`/api/uploads/${uploadId}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (!res.ok) {
      appendMessage(
        "bot",
        data?.error
          ? `Delete error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
          : `Delete error (${res.status}): ${text}`
      );
      return;
    }

    appendMessage("bot", "File deleted.");
    await refreshUploads();
    await loadSummary();
    await loadEvidence();
  } catch (e) {
    appendMessage("bot", `Network error: ${e?.message || e}`);
  }
});

async function sendMessage() {
  const msgEl = document.getElementById("msg");
  const msg = msgEl?.value.trim() || "";

  if (!msg) {
    appendMessage("bot", "Please type a message first.");
    return;
  }

  appendMessage("user", msg);
  if (msgEl) msgEl.value = "";

  try {
    await ensureSession();
    appendTemporaryThinking();

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: msg }),
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    removeTemporaryThinking();

    if (!res.ok) {
      appendMessage(
        "bot",
        data?.error
          ? `Error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
          : `Error (${res.status}): ${text}`
      );
      return;
    }

    appendMessage("bot", data?.answer ?? text);
    await loadSummary();
    await loadEvidence();
  } catch (e) {
    removeTemporaryThinking();
    appendMessage("bot", `Network error: ${e?.message || e}`);
  }
}

const sendBtn = document.getElementById("send");
if (sendBtn) {
  sendBtn.addEventListener("click", sendMessage);
}

const msgTextarea = document.getElementById("msg");
if (msgTextarea) {
  msgTextarea.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      await sendMessage();
    }
  });
}

const uploadBtn = document.getElementById("upload");
if (uploadBtn) {
  uploadBtn.addEventListener("click", async () => {
    const fileEl = document.getElementById("file");

    try {
      await ensureSession();

      const file = fileEl && fileEl.files && fileEl.files[0];
      if (!file) {
        appendMessage("bot", "Please select a file first.");
        return;
      }

      appendMessage("bot", "Uploading file...");

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
        appendMessage(
          "bot",
          data?.error
            ? `Upload error (${res.status}): ${data.error}${data.details ? " - " + data.details : ""}`
            : `Upload error (${res.status}): ${text}`
        );
        return;
      }

      appendMessage("bot", `Uploaded: ${data.original_name || file.name}`);
      if (fileEl) fileEl.value = "";

      await refreshUploads();
      await loadSummary();
      await loadEvidence();
    } catch (e) {
      appendMessage("bot", `Network error: ${e?.message || e}`);
    }
  });
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

document.addEventListener("DOMContentLoaded", async () => {
  const resumeContinueBtn = document.getElementById("resumeContinue");
  const resumeNewSessionBtn = document.getElementById("resumeNewSession");

  if (resumeContinueBtn) {
    resumeContinueBtn.addEventListener("click", async () => {
      hideResumeModal();
      setStatus(`Session: ${sessionId}`);
      await refreshUploads();
      await loadSummary();
      await loadEvidence();
    });
  }

  if (resumeNewSessionBtn) {
    resumeNewSessionBtn.addEventListener("click", async () => {
      hideResumeModal();
      await startNewSession();
    });
  }

  if (sessionId) {
    const shouldResume = await sessionHasMeaningfulContent();
    if (shouldResume) {
      showResumeModal();
    } else {
      setStatus(`Session: ${sessionId}`);
      await refreshUploads();
      await loadSummary();
      await loadEvidence();
    }
  } else {
    await ensureSession();
  }
});