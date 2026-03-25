import os
import sys
import uuid
import json
from flask import Flask, render_template, request, jsonify, send_from_directory
from openai import AzureOpenAI

from db_utils import get_sql_connection_string, health_check, run_sql_file, fetch_all, execute_non_query
from chat_storage import (
    create_chat_session,
    ensure_chat_session,
    add_chat_message,
    get_chat_messages,
    save_summary,
    get_summary,
    delete_chat_session,
)
from file_storage import (
    save_file_to_disk,
    add_upload_record,
    list_uploads_with_file_state,
    get_upload,
    get_upload_by_stored_name,
    delete_upload_record,
    delete_file_from_disk,
    get_upload_text_preview,
)

# Explicit template folder for Azure App Service reliability
app = Flask(__name__, template_folder="templates")

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

SCHEMA_SQL_PATH = os.path.join(BASE_DIR, "sql", "create_tables.sql")


REQUIRED_ENV_VARS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
]


def require_env_or_exit():
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        msg = (
            "FATAL: Missing required environment variables:\n"
            + "\n".join(f"- {k}" for k in missing)
            + "\n\nThis app is in SQL REQUIRED mode. Set these in Azure Web App -> Environment variables."
        )
        app.logger.error(msg)
        raise RuntimeError(msg)

    if not get_sql_connection_string():
        msg = (
            "FATAL: Missing SQL connection string.\n"
            "Set either:\n"
            "  - App setting: SQL_CONNECTION_STRING\n"
            "or\n"
            "  - Connection strings blade name: SQL_CONNECTION_STRING\n"
            "    (Azure will expose it as SQLCONNSTR_SQL_CONNECTION_STRING)\n"
        )
        app.logger.error(msg)
        raise RuntimeError(msg)


# Fail fast on startup if configuration is incomplete
require_env_or_exit()


# ===============================
# Azure OpenAI Client Factory
# ===============================
def get_client():
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    if not endpoint:
        return None, "Missing AZURE_OPENAI_ENDPOINT"
    if not api_key:
        return None, "Missing AZURE_OPENAI_API_KEY"

    try:
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        return client, None
    except Exception as e:
        return None, f"Client initialization failed: {type(e).__name__}: {str(e)}"


_schema_ready = False


def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return True

    try:
        if not os.path.isfile(SCHEMA_SQL_PATH):
            app.logger.error(f"Schema file not found: {SCHEMA_SQL_PATH}")
            return False

        run_sql_file(SCHEMA_SQL_PATH)
        _schema_ready = True
        return True
    except Exception:
        app.logger.exception("Schema initialization failed")
        return False


# ===============================
# Evidence Items Helpers
# ===============================

def clear_evidence_items(session_id: str):
    execute_non_query(
        "DELETE FROM dbo.evidence_items WHERE session_id = ?",
        (session_id,),
    )


