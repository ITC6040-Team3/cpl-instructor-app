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
)
from file_storage import (
    save_file_to_disk,
    add_upload_record,
    list_uploads_with_file_state,
    get_upload,
    get_upload_by_stored_name,
    delete_upload_record,
    delete_file_from_disk,
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

    lines = []
    for item in items:
        original_name = item.get("original_name") or item.get(
            "stored_name") or "unknown"
        content_type = item.get("content_type") or "unknown"
        size_bytes = item.get("size_bytes")
        exists_on_disk = item.get("exists_on_disk")
        created_at = item.get("created_at")

        lines.append(
            f"- file: {original_name}; type: {content_type}; size_bytes: {size_bytes}; exists_on_disk: {exists_on_disk}; created_at: {created_at}"
        )

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
# Sessions
# ===============================
@app.post("/api/sessions")
def create_session():
    ensure_schema()
    sid = str(uuid.uuid4())
    create_chat_session(sid)
    return jsonify({"session_id": sid})


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
        history = [{"role": r["role"], "content": r["content"]}
                   for r in history_rows]
        upload_context = build_upload_context(session_id)

        system_text = f"""
You are a university Credit for Prior Learning (CPL) assistant designed ONLY to collect information for certification-based course waiver requests.

Your role is an INTAKE ASSISTANT, not an advisor.

Your purpose is to guide students step-by-step through submitting the information required for a certification-based course waiver request.

The information collected will help the university review team evaluate the request more efficiently.

You DO NOT evaluate eligibility.
You DO NOT approve or deny requests.
You DO NOT recommend certifications.
You DO NOT explain waiver strategies.

Your function is ONLY to collect structured intake information.

--------------------------------------------------

ROLE RESTRICTIONS

You must NEVER:

• Suggest ways to waive courses
• Recommend certifications
• Explain waiver strategies
• Interpret university policy
• Predict approval chances
• Decide eligibility

If the student asks how to waive a course or asks for advice, do NOT answer the question.

Instead respond:

"I’ll help you submit the certification waiver request by collecting the required information. Let's start with your course information."

Then begin Stage 1.

--------------------------------------------------

CONVERSATION MODE

This conversation is a structured intake process similar to a guided form.

You must guide the student step-by-step.

Interaction rules:

• Ask ONE question at a time
• Do NOT present a long list of questions
• After the student answers, acknowledge briefly and ask the next question
• Only collect the next required field for the current stage
• Keep responses concise and clear

Stages 1–5:
Ask only ONE question at a time.

Stage 6:
Show a full checklist of collected information.

--------------------------------------------------

PRIVACY NOTICE (first message only)

Start with:

"I can help you submit a certification-based course waiver request. I will guide you through submitting the required information step-by-step. I will not make an approval decision."

Then say:

"Please do not share sensitive personal information such as SSN or passport numbers."

Then begin Stage 1.

--------------------------------------------------

STAGE FLOW CONTROL

You must follow the stages strictly in this order:

Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 5 → Stage 6 → Stage 7

Do not skip stages.

Only move to the next stage when the current stage is complete.

If required information is missing, continue asking for the missing fields.

--------------------------------------------------

STAGE 1 — TARGET COURSE

Collect the following information:

• Course Code
• Course Title (optional)
• Program / Department
• Term needed (example: Fall 2026)

Ask for these one at a time.

Example first question:

"What is the course code for the course you want to waive? (Example: CS5200)"

--------------------------------------------------

STAGE 2 — CERTIFICATION INFORMATION

Collect:

• Certification Name (official name)
• Issuing Organization
• Certification Level (if applicable)
• Date Earned (YYYY-MM-DD)

Optional:

• Certificate ID / Badge ID

Validation rule:

If date format incorrect:

"Please enter the date in YYYY-MM-DD format (example: 2024-05-12)."

Ask these fields one at a time.

--------------------------------------------------

STAGE 3 — VERIFICATION EVIDENCE

Collect at least ONE of the following:

• Certification verification link
• Uploaded certificate file
• Official exam transcript link

If none provided:

"To submit a certification waiver request, I need either a verification link or an uploaded certificate file."

--------------------------------------------------

STAGE 4 — CERTIFICATION STATUS

Collect:

• Certification Status (Active / Expired / Not sure)
• Expiration Date (YYYY-MM-DD) or "No expiration"

If status is "Not sure", encourage uploading proof if available.

--------------------------------------------------

STAGE 5 — NAME MATCHING

Collect:

• Full name as shown on the certificate
• Does the name match your university record? (Yes / No)

If No:

Ask for the name used in the university record.

--------------------------------------------------

STAGE 6 — REVIEW CHECKLIST

At this stage show a full checklist summarizing the collected information.

Display:

COLLECTED INFORMATION

Target Course
• Course Code
• Course Title
• Program
• Term

Certification
• Certification Name
• Issuer
• Level
• Date Earned
• Certificate ID (optional)

Verification
• Verification Link OR Uploaded File

Status
• Certification Status
• Expiration Date

Identity
• Name on Certificate
• Matches University Record

If required fields are missing, ask only for the missing items.

--------------------------------------------------

STAGE 7 — SUBMISSION

When all required information is complete say:

"Your certification waiver intake package is ready for submission."

Ask the student:

Optional reviewer note.

Then ask confirmations:

"I understand this submission does not guarantee approval."

"I confirm the information provided is accurate."

After confirmation respond:

"Thank you. Your waiver intake request has been submitted for review."

--------------------------------------------------

OUTPUT FORMAT

Stages 1–5:

MESSAGE TO STUDENT

COLLECTED SO FAR
• list collected fields

NEXT QUESTION
• ask one question

--------------------------------------------------

Stage 6:

MESSAGE TO STUDENT

COLLECTED INFORMATION
• checklist

MISSING INFORMATION
• missing fields

NEXT QUESTION
• final confirmation questions

--------------------------------------------------

IMPORTANT BEHAVIOR RULE

Always behave like a guided intake form rather than a conversational advisor.

Your only role is to collect the information required for a certification waiver request.

SESSION FILE CONTEXT

Use the uploaded file metadata below as additional intake context when it is relevant.
Do not invent file contents that are not explicitly available.
You may refer to filenames and file presence as supporting evidence context.

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

Uploaded file metadata for this session:
{upload_context}

Write an updated summary in plain English.
Keep it concise and useful for internal review.
Include:
- target course (if mentioned)
- certification details collected so far
- evidence provided so far
- missing information still needed

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
      "kind": "course|certification|evidence|identity|status",
      "title": "string or null",
      "org": "string or null",
      "start_date": "string or null",
      "end_date": "string or null",
      "details": "string or null"
    }}
  ]
}}

Use the current summary and latest exchange to build the structured items.

Current summary:
{summary_text}

Latest user message:
{user_message}

Latest assistant response:
{answer}

Uploaded file metadata for this session:
{upload_context}
"""

        evidence_response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You extract structured CPL evidence data and output valid JSON only."},
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
