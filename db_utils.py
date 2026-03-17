import os
import pyodbc


def get_sql_connection_string():
    direct = os.getenv("SQL_CONNECTION_STRING")
    if direct:
        return direct

    prefixed = os.getenv("SQLCONNSTR_SQL_CONNECTION_STRING")
    if prefixed:
        return prefixed

    prefixed2 = os.getenv("SQLAZURECONNSTR_SQL_CONNECTION_STRING")
    if prefixed2:
        return prefixed2

    return None


def get_db_connection():
    conn_str = get_sql_connection_string()
    if not conn_str:
        raise RuntimeError("SQL connection string is not configured.")
    return pyodbc.connect(conn_str, timeout=10)


def run_sql_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


def fetch_all(query: str, params=()):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        columns = [c[0] for c in cur.description]
        rows = cur.fetchall()
        return [dict(zip(columns, row)) for row in rows]


def fetch_one(query: str, params=()):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        row = cur.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cur.description]
        return dict(zip(columns, row))


def execute_non_query(query: str, params=()):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()


def health_check():
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        return {"ok": row[0] == 1}
