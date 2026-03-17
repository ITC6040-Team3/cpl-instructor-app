import os
import time
from werkzeug.utils import secure_filename

from db_utils import execute_non_query, fetch_all, fetch_one


ALLOWED_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "gif",
    "doc", "docx", "txt"
}


def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def build_stored_filename(session_id: str, original_name: str) -> str:
    safe_original = secure_filename(original_name)
    ts = int(time.time())
    return f"{session_id}_{ts}_{safe_original}"


def save_file_to_disk(file_obj, upload_dir: str, session_id: str):
    original_name = secure_filename(file_obj.filename or "")
    if not original_name:
        raise ValueError("Invalid filename")

    if not allowed_file(original_name):
        raise ValueError("File type not allowed")

    os.makedirs(upload_dir, exist_ok=True)

    stored_name = build_stored_filename(session_id, original_name)
    save_path = os.path.join(upload_dir, stored_name)

    file_obj.save(save_path)

    size_bytes = None
    try:
        size_bytes = os.path.getsize(save_path)
    except Exception:
        pass

    content_type = getattr(file_obj, "mimetype", None) or None

    return {
        "stored_name": stored_name,
        "original_name": original_name,
        "save_path": save_path,
        "size_bytes": size_bytes,
        "content_type": content_type,
    }


def add_upload_record(session_id, stored_name, original_name, content_type=None, size_bytes=None):
    execute_non_query(
        """
        INSERT INTO dbo.uploads(session_id, stored_name, original_name, content_type, size_bytes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, stored_name, original_name, content_type, size_bytes),
    )


def list_uploads(session_id):
    return fetch_all(
        """
        SELECT upload_id, original_name, stored_name, content_type, size_bytes, created_at
        FROM dbo.uploads
        WHERE session_id = ?
        ORDER BY upload_id DESC
        """,
        (session_id,),
    )


def get_upload(upload_id, session_id=None):
    if session_id:
        return fetch_one(
            """
            SELECT upload_id, session_id, original_name, stored_name, content_type, size_bytes, created_at
            FROM dbo.uploads
            WHERE upload_id = ? AND session_id = ?
            """,
            (upload_id, session_id),
        )

    return fetch_one(
        """
        SELECT upload_id, session_id, original_name, stored_name, content_type, size_bytes, created_at
        FROM dbo.uploads
        WHERE upload_id = ?
        """,
        (upload_id,),
    )


def get_upload_by_stored_name(stored_name):
    return fetch_one(
        """
        SELECT upload_id, session_id, original_name, stored_name, content_type, size_bytes, created_at
        FROM dbo.uploads
        WHERE stored_name = ?
        """,
        (stored_name,),
    )


def delete_upload_record(upload_id, session_id):
    execute_non_query(
        """
        DELETE FROM dbo.uploads
        WHERE upload_id = ? AND session_id = ?
        """,
        (upload_id, session_id),
    )


def delete_file_from_disk(upload_dir: str, stored_name: str) -> bool:
    safe_name = secure_filename(stored_name)
    file_path = os.path.join(upload_dir, safe_name)

    if not os.path.isfile(file_path):
        return False

    os.remove(file_path)
    return True


def file_exists_on_disk(upload_dir: str, stored_name: str) -> bool:
    safe_name = secure_filename(stored_name)
    file_path = os.path.join(upload_dir, safe_name)
    return os.path.isfile(file_path)


def list_uploads_with_file_state(session_id, upload_dir: str):
    items = list_uploads(session_id)

    for item in items:
        stored_name = item.get("stored_name")
        item["exists_on_disk"] = file_exists_on_disk(upload_dir, stored_name)

    return items


def extract_text_preview(file_path: str, max_chars: int = 600) -> str:
    """
    Safe preview extractor that does not require extra packages.
    - Always supports plain text files.
    - Falls back to no preview for file types that require optional parsers.
    - Never raises parsing errors to the caller.
    """
    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(max_chars).strip()

        # Do not require extra packages for PDF/DOCX parsing.
        # If future runtime environments include safe built-in parsing support,
        # this function can be extended without changing the API.
        if ext in {".pdf", ".docx", ".doc"}:
            return ""

    except Exception:
        return ""

    return ""


def get_upload_text_preview(upload_dir: str, stored_name: str, max_chars: int = 600) -> str:
    safe_name = secure_filename(stored_name)
    file_path = os.path.join(upload_dir, safe_name)

    if not os.path.isfile(file_path):
        return ""

    return extract_text_preview(file_path, max_chars=max_chars)
