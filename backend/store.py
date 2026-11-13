"""Persistent local conversation storage. Never opens the industry database."""
import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                    dataset_id TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL REFERENCES sessions(id),
                    client_message_id TEXT NOT NULL, question TEXT NOT NULL,
                    status TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL,
                    UNIQUE(session_id, client_message_id));
            """)
            # Only stale work is recovered at process start, never during live requests.
            for row in con.execute("SELECT id FROM requests WHERE status IN ('queued','processing')").fetchall():
                payload = {"request_id":row["id"], "status":"error", "data":None,
                           "error":{"code":"SERVICE_RESTARTED","message":"服务已重新启动，请重新提交这条问题。","retryable":True}}
                con.execute("UPDATE requests SET status='error',response=? WHERE id=?",
                            (json.dumps(payload,ensure_ascii=False),row["id"]))


    def create_session(self, dataset):
        sid, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        with self.connect() as con:
            con.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                        (sid,digest(token),dataset,now()))
        return {"session_id":sid,"session_token":token,"dataset_id":dataset}

    def authorized(self, sid, token):
        with self.connect() as con:
            row = con.execute("SELECT token_hash FROM sessions WHERE id=?",(sid,)).fetchone()
        return bool(row and token and secrets.compare_digest(row["token_hash"],digest(token)))

    def enqueue(self, sid, client_id, question):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT id,question FROM requests WHERE session_id=? AND client_message_id=?",
                              (sid,client_id)).fetchone()
            if old:
                if old["question"] != question:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return old["id"], False
            rid = str(uuid.uuid4())
            con.execute("""INSERT INTO requests(id,session_id,client_message_id,question,status,created_at)
                           VALUES(?,?,?,?,'queued',?)""",(rid,sid,client_id,question,now()))
        return rid, True

    def finish(self, rid, response):
        response = {"request_id":rid,**response}
        with self.connect() as con:
            con.execute("UPDATE requests SET status=?,response=? WHERE id=?",
                        (response["status"],json.dumps(response,ensure_ascii=False),rid))

    def request(self, rid):
        with self.connect() as con:
            row = con.execute("SELECT * FROM requests WHERE id=?",(rid,)).fetchone()
        return dict(row) if row else None

    def messages(self, sid):
        with self.connect() as con:
            rows = con.execute("SELECT * FROM requests WHERE session_id=? ORDER BY seq",(sid,)).fetchall()
        return [{"request_id":r["id"],"question":r["question"],"status":r["status"],
                 "created_at":r["created_at"],"response":json.loads(r["response"]) if r["response"] else None}
                for r in rows]
