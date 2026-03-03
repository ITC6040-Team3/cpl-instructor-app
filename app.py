import os
import sys
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import time
from openai import AzureOpenAI
import pyodbc

# Explicit template folder for Azure App Service reliability
app = Flask(__name__, template_folder="templates")

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "gif",
    "doc", "docx", "txt"
}


def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


# ===============================
# ✅ SQL REQUIRED MODE (per-team DB)
# Supports BOTH:
#   - App setting: SQL_CONNECTION_STRING
#   - Azure "Connection strings" blade:
#       SQLCONNSTR_SQL_CONNECTION_STRING (or SQLAZURECONNSTR_SQL_CONNECTION_STRING)
# ===============================
def get_sql_connection_string():
    # App settings
    direct = os.getenv("SQL_CONNECTION_STRING")
    if direct:
        return direct

    # If set under App Service -> Connection strings with name "SQL_CONNECTION_STRING"
    # App Service commonly exposes: SQLCONNSTR_<name>
    prefixed = os.getenv("SQLCONNSTR_SQL_CONNECTION_STRING")
    if prefixed:
        return prefixed

    # Some environments may use SQLAZURECONNSTR_
    prefixed2 = os.getenv("SQLAZURECONNSTR_SQL_CONNECTION_STRING")
    if prefixed2:
        return prefixed2

    return None


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


# ===============================
# DB helpers
# ===============================
def get_db_connection():
    conn_str = get_sql_connection_string()
    # timeout is seconds; keep short for health checks
    return pyodbc.connect(conn_str, timeout=10)


def db_ping():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        try:
            conn.close()
        except Exception:
            pass


MIGRATION_SQL = """
IF OBJECT_ID('dbo.sessions', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.sessions (
    session_id UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    user_label NVARCHAR(200) NULL,
    PRIMARY KEY (session_id)
  );
END;

IF OBJECT_ID('dbo.messages', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.messages (
    message_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    session_id UNIQUEIDENTIFIER NOT NULL,
    role NVARCHAR(50) NOT NULL,
    content NVARCHAR(MAX) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_messages_sessions')
  BEGIN
    ALTER TABLE dbo.messages
    ADD CONSTRAINT fk_messages_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;

IF OBJECT_ID('dbo.summaries', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.summaries (
    session_id UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
    summary_text NVARCHAR(MAX) NOT NULL,
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_summaries_sessions')
  BEGIN
    ALTER TABLE dbo.summaries
    ADD CONSTRAINT fk_summaries_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;

IF OBJECT_ID('dbo.evidence_items', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.evidence_items (
    evidence_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    session_id UNIQUEIDENTIFIER NOT NULL,
    kind NVARCHAR(50) NOT NULL,
    title NVARCHAR(300) NULL,
    org NVARCHAR(300) NULL,
    start_date NVARCHAR(40) NULL,
    end_date NVARCHAR(40) NULL,
    details NVARCHAR(MAX) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_evidence_sessions')
  BEGIN
    ALTER TABLE dbo.evidence_items
    ADD CONSTRAINT fk_evidence_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;

IF OBJECT_ID('dbo.uploads', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.uploads (
    upload_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    session_id UNIQUEIDENTIFIER NOT NULL,
    stored_name NVARCHAR(500) NOT NULL,
    original_name NVARCHAR(500) NOT NULL,
    content_type NVARCHAR(120) NULL,
    size_bytes BIGINT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_uploads_sessions')
  BEGIN
    ALTER TABLE dbo.uploads
    ADD CONSTRAINT fk_uploads_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;
"""

_schema_ready = False


def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return True

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(MIGRATION_SQL)
            conn.commit()
            _schema_ready = True
            return True
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        app.logger.exception("Schema initialization failed")
        return False


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
        # SQL required mode
        "SQL_CONNECTION_STRING": "✅ set" if sql_present else "❌ missing (REQUIRED)",
    }
    return render_template("admin.html", status=status)


# ===============================
# ✅ HEALTH = APP + DB READINESS
# ===============================
@app.get("/health")
def health():
    try:
        ensure_schema()
        result = db_ping()
        if result != 1:
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


# ===============================
# 🔍 DEBUG SUPERPOWER ROUTE
# Shows SDK versions for troubleshooting
# ===============================
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


# ===============================
# ✅ DB CHECK ROUTE (still useful for debugging)
# ===============================
@app.get("/dbcheck")
def dbcheck():
    try:
        result = db_ping()
        return jsonify({"status": "DB Connected", "result": result})
    except Exception as e:
        app.logger.exception("DB connection check failed")
        return jsonify(
            {
                "error": f"DB check failed: {type(e).__name__}",
                "details": str(e),
            }
        ), 500