def save_evidence_items(session_id: str, items):
    clear_evidence_items(session_id)

    for item in items:
        kind = (item.get("kind") or "certification").strip()
        title = item.get("title")
        org = item.get("org")
        start_date = item.get("start_date")
        end_date = item.get("end_date")
        details = item.get("details")

        execute_non_query(
            """
            INSERT INTO dbo.evidence_items(session_id, kind, title, org, start_date, end_date, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, kind, title, org, start_date, end_date, details),
        )


def parse_json_payload(text: str):
    text = (text or "").strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    return json.loads(text)


# ===============================
# Upload Session File Context Helper
# ===============================
def build_upload_context(session_id: str):
    items = list_uploads_with_file_state(session_id, UPLOAD_DIR)
    if not items:
        return "No uploaded files for this session."

    max_files = 2
    max_chars_per_file = 600
    total_char_budget = 1200
    used_chars = 0

    lines = []
    for item in items[:max_files]:
        original_name = item.get("original_name") or item.get(
            "stored_name") or "unknown"
        stored_name = item.get("stored_name") or ""
        content_type = item.get("content_type") or "unknown"
        size_bytes = item.get("size_bytes")
        exists_on_disk = item.get("exists_on_disk")
        created_at = item.get("created_at")

        preview = ""
        if exists_on_disk and stored_name:
            preview = get_upload_text_preview(
                UPLOAD_DIR,
                stored_name,
                max_chars=max_chars_per_file,
            )

        if preview:
            remaining = max(total_char_budget - used_chars, 0)
            if remaining > 0:
                preview = preview[:remaining]
                used_chars += len(preview)
            else:
                preview = ""

        lines.append(
            f"- file: {original_name}; type: {content_type}; size_bytes: {size_bytes}; exists_on_disk: {exists_on_disk}; created_at: {created_at}"
        )

        if preview:
            lines.append(f"  preview: {preview}")

    return "Uploaded files for this session:\n" + "\n".join(lines)


# ===============================
# Static File Route (bulletproof)
# ===============================
@app.get("/static/<path:filename>")
def static_files(filename):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    return send_from_directory(static_dir, filename)


# ===============================
# Basic Pages
# ===============================
@app.get("/")
def home():
    return render_template("index.html")


@app.get("/chat")
def chat_page():
    return render_template("chat.html")


@app.get("/admin")
def admin_page():
    sql_present = True if get_sql_connection_string() else False

    status = {
        "AZURE_OPENAI_ENDPOINT": "✅ set" if os.getenv("AZURE_OPENAI_ENDPOINT") else "❌ missing",
        "AZURE_OPENAI_API_KEY": "✅ set" if os.getenv("AZURE_OPENAI_API_KEY") else "❌ missing",
        "AZURE_OPENAI_API_VERSION": os.getenv("AZURE_OPENAI_API_VERSION") or "(default: 2024-12-01-preview)",
        "AZURE_OPENAI_DEPLOYMENT": "✅ set" if os.getenv("AZURE_OPENAI_DEPLOYMENT") else "❌ missing",
        "SQL_CONNECTION_STRING": "✅ set" if sql_present else "❌ missing (REQUIRED)",
    }
    return render_template("admin.html", status=status)


# ===============================
# Health + Debug
# ===============================
@app.get("/health")
def health():
    try:
        ensure_schema()
        result = health_check()
        if not result.get("ok"):
            return jsonify({"status": "error", "details": "DB ping returned unexpected result"}), 500
        return jsonify({"status": "ok"})
    except Exception as e:
        app.logger.exception("Health check failed (DB not ready)")
        return jsonify(
            {
                "status": "error",
                "error": f"{type(e).__name__}",
                "details": str(e),
            }
        ), 500


@app.get("/versions")
def versions():
    try:
        import openai
        import httpx

        return jsonify(
            {
                "openai_version": getattr(openai, "__version__", "unknown"),
                "httpx_version": getattr(httpx, "__version__", "unknown"),
                "python_version": sys.version,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/dbcheck")
def dbcheck():
    try:
        result = health_check()
        return jsonify({"status": "DB Connected", "result": result})
    except Exception as e:
        app.logger.exception("DB connection check failed")
        return jsonify(
            {
                "error": f"DB check failed: {type(e).__name__}",
                "details": str(e),
            }
        ), 500


@app.get("/setup-db")
def setup_db():
    try:
        if not os.path.isfile(SCHEMA_SQL_PATH):
            return jsonify({
                "status": "error",
                "message": f"Schema file not found: {SCHEMA_SQL_PATH}"
            }), 500

        run_sql_file(SCHEMA_SQL_PATH)
        return jsonify({
            "status": "success",
            "message": "Database tables created successfully."
        })
    except Exception as e:
        app.logger.exception("Setup DB failed")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.get("/api/dbinfo")
def dbinfo():
    try:
        ensure_schema()

        tables_rows = fetch_all(
            """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'dbo'
            """
        )
        tables = sorted([r["TABLE_NAME"] for r in tables_rows])

        counts = {}
        for t in ["sessions", "messages", "summaries", "evidence_items", "uploads"]:
            if t in tables:
                result = fetch_all(
                    f"SELECT COUNT(1) AS row_count FROM dbo.{t}")
                counts[t] = int(result[0]["row_count"]) if result else 0
            else:
                counts[t] = None

        last_message = None
        if "messages" in tables:
            rows = fetch_all(
                """
                SELECT TOP 1 session_id, role, created_at
                FROM dbo.messages
                ORDER BY message_id DESC
                """
            )
            if rows:
                last_message = {
                    "session_id": str(rows[0]["session_id"]),
                    "role": rows[0]["role"],
                    "created_at": str(rows[0]["created_at"]),
                }

        return jsonify({
            "status": "ok",
            "tables": tables,
            "row_counts": counts,
            "last_message": last_message,
        })

    except Exception as e:
        app.logger.exception("dbinfo failed")
        return jsonify({
            "status": "error",
            "error": f"{type(e).__name__}",
            "details": str(e)
        }), 500


# ===============================
# AI-Generated Session Summary API
# ===============================

@app.get("/api/summary/<session_id>")
def api_get_summary(session_id):
    try:
        ensure_schema()
        summary = get_summary(session_id)
        return jsonify({
            "status": "ok",
            "summary": summary,
        })
    except Exception as e:
        app.logger.exception("Get summary failed")
        return jsonify({
            "error": f"Get summary failed: {type(e).__name__}",
            "details": str(e)
        }), 500


# ===============================
# AI-Extracted Evidence Items API
# ===============================

@app.get("/api/evidence/<session_id>")
def api_get_evidence(session_id):
    try:
        ensure_schema()
        rows = fetch_all(
            """
            SELECT evidence_id, session_id, kind, title, org, start_date, end_date, details, created_at
            FROM dbo.evidence_items
            WHERE session_id = ?
            ORDER BY evidence_id ASC
            """,
            (session_id,),
        )

        for row in rows:
            if row.get("evidence_id") is not None:
                row["evidence_id"] = int(row["evidence_id"])
            if row.get("created_at") is not None:
                row["created_at"] = str(row["created_at"])
            if row.get("session_id") is not None:
                row["session_id"] = str(row["session_id"])

        return jsonify({
            "status": "ok",
            "items": rows,
        })
    except Exception as e:
        app.logger.exception("Get evidence failed")
        return jsonify({
            "error": f"Get evidence failed: {type(e).__name__}",
            "details": str(e)
        }), 500


# ===============================
# Chat History API
# ===============================

@app.get("/api/messages/<session_id>")
def api_get_messages(session_id):
    try:
        ensure_schema()
        rows = get_chat_messages(session_id, limit=100)

        items = []
        for row in rows:
            items.append({
                "role": row.get("role"),
                "content": row.get("content"),
                "created_at": str(row.get("created_at")) if row.get("created_at") else None,
            })

        return jsonify({
            "status": "ok",
            "items": items,
        })
    except Exception as e:
        app.logger.exception("Get messages failed")
        return jsonify({
            "error": f"Get messages failed: {type(e).__name__}",
            "details": str(e)
        }), 500

# ===============================
# Sessions
# ===============================


@app.post("/api/sessions")
def create_session():
    ensure_schema()
    sid = str(uuid.uuid4())
    create_chat_session(sid)
    return jsonify({"session_id": sid})


# ===============================
# Delete an entire session and all associated uploaded files
# ===============================
@app.delete("/api/session/<session_id>")
def api_delete_session(session_id):
    try:
        ensure_schema()

        session_row = fetch_all(
            """
            SELECT session_id
            FROM dbo.sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if not session_row:
            return jsonify({"error": "Session not found"}), 404

        upload_items = list_uploads_with_file_state(session_id, UPLOAD_DIR)
        deleted_files = []
        missing_files = []

        for item in upload_items:
            stored_name = item.get("stored_name")
            if not stored_name:
                continue

            removed = delete_file_from_disk(UPLOAD_DIR, stored_name)
            if removed:
                deleted_files.append(stored_name)
            else:
                missing_files.append(stored_name)

        delete_chat_session(session_id)

        return jsonify({
            "status": "ok",
            "session_id": session_id,
            "deleted_files": deleted_files,
            "missing_files": missing_files,
        })
    except Exception as e:
        app.logger.exception("Delete session failed")
        return jsonify({
            "error": f"Delete session failed: {type(e).__name__}",
            "details": str(e)
        }), 500


# ===============================
# File Uploads, List, Download, Delete
# ===============================
@app.post("/api/upload")
def api_upload():
    try:
        ensure_schema()

        session_id = (request.form.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        ensure_chat_session(session_id)

        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400

        f = request.files["file"]
        if not f or not f.filename:
            return jsonify({"error": "file is required"}), 400

        file_info = save_file_to_disk(f, UPLOAD_DIR, session_id)
        add_upload_record(
            session_id=session_id,
            stored_name=file_info["stored_name"],
            original_name=file_info["original_name"],
            content_type=file_info["content_type"],
            size_bytes=file_info["size_bytes"],
        )

        return jsonify({
            "status": "ok",
            "stored_name": file_info["stored_name"],
            "original_name": file_info["original_name"],
            "size_bytes": file_info["size_bytes"],
            "content_type": file_info["content_type"],
        })

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        app.logger.exception("Upload failed")
        return jsonify({
            "error": f"Upload failed: {type(e).__name__}",
            "details": str(e)
        }), 500


@app.get("/api/uploads/<session_id>")
def api_list_uploads(session_id):
    try:
        ensure_schema()

        items = list_uploads_with_file_state(session_id, UPLOAD_DIR)
        for item in items:
            item["download_url"] = f"/api/download/{item['stored_name']}"
            if "created_at" in item:
                item["created_at"] = str(item["created_at"])
            if item.get("size_bytes") is not None:
                item["size_bytes"] = int(item["size_bytes"])
            if item.get("upload_id") is not None:
                item["upload_id"] = int(item["upload_id"])

        return jsonify({"status": "ok", "items": items})

    except Exception as e:
        app.logger.exception("List uploads failed")
        return jsonify({
            "error": f"List uploads failed: {type(e).__name__}",
            "details": str(e)
        }), 500


@app.get("/api/download/<path:stored_name>")
def api_download(stored_name):
    try:
        upload = get_upload_by_stored_name(stored_name)

        file_path = os.path.join(UPLOAD_DIR, stored_name)
        if not upload or not os.path.isfile(file_path):
            return jsonify({"error": "File not found"}), 404

        return send_from_directory(UPLOAD_DIR, stored_name, as_attachment=True)

    except Exception as e:
        app.logger.exception("Download failed")
        return jsonify({
            "error": f"Download failed: {type(e).__name__}",
            "details": str(e)
        }), 500


@app.delete("/api/uploads/<int:upload_id>")
def api_delete_upload(upload_id):
    try:
        ensure_schema()

        data = request.get_json(silent=True) or {}
        session_id = (data.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        upload = get_upload(upload_id, session_id)
        if not upload:
            return jsonify({"error": "Upload not found for this session"}), 404

        stored_name = upload["stored_name"]
        delete_upload_record(upload_id, session_id)
        file_deleted = delete_file_from_disk(UPLOAD_DIR, stored_name)

        return jsonify({
            "status": "ok",
            "upload_id": upload_id,
            "stored_name": stored_name,
            "file_deleted": file_deleted
        })

    except Exception as e:
        app.logger.exception("Delete upload failed")
        return jsonify({
            "error": f"Delete upload failed: {type(e).__name__}",
            "details": str(e)
        }), 500


# ===============================
# Chat API Endpoint
# ===============================
@app.post("/api/chat")
def api_chat():
    try:
        ensure_schema()

        data = request.get_json(silent=True) or {}
        session_id = (data.get("session_id") or "").strip()
        user_message = (data.get("message") or "").strip()

        if not session_id:
            return jsonify({"error": "session_id is required"}), 400
        if not user_message:
            return jsonify({"error": "Message is required"}), 400

        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not deployment:
            return jsonify({"error": "Missing AZURE_OPENAI_DEPLOYMENT"}), 500

        client, err = get_client()
        if err:
            return jsonify({"error": err}), 500

        ensure_chat_session(session_id)
        add_chat_message(session_id, "user", user_message)
        
        history_rows = get_chat_messages(session_id, limit=20)
        history = [{"role": r["role"], "content": r["content"]} for r in history_rows]
        
        upload_context = build_upload_context(session_id)
        
        existing_summary_row = get_summary(session_id)
        existing_summary = ""
        if existing_summary_row and existing_summary_row.get("summary_text"):
            existing_summary = existing_summary_row["summary_text"]

        system_text = f"""
You are a university Credit for Prior Learning (CPL) intake assistant.

Your role is ONLY to collect information for a course waiver request supported by either certification evidence or work experience evidence.
You are not an academic advisor, reviewer, or decision maker.

You must NOT:
- evaluate eligibility
- approve or deny requests
- recommend certifications
- explain waiver strategies
- interpret university policy
- predict approval chances

If the student asks for waiver advice, policy interpretation, certification recommendations, or approval likelihood, respond:
"I can help you submit the waiver request by collecting the required information step-by-step, but I cannot provide waiver advice, policy interpretation, certification recommendations, or approval guidance."

### CURRENT SESSION STATE (GROUND TRUTH)
Use the following summary as the main source of truth for what has already been collected, what is still missing, which intake stage is currently active, and whether the student is following the certification path or the work experience path:
{existing_summary}

### CONVERSATION RULES
- Ask only ONE question at a time.
- Keep responses short, clear, and professional.
- After each student answer, briefly acknowledge it and ask the next needed question.
- Do not ask for multiple missing fields in one message unless you are in the review stage.
- Behave like a guided intake form, not a free-form chatbot.
- Do not skip stages.
- After the waiver support type is identified, follow only the relevant path.

### FIRST MESSAGE ONLY
If no prior intake information has been collected yet, say exactly this:
"I can help you submit a course waiver request. I will guide you through the required information step-by-step. Please do not share sensitive personal information such as SSN, passport numbers, or bank information. You may upload .txt, .pdf, or .docx files. Please note that .txt files may provide a short readable preview, while .pdf and .docx files are treated as metadata only. For best content-reading support, please upload a .txt file when possible."
Then immediately ask the first question of Stage 1A.

### INTAKE STAGES

Stage 1A: Target Course
Collect one at a time:
- Course Code
- Course Title (optional)
- Program / Department
- Term needed

First question example:
"What is the course code for the course you want to waive? (Example: CS5200)"

Stage 1B: Waiver Support Type
After Stage 1A is complete, ask:
"Would you like to support this waiver request with a certification or with work experience?"

If the student answers certification, follow the Certification Path.
If the student answers work experience, follow the Work Experience Path.
If unclear, ask for clarification before moving on.

----------------------------
CERTIFICATION PATH
----------------------------

Stage 2C: Certification Information
Collect one at a time:
- Certification Name
- Issuing Organization
- Certification Level (if applicable)
- Date Earned (YYYY-MM-DD)

Optional:
- Certificate ID / Badge ID

If the date is invalid, ask the student to provide it in YYYY-MM-DD format.

Stage 3C: Verification Evidence
Collect at least one:
- Certification verification link
- Uploaded certificate file
- Official exam transcript link

*CRITICAL FILE RULE*:
.txt files may provide a short preview that can be used as supporting context.
.pdf and .docx files provide metadata only, not full readable content.
If a user uploads a file, acknowledge receipt, but do not invent file contents.
You must still ask for any required fields that have not been explicitly provided by the student.

If upload context shows that at least one file has been uploaded, or the student provides a certification verification link or official exam transcript link, consider Stage 3C complete and move to Stage 4C.

Stage 4C: Certification Status
Collect:
- Certification Status (Active / Expired / Not sure)
- Expiration Date (YYYY-MM-DD) or "No expiration"

If status is "Not sure", encourage the student to upload proof if available.

Stage 5C: Name Matching
Collect:
- Full name as shown on the certificate
- Whether the name matches the university record (Yes / No)

If No, ask for the name used in the university record.

----------------------------
WORK EXPERIENCE PATH
----------------------------

Stage 2W: Work Experience Information
Collect one at a time:
- Job Title
- Employer / Organization
- Employment Start Date (YYYY-MM-DD if known)
- Employment End Date (YYYY-MM-DD), or "Current"
- Brief description of relevant responsibilities

If exact dates are unknown, accept the most accurate approximate date the student can provide.

Stage 3W: Supporting Evidence
Collect at least one:
- Resume
- Employer letter
- Offer letter
- Pay stub
- Work portfolio
- Supervisor or HR contact information
- Uploaded supporting file

*CRITICAL FILE RULE*:
.txt files may provide a short preview that can be used as supporting context.
.pdf and .docx files provide metadata only, not full readable content.
If a user uploads a file, acknowledge receipt, but do not invent file contents.
You must still ask for any required fields that have not been explicitly provided by the student.

If upload context shows that at least one file has been uploaded, or the student provides employer contact information or other supporting documentation details, consider Stage 3W complete and move to Stage 4W.

Stage 4W: Relevance to Course
Collect:
- Which course topics, skills, or learning outcomes were covered through this work experience
- Approximate length of relevant experience
- Whether the experience was full-time, part-time, internship, contract, or other

Stage 5W: Name Matching
Collect:
- Full name shown on the supporting documents
- Whether the name matches the university record (Yes / No)

If No, ask for the name used in the university record.

----------------------------
FINAL COMMON STAGES
----------------------------

Stage 6: Review Checklist
Show a clear checklist of all collected information based on the selected path.
If anything required is missing, ask only for the missing items.

If the student corrects any previously collected field during Stage 6, treat the new value as the latest ground truth, update the checklist, and continue the review stage until all required information is complete.

Stage 7: Submission
When all required information is complete, say:
"Your waiver intake package is ready for submission."

Then ask for:
- optional reviewer note
- confirmation that submission does not guarantee approval
- confirmation that the provided information is accurate

After confirmation, say:
"Thank you. Your waiver intake request has been submitted for review."

### OUTPUT FORMAT (Stages before Review)
MESSAGE TO STUDENT
[Short acknowledgement]

COLLECTED SO FAR
[Only include fields already collected, if any]

NEXT QUESTION
[Ask exactly one question]

### OUTPUT FORMAT (Stage 6)
MESSAGE TO STUDENT
[Short review message]

COLLECTED INFORMATION
[Checklist of all collected fields based on the selected path]

MISSING INFORMATION
[Only required missing fields]

NEXT QUESTION
[Ask exactly one question]

### UPLOAD CONTEXT
Use this to verify whether the student has uploaded any files during this session:

{upload_context}
"""
        messages = [{"role": "system", "content": system_text}] + history

        response = client.chat.completions.create(
            model=deployment,
            messages=messages,
            temperature=0.3,
        )

        answer = (response.choices[0].message.content or "").strip()
        add_chat_message(session_id, "assistant", answer)

        existing_summary_row = get_summary(session_id)
        existing_summary = ""
        if existing_summary_row and existing_summary_row.get("summary_text"):
            existing_summary = existing_summary_row["summary_text"]

        summary_prompt = f"""
You are updating a concise structured summary for a university CPL intake session.

Current summary:
{existing_summary}

New user message:
{user_message}

New assistant response:
{answer}

Uploaded file context for this session:
{upload_context}

Write an updated summary in plain English.
Keep it concise and useful for internal review.

Include:
- waiver support type (certification or work experience, if known)
- target course information collected so far
- certification details collected so far, if the student is following the certification path
- work experience details collected so far, if the student is following the work experience path
- evidence provided so far
- missing information still needed
- current intake stage, if it can be reasonably inferred

Do not include extra commentary.
"""

        summary_response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You generate concise internal summaries for CPL intake sessions."},
                {"role": "user", "content": summary_prompt},
            ],
            temperature=0.2,
        )

        summary_text = (
            summary_response.choices[0].message.content or "").strip()
        save_summary(session_id, summary_text)

        evidence_prompt = f"""
You extract structured evidence items for a university CPL intake session.

Return STRICT JSON only.
Do not use markdown.
Do not add explanation.

Schema:
{{
  "items": [
    {{
      "kind": "course|certification|work_experience|evidence|identity|status",
      "title": "string or null",
      "org": "string or null",
      "start_date": "string or null",
      "end_date": "string or null",
      "details": "string or null"
    }}
  ]
}}

Use the current summary and latest exchange to build the structured items.

Guidance:
- Use kind="course" for course code, course title, program/department, and term information when appropriate.
- Use kind="certification" for certification name, certification level, badge ID, certificate ID, and related certification information.
- Use kind="work_experience" for job title, employer, employment period, relevant responsibilities, experience type, and other work-related information.
- Use kind="evidence" for verification links, uploaded files, employer letters, resumes, portfolios, transcripts, or other supporting proof.
- Use kind="identity" for name matching information.
- Use kind="status" for certification status, expiration details, or other status-related information.

Rules:
- Do not invent file contents.
- Uploaded files may count as supporting evidence even when only metadata is available.
- If the student is on the certification path, prefer certification-related structured items.
- If the student is on the work experience path, prefer work_experience-related structured items.

Current summary:
{summary_text}

Latest user message:
{user_message}

Latest assistant response:
{answer}

Uploaded file context for this session:
{upload_context}
"""

        evidence_response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You extract structured CPL intake data for certification or work experience waiver requests and output valid JSON only."},
                {"role": "user", "content": evidence_prompt},
            ],
            temperature=0.1,
        )

        evidence_raw = (
            evidence_response.choices[0].message.content or "").strip()
        evidence_data = parse_json_payload(evidence_raw)
        evidence_items = evidence_data.get("items", [])
        if not isinstance(evidence_items, list):
            evidence_items = []

        save_evidence_items(session_id, evidence_items)

        return jsonify({
            "answer": answer,
            "summary": summary_text,
            "evidence_items": evidence_items,
        })

    except Exception as e:
        app.logger.exception("Chat failed")
        return jsonify(
            {
                "error": f"Chat failed: {type(e).__name__}",
                "details": str(e),
            }
        ), 500


# ===============================
# Local Dev Entry Point
# ===============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
