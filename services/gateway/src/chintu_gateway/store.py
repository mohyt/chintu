import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import ApiError


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
ACTIVE = {"running", "waiting_for_input", "cancelling"}
DEFAULT_TOKEN_TTL_DAYS = 30
MAX_OPEN_ENROLLMENTS = 5


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def verifier(value):
    return hashlib.sha256(value.encode()).hexdigest()


def result(summary, code=None, *, partial_effects=False):
    return {"summary": summary, "speech_text": summary[:2000], "artifact_ids": [],
            "partial_effects": ["Effects may have occurred before execution stopped; review the workspace."] if partial_effects else [],
            "error": None if code is None else
            {"code": code, "message": summary, "retryable": False, "details": {}}}


class Store:
    """Short SQLite transactions keep snapshots, cursors, and operation keys atomic."""

    def __init__(self, path, queue_limit=10, project_paths=None, token_ttl_days=DEFAULT_TOKEN_TTL_DAYS):
        self.path = Path(path).resolve()
        self.project_paths = {key: str(Path(value).resolve()) for key, value in (project_paths or {}).items()}
        self.queue_limit = queue_limit
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        if type(token_ttl_days) is not int or not 1 <= token_ttl_days <= 365:
            raise ValueError("token_ttl_days must be an integer between 1 and 365")
        self.token_ttl_seconds = token_ttl_days * 86400
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # umask must also protect WAL/SHM and worker-lock files at runtime.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS enrollments(
                    verifier TEXT PRIMARY KEY, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS clients(
                    client_id TEXT PRIMARY KEY, name TEXT NOT NULL, verifier TEXT UNIQUE NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tasks(
                    task_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, state TEXT NOT NULL,
                    project_id TEXT NOT NULL, instruction TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(
                    client_id TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
                    digest TEXT NOT NULL, task_id TEXT NOT NULL,
                    PRIMARY KEY(client_id, operation, key));
                CREATE TABLE IF NOT EXISTS events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, envelope TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_task ON events(task_id, event_id);
                CREATE TABLE IF NOT EXISTS task_workspaces(task_id TEXT PRIMARY KEY, path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_blocks(
                    task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL,
                    attempt_id TEXT, created_at TEXT NOT NULL, cleared_at TEXT, review_record TEXT);
            """)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notes(
                    note_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    finished_at TEXT, file TEXT);
                CREATE TABLE IF NOT EXISTS note_chunks(
                    note_id TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(note_id, seq));
            """)
            # Presence columns for the local console; additive so older databases keep working.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(clients)")}
            if "paired_at" not in columns:
                db.execute("ALTER TABLE clients ADD COLUMN paired_at REAL")
            if "last_seen" not in columns:
                db.execute("ALTER TABLE clients ADD COLUMN last_seen REAL")
            if "expires_at" not in columns:
                # Credentials paired before expiry existed get the configured lifetime from now.
                db.execute("ALTER TABLE clients ADD COLUMN expires_at REAL")
                db.execute("UPDATE clients SET expires_at=? WHERE expires_at IS NULL",
                           (time.time() + self.token_ttl_seconds,))
            task_columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "client_id" not in task_columns:
                # Tasks are private to the pairing client; rows from before this column have no owner
                # and stay invisible to every phone (the worker and operator tools still see them).
                db.execute("ALTER TABLE tasks ADD COLUMN client_id TEXT")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def create_enrollment(self, ttl_seconds=300):
        if not 1 <= ttl_seconds <= 900:
            raise ValueError("Enrollment expiry must be between 1 and 900 seconds")
        secret = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute("DELETE FROM enrollments WHERE consumed=1 OR expires<=?", (time.time(),))
            # Bound the pool of live secrets: the newest MAX_OPEN_ENROLLMENTS survive, oldest go.
            db.execute("DELETE FROM enrollments WHERE consumed=0 AND verifier NOT IN "
                       "(SELECT verifier FROM enrollments WHERE consumed=0 ORDER BY expires DESC LIMIT ?)",
                       (MAX_OPEN_ENROLLMENTS - 1,))
            db.execute("INSERT INTO enrollments(verifier, expires) VALUES (?, ?)",
                       (verifier(secret), time.time() + ttl_seconds))
        return secret

    def open_enrollments(self):
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) AS n FROM enrollments WHERE consumed=0 AND expires>?",
                              (time.time(),)).fetchone()["n"]

    def pair(self, secret, name):
        token, client_id = secrets.token_urlsafe(48), str(uuid4())
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE enrollments SET consumed=1 WHERE verifier=? AND consumed=0 AND expires>?",
                (verifier(secret), time.time())).rowcount
            if changed != 1:
                raise ApiError(401, "unauthorized", "Enrollment secret is invalid or expired.")
            db.execute("INSERT INTO clients(client_id,name,verifier,paired_at,last_seen,expires_at) VALUES (?,?,?,?,?,?)",
                       (client_id, name, verifier(token), time.time(), time.time(), time.time() + self.token_ttl_seconds))
        return {"client_id": client_id, "token": token, "token_type": "Bearer"}

    def authenticate(self, token):
        with self.connection() as db:
            row = db.execute("SELECT client_id, last_seen FROM clients WHERE verifier=? AND revoked=0 AND expires_at>?",
                             (verifier(token), time.time())).fetchone()
            if row and (row["last_seen"] is None or time.time() - row["last_seen"] > 5):
                # Coarse presence stamp; throttled so polling doesn't turn every request into a write.
                db.execute("UPDATE clients SET last_seen=? WHERE client_id=?", (time.time(), row["client_id"]))
        if not row:
            raise ApiError(401, "unauthorized", "A valid client credential is required.")
        return row["client_id"]

    # --- Voice notes: chunks stream in while the owner talks; finishing writes one Markdown file. ---
    def note_create(self, client_id):
        note_id = str(uuid4())
        with self.transaction() as db:
            db.execute("INSERT INTO notes(note_id, client_id, created_at) VALUES (?,?,?)", (note_id, client_id, now()))
        return {"note_id": note_id, "created_at": now()}

    def _note(self, db, note_id, client_id):
        row = db.execute("SELECT note_id, client_id, created_at, finished_at, file FROM notes WHERE note_id=?", (note_id,)).fetchone()
        if row is None or row["client_id"] != client_id:
            raise ApiError(404, "not_found", "Note not found.")
        return row

    def note_append(self, note_id, client_id, text):
        with self.transaction() as db:
            row = self._note(db, note_id, client_id)
            if row["finished_at"]:
                raise ApiError(409, "conflict", "This note is already finished.")
            count = db.execute("SELECT COUNT(*) AS n FROM note_chunks WHERE note_id=?", (note_id,)).fetchone()["n"]
            if count >= 500:
                raise ApiError(409, "conflict", "This note is full; finish it and start another.")
            db.execute("INSERT INTO note_chunks(note_id, seq, text) VALUES (?,?,?)", (note_id, count + 1, text.strip()))
            words = sum(len(r["text"].split()) for r in db.execute("SELECT text FROM note_chunks WHERE note_id=?", (note_id,)))
        return {"note_id": note_id, "chunks": count + 1, "words": words}

    def note_finish(self, note_id, client_id, notes_dir):
        with self.transaction() as db:
            row = self._note(db, note_id, client_id)
            chunks = [r["text"] for r in db.execute("SELECT text FROM note_chunks WHERE note_id=? ORDER BY seq", (note_id,))]
            if row["finished_at"]:
                return {"note_id": note_id, "file": row["file"] or "", "words": sum(len(c.split()) for c in chunks),
                        "finished_at": row["finished_at"], "text": "\n\n".join(chunks)[:4000]}
            stamp = datetime.now().astimezone()
            finished = now()
            if not any(c.strip() for c in chunks):
                # Nothing was dictated: close the note without littering the folder with empty files.
                db.execute("UPDATE notes SET finished_at=?, file=? WHERE note_id=?", (finished, "", note_id))
                return {"note_id": note_id, "file": "", "words": 0, "finished_at": finished, "text": ""}
            file_name = stamp.strftime("%Y-%m-%d-%H%M") + "-" + note_id[:8] + ".md"
            folder = Path(notes_dir); folder.mkdir(parents=True, exist_ok=True)
            body = "# Note " + stamp.strftime("%Y-%m-%d %H:%M") + "\n\n" + "\n\n".join(chunks) + "\n"
            target = folder / file_name
            target.write_text(body, encoding="utf-8")
            os.chmod(target, 0o600)
            db.execute("UPDATE notes SET finished_at=?, file=? WHERE note_id=?", (finished, file_name, note_id))
        return {"note_id": note_id, "file": file_name, "words": sum(len(c.split()) for c in chunks),
                "finished_at": finished, "text": "\n\n".join(chunks)[:4000]}

    def clients(self, presence_seconds=120):
        """Operator-facing client list; verifiers never leave the database."""
        current = time.time()
        with self.connection() as db:
            return [{"client_id": row["client_id"], "name": row["name"],
                     "paired_at": row["paired_at"], "last_seen": row["last_seen"], "expires_at": row["expires_at"],
                     "connected": row["last_seen"] is not None and current - row["last_seen"] <= presence_seconds
                     and row["expires_at"] > current}
                    for row in db.execute("SELECT client_id,name,paired_at,last_seen,expires_at FROM clients "
                                          "WHERE revoked=0 AND expires_at>? ORDER BY paired_at", (current,))]

    def local_status(self, presence_seconds=120):
        """Operator-facing summary for the desktop companion; no secrets, no task text."""
        clients = self.clients(presence_seconds)
        with self.connection() as db:
            active = db.execute("SELECT COUNT(*) AS n FROM tasks WHERE state IN ('running','cancelling','waiting_for_input')").fetchone()["n"]
            total = db.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        return {"clients": clients, "phone_connected": any(c["connected"] for c in clients),
                "active_tasks": active, "total_tasks": total}

    def revoke(self, client_id):
        """True when a live client was revoked; revoking again or an unknown ID is False."""
        with self.transaction() as db:
            return db.execute("UPDATE clients SET revoked=1 WHERE client_id=? AND revoked=0", (client_id,)).rowcount == 1

    def revoke_all(self):
        with self.transaction() as db:
            return db.execute("UPDATE clients SET revoked=1 WHERE revoked=0").rowcount

    def _get(self, db, task_id, client_id=None):
        """Snapshot by ID; with ``client_id`` another client's task is indistinguishable from none."""
        row = db.execute("SELECT snapshot, client_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or (client_id is not None and row["client_id"] != client_id):
            raise ApiError(404, "not_found", "Task not found.")
        return json.loads(row["snapshot"])

    def get(self, task_id, client_id=None):
        with self.connection() as db:
            return self._get(db, task_id, client_id)

    def workspace_for_task(self, task_id):
        with self.connection() as db:
            snapshot = self._get(db, task_id)
            return self._workspace(db, task_id, snapshot["project_id"])

    def _event(self, db, snapshot, kind, payload):
        snapshot["task_version"] += 1
        snapshot["updated_at"] = now()
        envelope = {"task_id": snapshot["task_id"], "attempt_id": snapshot["attempt_id"],
                    "kind": kind, "timestamp": snapshot["updated_at"],
                    "task_version": snapshot["task_version"], "payload": payload}
        cursor = db.execute("INSERT INTO events(task_id,envelope) VALUES (?,?)",
                            (snapshot["task_id"], encode(envelope)))
        envelope["event_id"] = cursor.lastrowid
        db.execute("UPDATE events SET envelope=? WHERE event_id=?", (encode(envelope), cursor.lastrowid))
        db.execute("UPDATE tasks SET snapshot=?,state=? WHERE task_id=?",
                   (encode(snapshot), snapshot["state"], snapshot["task_id"]))
        if snapshot["state"] == "interrupted" and snapshot["provider"] != "scripted":
            self._block(db, snapshot)

    def _workspace(self, db, task_id, project_id):
        row = db.execute("SELECT path FROM task_workspaces WHERE task_id=?", (task_id,)).fetchone()
        return row["path"] if row else self.project_paths.get(project_id, "project:" + project_id)

    def _block(self, db, snapshot):
        db.execute("INSERT OR IGNORE INTO workspace_blocks VALUES (?,?,?,?,?,NULL,NULL)",
                   (snapshot["task_id"], snapshot["project_id"],
                    self._workspace(db, snapshot["task_id"], snapshot["project_id"]),
                    snapshot["attempt_id"], now()))

    def _workspace_check(self, db, project_id):
        path = self.project_paths.get(project_id, "project:" + project_id)
        if db.execute("SELECT 1 FROM workspace_blocks WHERE path=? AND cleared_at IS NULL", (path,)).fetchone():
            raise ApiError(409, "invalid_state", "Workspace is awaiting local execution reconciliation.")

    def _replay(self, db, client_id, operation, key, content):
        row = db.execute("SELECT digest,task_id FROM operations WHERE client_id=? AND operation=? AND key=?",
                         (client_id, operation, key)).fetchone()
        if row:
            if row["digest"] != digest(content):
                raise ApiError(409, "idempotency_conflict", "Operation key was used with different content.")
            return self._get(db, row["task_id"])
        return None

    def _record(self, db, client_id, operation, key, content, task_id):
        db.execute("INSERT INTO operations VALUES (?,?,?,?,?)",
                   (client_id, operation, key, digest(content), task_id))

    def _queue_check(self, db):
        count = db.execute("SELECT count(*) FROM tasks WHERE state='queued'").fetchone()[0]
        if count >= self.queue_limit:
            raise ApiError(429, "queue_full", "Task queue is full.", retryable=True)

    def submit(self, client_id, body, validate_capability):
        body = {"conversation_id": None, "title": None, **body}
        with self.transaction() as db:
            existing = self._replay(db, client_id, "submit", body["submission_key"], body)
            if existing is not None:
                return existing, True
            validate_capability(body)
            self._workspace_check(db, body["project_id"])
            self._queue_check(db)
            stamp, task_id = now(), str(uuid4())
            snapshot = {k: body[k] for k in ("project_id", "provider", "instruction",
                         "execution_profile", "conversation_id", "title")}
            snapshot.update(task_id=task_id, attempt_id=None, provider_session_id=None,
                            state="queued", task_version=0, created_at=stamp, updated_at=stamp,
                            result=None, pending_input=None)
            db.execute("INSERT INTO tasks(task_id,snapshot,state,project_id,instruction,client_id) VALUES (?,?,?,?,?,?)",
                       (task_id, encode(snapshot), "queued", body["project_id"], body["instruction"], client_id))
            db.execute("INSERT INTO task_workspaces VALUES (?,?)",
                       (task_id, self.project_paths.get(body["project_id"], "project:" + body["project_id"])))
            self._event(db, snapshot, "task_accepted", {"state": "queued"})
            self._record(db, client_id, "submit", body["submission_key"], body, task_id)
        return snapshot, False

    def mutate(self, client_id, task_id, operation, body, capabilities):
        content = {"task_id": task_id, **body}
        signal = None
        with self.transaction() as db:
            existing = self._replay(db, client_id, operation, body["message_id"], content)
            if existing is not None:
                return existing, True, None
            snapshot = self._get(db, task_id, client_id)
            if operation == "followup":
                if body["expected_task_version"] != snapshot["task_version"]:
                    raise ApiError(409, "stale_task_version", "Task changed; refresh before sending new work.")
                if not capabilities.continuation:
                    raise ApiError(501, "unsupported_capability", "Session continuation is unavailable.")
                if snapshot["state"] in ACTIVE and not capabilities.active_steering:
                    raise ApiError(501, "unsupported_capability", "Active session steering is unavailable.")
                if snapshot["state"] != "succeeded" or not snapshot["provider_session_id"]:
                    raise ApiError(409, "invalid_state", "Follow-up requires a completed, idle provider session.")
                if snapshot["provider"] != capabilities.provider or not capabilities.available:
                    raise ApiError(501, "unsupported_capability", "Task provider is not currently enabled.")
                if snapshot["execution_profile"] not in capabilities.execution_profiles:
                    raise ApiError(501, "unsupported_capability", "Task execution profile is not currently enabled.")
                if self._workspace(db, task_id, snapshot["project_id"]) != self.project_paths.get(
                        snapshot["project_id"], "project:" + snapshot["project_id"]):
                    raise ApiError(409, "invalid_state", "Registered workspace changed after task acceptance.")
                self._workspace_check(db, snapshot["project_id"])
                self._queue_check(db)
                snapshot.update(state="queued", result=None, pending_input=None, attempt_id=None)
                db.execute("UPDATE tasks SET instruction=? WHERE task_id=?", (body["instruction"], task_id))
                self._event(db, snapshot, "task_accepted", {"state": "queued"})
            elif operation == "cancel":
                if snapshot["state"] not in TERMINAL:
                    if snapshot["state"] == "queued":
                        snapshot.update(state="cancelled", result=result("Task cancelled before execution."))
                        self._event(db, snapshot, "cancelled", snapshot["result"])
                    elif snapshot["state"] != "cancelling":
                        if (not capabilities.cancellation or not capabilities.available
                                or snapshot["provider"] != capabilities.provider):
                            raise ApiError(501, "unsupported_capability", "Cancellation is unavailable.")
                        snapshot["state"] = "cancelling"
                        self._event(db, snapshot, "cancellation_requested", {"state": "cancelling"})
                        signal = snapshot["attempt_id"]
            self._record(db, client_id, operation, body["message_id"], content, task_id)
        return snapshot, False, signal

    def claim(self, provider=None):
        with self.transaction() as db:
            # V1 globally serializes execution, also preventing workspace collisions.
            if db.execute("SELECT 1 FROM tasks WHERE state IN ('running','cancelling','waiting_for_input') LIMIT 1").fetchone():
                return None
            rows = db.execute("SELECT task_id,instruction,project_id,snapshot FROM tasks WHERE state='queued' ORDER BY rowid").fetchall()
            row = None
            for candidate in rows:
                snapshot = json.loads(candidate["snapshot"])
                if provider is not None and snapshot["provider"] != provider:
                    continue
                workspace = self._workspace(db, candidate["task_id"], candidate["project_id"])
                if db.execute("SELECT 1 FROM workspace_blocks WHERE path=? AND cleared_at IS NULL", (workspace,)).fetchone():
                    continue
                row = candidate
                break
            if row is None:
                return None
            snapshot = self._get(db, row["task_id"])
            snapshot.update(state="running", attempt_id=str(uuid4()))
            self._event(db, snapshot, "started", {"state": "running"})
            return snapshot, row["instruction"]

    def workspace_blocks(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM workspace_blocks WHERE cleared_at IS NULL ORDER BY created_at")]

    def reconcile(self, task_id, review_record):
        """Record an operator's externally checked stop; never re-launch the interrupted task."""
        record = Path(review_record).resolve()
        if not record.is_file() or record.stat().st_size > 65536 or not record.read_bytes().strip():
            raise ValueError("Reconciliation requires a nonempty local review file of at most 64 KiB")
        with self.transaction() as db:
            snapshot = self._get(db, task_id)
            if snapshot["state"] != "interrupted":
                raise ApiError(409, "invalid_state", "Only interrupted execution can be reconciled locally.")
            row = db.execute("SELECT * FROM workspace_blocks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise ApiError(409, "invalid_state", "No uncertain real execution is quarantined for this task.")
            if row["cleared_at"] is None:
                db.execute("UPDATE workspace_blocks SET cleared_at=?,review_record=? WHERE task_id=?",
                           (now(), str(record), task_id))
        return snapshot

    def adapter_event(self, task_id, attempt_id, kind, payload, session_id=None):
        with self.transaction() as db:
            snapshot = self._get(db, task_id)
            if snapshot["attempt_id"] != attempt_id or snapshot["state"] in TERMINAL:
                return False
            if session_id:
                snapshot["provider_session_id"] = session_id
            if kind == "started":
                # Persist session identity with an event; claim already recorded running.
                self._event(db, snapshot, "started", {"state": snapshot["state"]})
            elif kind in {"progress", "output"}:
                self._event(db, snapshot, kind, payload)
            elif kind in TERMINAL:
                snapshot.update(state=kind, result=payload, pending_input=None)
                self._event(db, snapshot, "completed" if kind == "succeeded" else kind, payload)
            else:
                raise ValueError("Unsupported adapter event")
        return True

    def recover(self):
        with self.transaction() as db:
            ids = db.execute("SELECT task_id FROM tasks WHERE state IN ('running','cancelling','waiting_for_input')").fetchall()
            for row in ids:
                snapshot = self._get(db, row["task_id"])
                snapshot.update(state="interrupted", pending_input=None, result=result(
                    "Execution was interrupted. Reconciliation is required; no automatic retry.", "worker_lost",
                    partial_effects=snapshot["provider"] != "scripted"))
                self._event(db, snapshot, "interrupted", snapshot["result"])

    def tasks_page(self, limit, cursor=None, client_id=None):
        """Tasks visible to ``client_id``; ``None`` is the operator's unscoped view."""
        after = 0
        if cursor is not None:
            try:
                after = int(cursor)
                if after < 0 or str(after) != cursor:
                    raise ValueError
            except ValueError:
                raise ApiError(400, "invalid_cursor", "Invalid task cursor.") from None
        query, args = "SELECT rowid,snapshot FROM tasks WHERE rowid>?", [after]
        if client_id is not None:
            query += " AND client_id=?"
            args.append(client_id)
        with self.connection() as db:
            rows = db.execute(query + " ORDER BY rowid LIMIT ?", (*args, limit + 1)).fetchall()
        return {"tasks": [json.loads(r["snapshot"]) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1]["rowid"]) if len(rows) > limit else None}

    def events_page(self, after, limit, task_id=None, client_id=None):
        with self.connection() as db:
            maximum = db.execute("SELECT coalesce(max(event_id),0) FROM events").fetchone()[0]
            if after > maximum:
                raise ApiError(400, "invalid_cursor", "Event cursor is ahead of the server.")
            query = "SELECT envelope FROM events WHERE event_id>?"
            args = [after]
            if task_id is not None:
                query += " AND task_id=?"
                args.append(task_id)
            if client_id is not None:
                query += " AND task_id IN (SELECT task_id FROM tasks WHERE client_id=?)"
                args.append(client_id)
            rows = db.execute(query + " ORDER BY event_id LIMIT ?", (*args, limit + 1)).fetchall()
        events = [json.loads(row["envelope"]) for row in rows[:limit]]
        return {"events": events, "next_cursor": events[-1]["event_id"] if events else after,
                "has_more": len(rows) > limit}
