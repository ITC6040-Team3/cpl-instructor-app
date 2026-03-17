from db_utils import execute_non_query, fetch_all, fetch_one


def create_chat_session(session_id: str, user_label=None):
    execute_non_query(
        """
        INSERT INTO dbo.sessions(session_id, user_label)
        VALUES (?, ?)
        """,
        (session_id, user_label),
    )


def ensure_chat_session(session_id: str, user_label=None):
    existing = fetch_one(
        """
        SELECT session_id
        FROM dbo.sessions
        WHERE session_id = ?
        """,
        (session_id,),
    )

    if not existing:
        create_chat_session(session_id, user_label)


def get_chat_session(session_id: str):
    return fetch_one(
        """
        SELECT session_id, created_at, user_label
        FROM dbo.sessions
        WHERE session_id = ?
        """,
        (session_id,),
    )


def add_chat_message(session_id: str, role: str, content: str):
    execute_non_query(
        """
        INSERT INTO dbo.messages(session_id, role, content)
        VALUES (?, ?, ?)
        """,
        (session_id, role, content),
    )


def get_chat_messages(session_id: str, limit: int = 20):
    query = f"""
        SELECT TOP {limit} message_id, role, content, created_at
        FROM dbo.messages
        WHERE session_id = ?
        ORDER BY message_id DESC
    """
    rows = fetch_all(query, (session_id,))
    rows.reverse()
    return rows


def get_all_chat_messages(session_id: str):
    return fetch_all(
        """
        SELECT message_id, role, content, created_at
        FROM dbo.messages
        WHERE session_id = ?
        ORDER BY message_id ASC
        """,
        (session_id,),
    )


def save_summary(session_id: str, summary_text: str):
    existing = fetch_one(
        """
        SELECT session_id
        FROM dbo.summaries
        WHERE session_id = ?
        """,
        (session_id,),
    )

    if existing:
        execute_non_query(
            """
            UPDATE dbo.summaries
            SET summary_text = ?, updated_at = SYSUTCDATETIME()
            WHERE session_id = ?
            """,
            (summary_text, session_id),
        )
    else:
        execute_non_query(
            """
            INSERT INTO dbo.summaries(session_id, summary_text)
            VALUES (?, ?)
            """,
            (session_id, summary_text),
        )


def get_summary(session_id: str):
    return fetch_one(
        """
        SELECT session_id, summary_text, updated_at
        FROM dbo.summaries
        WHERE session_id = ?
        """,
        (session_id,),
    )


def delete_chat_session(session_id: str):
    # Delete child rows first because of foreign keys
    execute_non_query(
        "DELETE FROM dbo.messages WHERE session_id = ?",
        (session_id,),
    )

    execute_non_query(
        "DELETE FROM dbo.summaries WHERE session_id = ?",
        (session_id,),
    )

    execute_non_query(
        "DELETE FROM dbo.sessions WHERE session_id = ?",
        (session_id,),
    )
