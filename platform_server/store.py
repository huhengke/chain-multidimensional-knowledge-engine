"""Transactional metadata, encrypted credentials and managed objects for one host."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "platform.sqlite3"
        # A deployment may provide its key from a secret manager. Local installs
        # create a private key once; backup/restore must include it separately.
        key = os.environ.get("PLATFORM_ENCRYPTION_KEY")
        key_file = self.root / ".encryption-key"
        if key is None:
            try:
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
            key = key_file.read_text(encoding="ascii").strip()
        self.cipher = Fernet(key.encode("ascii"))
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_versions VALUES(1);
                CREATE TABLE IF NOT EXISTS records(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_owner ON records(kind,owner,created_at);
                CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, upstream BLOB NOT NULL,
                    expires REAL NOT NULL, owner TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    owner TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
                    detail TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind: str, owner: str, value: dict, *, action: str | None = None) -> dict:
        value = dict(value)
        value.setdefault("id", uid(kind))
        value.setdefault("created_at", now())
        value["updated_at"] = now()
        with self.connect() as db:
            prior = db.execute("SELECT kind,owner FROM records WHERE id=?", (value["id"],)).fetchone()
            if prior and (prior["kind"] != kind or prior["owner"] != owner):
                raise ValueError("record ownership is immutable")
            db.execute("""INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(id)
                DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at""",
                (value["id"], kind, owner, json.dumps(value, ensure_ascii=False), value["created_at"], value["updated_at"]))
            if action:
                db.execute("INSERT INTO audit(at,owner,action,target,detail) VALUES(?,?,?,?,?)",
                           (now(), owner, action, value["id"], "{}"))
        return value

    def get(self, kind: str, owner: str, record_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM records WHERE kind=? AND owner=? AND id=?", (kind, owner, record_id)).fetchone()
        return json.loads(row["data"]) if row else None

    def owner_of(self, kind: str, record_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT owner FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return row["owner"] if row else None

    def list(self, kind: str, owner: str | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM records WHERE kind=?" + (" AND owner=?" if owner is not None else "") + " ORDER BY created_at,id", (kind, owner) if owner is not None else (kind,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def owners(self, kind: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT DISTINCT owner FROM records WHERE kind=? ORDER BY owner", (kind,)).fetchall()
        return [row["owner"] for row in rows]

    def delete(self, kind: str, owner: str, record_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM records WHERE kind=? AND owner=? AND id=?", (kind, owner, record_id))
        return cursor.rowcount > 0

    def secret(self, value: str) -> str:
        key = uid("secret")
        with self.connect() as db:
            db.execute("INSERT INTO secrets VALUES(?,?)", (key, self.cipher.encrypt(value.encode())))
        return key

    def reveal(self, key: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM secrets WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("credential reference not found")
        return self.cipher.decrypt(row["value"]).decode()

    def object(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        path = self.objects / digest
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("object checksum mismatch")
            return digest
        fd, staged = tempfile.mkstemp(prefix=".pending-", dir=self.objects)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # All writers for a hash have identical bytes. The final path only
            # becomes visible after the complete, flushed object is available.
            os.replace(staged, path)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        return digest

    def object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid object digest")
        return self.objects / digest

    def audit(self, owner: str, action: str, target: str, detail: dict | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO audit(at,owner,action,target,detail) VALUES(?,?,?,?,?)", (now(), owner, action, target, json.dumps(detail or {}, ensure_ascii=False)))

    def events(self, owner: str | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audit" + (" WHERE owner=?" if owner else "") + " ORDER BY seq DESC LIMIT 300", (owner,) if owner else ()).fetchall()
        return [dict(row, detail=json.loads(row["detail"])) for row in rows]
