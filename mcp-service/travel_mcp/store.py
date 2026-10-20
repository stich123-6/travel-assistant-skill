"""User-scoped SQLite persistence; third-party secrets stay encrypted at rest."""

import hashlib
import hmac
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ServiceError("invalid_input", f"{name}必须是有效文本")
    return value.strip()


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ServiceError("invalid_input", "内容必须为有效JSON") from exc


def _revision(value):
    if type(value) is not int or value < 1:
        raise ServiceError("conflict", "请提供当前版本后重试")
    return value


class Store:
    def __init__(self, data_dir: Path, encryption_key: str):
        try:
            if not isinstance(encryption_key, str) or not encryption_key:
                raise ValueError("missing key")
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ServiceError("configuration_error", "必须配置有效的服务端加密主密钥") from exc
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "travel.sqlite3"
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS credentials (
                    user_id TEXT NOT NULL REFERENCES users(id), provider TEXT NOT NULL,
                    encrypted_values BLOB NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, provider)
                );
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_user ON records(user_id);
                CREATE TABLE IF NOT EXISTS origins (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    label TEXT NOT NULL, address TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS origins_user ON origins(user_id);
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _password(password, salt):
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)

    def create_user(self, username, password):
        username = _text(username, "用户名", 200)
        _text(password, "密码", 10000)
        user_id, salt = str(uuid.uuid4()), os.urandom(16)
        password_hash = self._password(password, salt)
        try:
            with self.connection() as connection:
                connection.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (user_id, username, salt, password_hash, _now()))
        except sqlite3.IntegrityError as exc:
            raise ServiceError("conflict", "用户名已存在") from exc
        return {"id": user_id, "username": username}

    def authenticate(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 10000:
            return None
        with self.connection() as connection:
            row = connection.execute("SELECT id, password_salt, password_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()
        salt = row["password_salt"] if row else b"missing-user-salt"
        candidate = self._password(password, salt)
        return row["id"] if row and hmac.compare_digest(candidate, row["password_hash"]) else None

    def get_user(self, user_id):
        with self.connection() as connection:
            row = connection.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def _require_user(self, user_id):
        if not self.get_user(user_id):
            raise ServiceError("not_found", "用户不存在")

    @staticmethod
    def _provider(provider):
        if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
            raise ServiceError("invalid_input", "服务名称无效")
        return provider

    def set_credentials(self, user_id, provider, values):
        self._require_user(user_id)
        provider = self._provider(provider)
        if not isinstance(values, dict) or not values:
            raise ServiceError("invalid_input", "凭证必须为非空对象")
        encrypted = self._cipher.encrypt(_json(values).encode("utf-8"))
        with self.connection() as connection:
            connection.execute("INSERT INTO credentials VALUES (?, ?, ?, ?) ON CONFLICT(user_id, provider) DO UPDATE SET encrypted_values = excluded.encrypted_values, updated_at = excluded.updated_at", (user_id, provider, encrypted, _now()))

    def get_credentials(self, user_id, provider):
        provider = self._provider(provider)
        with self.connection() as connection:
            row = connection.execute("SELECT encrypted_values FROM credentials WHERE user_id = ? AND provider = ?", (user_id, provider)).fetchone()
        if not row:
            return None
        try:
            return json.loads(self._cipher.decrypt(row["encrypted_values"]))
        except (InvalidToken, ValueError) as exc:
            raise ServiceError("credentials_unavailable", "凭证无法读取，请检查服务端主密钥") from exc

    def credential_status(self, user_id):
        with self.connection() as connection:
            rows = connection.execute("SELECT provider, updated_at FROM credentials WHERE user_id = ? ORDER BY provider", (user_id,)).fetchall()
        return {row["provider"]: {"configured": True, "updated_at": row["updated_at"]} for row in rows}

    def delete_credentials(self, user_id, provider):
        with self.connection() as connection:
            connection.execute("DELETE FROM credentials WHERE user_id = ? AND provider = ?", (user_id, self._provider(provider)))

    def get_record(self, user_id, record_id):
        with self.connection() as connection:
            row = connection.execute("SELECT payload FROM records WHERE user_id = ? AND id = ?", (user_id, record_id)).fetchone()
        if not row:
            raise ServiceError("not_found", "记录不存在")
        return json.loads(row["payload"])

    def save_record(self, user_id, record, expected_revision=None):
        self._require_user(user_id)
        if not isinstance(record, dict):
            raise ServiceError("invalid_input", "记录必须为对象")
        saved = json.loads(_json(record))
        record_id = _text(saved.get("id", str(uuid.uuid4())), "记录ID", 200)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT user_id, revision, created_at FROM records WHERE id = ?", (record_id,)).fetchone()
            if row and row["user_id"] != user_id:
                raise ServiceError("not_found", "记录不存在")
            if row:
                expected_revision = _revision(expected_revision)
                if row["revision"] != expected_revision:
                    raise ServiceError("conflict", "记录已更新，请读取最新版本后重试")
                revision, created_at = row["revision"] + 1, row["created_at"]
            else:
                if expected_revision is not None:
                    raise ServiceError("conflict", "记录不存在或版本已变化")
                revision, created_at = 1, _now()
            saved.update(id=record_id, revision=revision, created_at=created_at, updated_at=_now())
            payload = _json(saved)
            if row:
                changed = connection.execute("UPDATE records SET revision = ?, payload = ?, updated_at = ? WHERE id = ? AND user_id = ? AND revision = ?", (revision, payload, saved["updated_at"], record_id, user_id, expected_revision)).rowcount
                if changed != 1:
                    raise ServiceError("conflict", "记录已更新，请读取最新版本后重试")
            else:
                connection.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)", (record_id, user_id, revision, payload, created_at, saved["updated_at"]))
        return saved

    def list_records(self, user_id):
        with self.connection() as connection:
            rows = connection.execute("SELECT payload FROM records WHERE user_id = ? ORDER BY updated_at DESC, id", (user_id,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    @staticmethod
    def _origin_rows(connection, user_id):
        rows = connection.execute("SELECT id, label, address, revision, created_at, updated_at FROM origins WHERE user_id = ? ORDER BY created_at, id", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def origin(self, user_id, action, label=None, address=None, origin_id=None, expected_revision=None):
        self._require_user(user_id)
        if action not in {"list", "add", "update", "delete"}:
            raise ServiceError("invalid_input", "地点操作无效")
        if action == "add" and not address:
            result = self.origin(user_id, "list")
            result["status"] = "needs_input"
            result["mobile_form"]["questions"] = [{"field": "address", "prompt": "这个出发地点的具体地址是什么？"}]
            return result
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if action == "add":
                timestamp = _now()
                connection.execute("INSERT INTO origins VALUES (?, ?, ?, ?, 1, ?, ?)", (str(uuid.uuid4()), user_id, _text(label or "其他地点", "地点名称", 200), _text(address, "地点地址"), timestamp, timestamp))
            elif action in {"update", "delete"}:
                row = connection.execute("SELECT * FROM origins WHERE id = ? AND user_id = ?", (origin_id, user_id)).fetchone()
                if not row:
                    raise ServiceError("not_found", "地点不存在")
                expected_revision = _revision(expected_revision)
                if row["revision"] != expected_revision:
                    raise ServiceError("conflict", "地点已更新，请读取最新版本后重试")
                if action == "delete":
                    connection.execute("DELETE FROM origins WHERE id = ? AND user_id = ? AND revision = ?", (origin_id, user_id, expected_revision))
                else:
                    connection.execute("UPDATE origins SET label = ?, address = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND user_id = ? AND revision = ?", (_text(label, "地点名称", 200) if label is not None else row["label"], _text(address, "地点地址") if address is not None else row["address"], _now(), origin_id, user_id, expected_revision))
            origins = self._origin_rows(connection, user_id)
        return {"status": "ok", "origins": origins, "mobile_form": {"title": "常用出发地点", "items": origins, "questions": [], "actions": ["add", "update", "delete"]}}