@app.get("/api/dbinfo")
def dbinfo():
    try:
        ensure_schema()

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'dbo'
            """)
            tables = sorted([r[0] for r in cur.fetchall()])

            def count_rows(table_name: str) -> int:
                cur.execute(f"SELECT COUNT(1) FROM dbo.{table_name}")
                row = cur.fetchone()
                return int(row[0]) if row else 0

            counts = {}
            for t in ["sessions", "messages", "summaries", "evidence_items", "uploads"]:
                counts[t] = count_rows(t) if t in tables else None

            cur.execute("""
                SELECT TOP 1 session_id, role, created_at
                FROM dbo.messages
                ORDER BY message_id DESC
            """)
            last_msg = cur.fetchone()
            last_message = None
            if last_msg:
                last_message = {
                    "session_id": str(last_msg[0]),
                    "role": last_msg[1],
                    "created_at": str(last_msg[2]),
                }

        finally:
            try:
                conn.close()
            except Exception:
                pass

        return jsonify({
            "status": "ok",
            "tables": tables,
            "row_counts": counts,
            "last_message": last_message
        })

    except Exception as e:
        app.logger.exception("dbinfo failed")
        return jsonify({
            "status": "error",
            "error": f"{type(e).__name__}",
            "details": str(e)
        }), 500


@app.post("/api/sessions")
def create_session():
    ensure_schema()
    sid = str(uuid.uuid4())

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dbo.sessions(session_id, user_label) VALUES (?, ?)", (sid, None))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return jsonify({"session_id": sid})


# ===============================
# File Uploads, List, Download
# ===============================

@app.post("/api/upload")
def api_upload():
    try:
        ensure_schema()

        session_id = (request.form.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400

        f = request.files["file"]
        if not f or not f.filename:
            return jsonify({"error": "file is required"}), 400

        original_name = secure_filename(f.filename)
        if not allowed_file(original_name):
            return jsonify({"error": "file type not allowed"}), 400

        ts = int(time.time())
        stored_name = f"{session_id}_{ts}_{original_name}"
        save_path = os.path.join(UPLOAD_DIR, stored_name)

        f.save(save_path)

        size_bytes = None
        try:
            size_bytes = os.path.getsize(save_path)
        except Exception:
            pass

        content_type = f.mimetype or None

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO dbo.uploads(session_id, stored_name, original_name, content_type, size_bytes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, stored_name, original_name, content_type, size_bytes),
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return jsonify({
            "status": "ok",
            "stored_name": stored_name,
            "original_name": original_name,
            "size_bytes": size_bytes,
            "content_type": content_type
        })

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

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT TOP 50 upload_id, original_name, stored_name, content_type, size_bytes, created_at
                FROM dbo.uploads
                WHERE session_id = ?
                ORDER BY upload_id DESC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        items = []
        for r in rows:
            items.append({
                "upload_id": int(r[0]),
                "original_name": r[1],
                "stored_name": r[2],
                "content_type": r[3],
                "size_bytes": int(r[4]) if r[4] is not None else None,
                "created_at": str(r[5]),
                "download_url": f"/api/download/{r[2]}"
            })

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
        safe_name = secure_filename(stored_name)
        file_path = os.path.join(UPLOAD_DIR, safe_name)

        if not os.path.isfile(file_path):
            return jsonify({"error": "File not found"}), 404

        return send_from_directory(UPLOAD_DIR, safe_name, as_attachment=True)

    except Exception as e:
        app.logger.exception("Download failed")
        return jsonify({
            "error": f"Download failed: {type(e).__name__}",
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

        # Store user message and load history
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO dbo.messages(session_id, role, content) VALUES (?, ?, ?)",
                (session_id, "user", user_message),
            )
            conn.commit()

            cur.execute(
                """
                SELECT TOP 20 role, content
                FROM dbo.messages
                WHERE session_id = ?
                ORDER BY message_id DESC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
            history = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        finally:
            try:
                conn.close()
            except Exception:
                pass

        system_text = "You are a helpful assistant for the CPL course."
        messages = [{"role": "system", "content": system_text}] + history

        response = client.chat.completions.create(
            model=deployment,
            messages=messages,
            temperature=0.3,
        )

        answer = (response.choices[0].message.content or "").strip()

        # Store assistant message
        conn2 = get_db_connection()
        try:
            cur2 = conn2.cursor()
            cur2.execute(
                "INSERT INTO dbo.messages(session_id, role, content) VALUES (?, ?, ?)",
                (session_id, "assistant", answer),
            )
            conn2.commit()
        finally:
            try:
                conn2.close()
            except Exception:
                pass

        return jsonify({"answer": answer})

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
