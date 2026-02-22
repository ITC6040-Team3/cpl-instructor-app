import os
import logging
from flask import Flask, render_template, request, jsonify, send_from_directory
from openai import AzureOpenAI

# DB (optional)
import pyodbc

# Explicit template folder for Azure App Service reliability
app = Flask(__name__, template_folder="templates")
app.logger.setLevel(logging.INFO)


# ===============================
# Helpers
# ===============================
def getenv_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def get_client():
    """
    Azure OpenAI client factory.
    """
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
        return None, f"Client initialization failed: {type(e).__name__}: {e}"


def get_sql_connection_string():
    """
    Supports BOTH ways of configuring SQL on Azure App Service:

    A) App setting:
       SQL_CONNECTION_STRING=...

    B) App Service "Connection strings" blade:
       If you add name "SQL_CONNECTION_STRING", App Service exposes it as:
       SQLCONNSTR_SQL_CONNECTION_STRING

       (Also supports SQLAZURECONNSTR_ prefix just in case)
    """
    # Most direct (App settings)
    direct = os.getenv("SQL_CONNECTION_STRING")
    if direct:
        return direct

    # If user added it under "Connection strings" with name SQL_CONNECTION_STRING
    # App Service commonly exposes: SQLCONNSTR_<name>
    prefixed = os.getenv("SQLCONNSTR_SQL_CONNECTION_STRING")
    if prefixed:
        return prefixed

    # Some environments may use SQLAZURECONNSTR_
    prefixed2 = os.getenv("SQLAZURECONNSTR_SQL_CONNECTION_STRING")
    if prefixed2:
        return prefixed2

    # Optional: allow a generic "SQLCONNSTR" variable if someone used different naming
    # (kept conservative; comment out if you prefer strict)
    fallback = os.getenv("SQLCONNSTR")
    if fallback:
        return fallback

    return None


def sql_is_required() -> bool:
    """
    If you want to force SQL for everyone, set:
      REQUIRE_SQL=1
    in App Service -> Environment variables (App settings)
    """
    return getenv_bool("REQUIRE_SQL", default=False)


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
    conn_present = True if get_sql_connection_string() else False

    status = {
        "AZURE_OPENAI_ENDPOINT": "✅ set" if os.getenv("AZURE_OPENAI_ENDPOINT") else "❌ missing",
        "AZURE_OPENAI_API_KEY": "✅ set" if os.getenv("AZURE_OPENAI_API_KEY") else "❌ missing",
        "AZURE_OPENAI_API_VERSION": os.getenv("AZURE_OPENAI_API_VERSION") or "(default: 2024-12-01-preview)",
        "AZURE_OPENAI_DEPLOYMENT": "✅ set" if os.getenv("AZURE_OPENAI_DEPLOYMENT") else "❌ missing",
        # SQL is OPTIONAL unless REQUIRE_SQL=1
        "SQL_CONNECTION_STRING": (
            "✅ set" if conn_present else ("❌ missing (REQUIRED)" if sql_is_required() else "⚪ missing (optional)")
        ),
        "REQUIRE_SQL": "✅ enabled" if sql_is_required() else "⚪ disabled",
    }
    return render_template("admin.html", status=status)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


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
                "python_version": os.sys.version,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===============================
# ✅ DB CHECK ROUTE (optional)
# Verifies Web App can connect to Azure SQL
# ===============================
@app.get("/dbcheck")
def dbcheck():
    conn_str = get_sql_connection_string()

    if not conn_str:
        # If SQL is required, treat as error; otherwise just say "skipped"
        if sql_is_required():
            return jsonify({"error": "Missing SQL connection string (REQUIRE_SQL=1)"}), 500
        return jsonify({"status": "skipped", "message": "SQL not configured (optional)"}), 200

    try:
        conn = pyodbc.connect(conn_str, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        conn.close()
        return jsonify({"status": "DB Connected", "result": int(row[0])})
    except Exception as e:
        app.logger.exception("DB connection check failed")
        return jsonify(
            {
                "error": f"DB check failed: {type(e).__name__}",
                "details": str(e),
            }
        ), 500


# ===============================
# Chat API Endpoint
# ===============================
@app.post("/api/chat")
def api_chat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({"error": "Message is required"}), 400

        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not deployment:
            return jsonify({"error": "Missing AZURE_OPENAI_DEPLOYMENT"}), 500

        client, err = get_client()
        if err:
            return jsonify({"error": err}), 500

        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You are a helpful assistant for the CPL course."},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )

        answer = (response.choices[0].message.content or "").strip()
        return jsonify({"answer": answer})

    except Exception as e:
        app.logger.exception("Azure OpenAI call failed")
        return jsonify({"error": f"Azure OpenAI call failed: {type(e).__name__}: {e}"}), 500


# ===============================
# Local Dev Entry Point
# ===============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
