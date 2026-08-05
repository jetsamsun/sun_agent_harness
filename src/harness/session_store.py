"""Redis-backed session persistence (optional).

Enabled only when SUN_REDIS_URL is set. Connection failure → hard error.
Stores working state (live Context messages + todos/plan) and a full
append-only transcript for audit / future deep-resume.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from redis import Redis
from redis.exceptions import RedisError


class SessionStoreError(RuntimeError):
    """Raised when Redis is required but unavailable or misused."""


def new_session_id(now: datetime | None = None) -> str:
    """Short id: YYYYMMDD-xxxx (4 hex chars)."""
    dt = now or datetime.now(UTC).astimezone()
    return f"{dt.strftime('%Y%m%d')}-{secrets.token_hex(2)}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SessionMeta:
    id: str
    cwd: str
    title: str
    created_at: str
    updated_at: str
    model: str
    user_turns: int
    status: str  # active | done

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "user_turns": self.user_turns,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMeta:
        return cls(
            id=str(data["id"]),
            cwd=str(data.get("cwd") or ""),
            title=str(data.get("title") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            model=str(data.get("model") or ""),
            user_turns=int(data.get("user_turns") or 0),
            status=str(data.get("status") or "active"),
        )


class SessionStore:
    """Plain JSON blobs in Redis. No TTL — prune explicitly."""

    def __init__(self, client: Redis, *, prefix: str = "sun") -> None:
        self._r = client
        self._prefix = prefix.strip() or "sun"

    @classmethod
    def connect(cls, url: str, *, prefix: str = "sun") -> SessionStore:
        """Connect and ping. Raises SessionStoreError on failure."""
        url = (url or "").strip()
        if not url:
            raise SessionStoreError("SUN_REDIS_URL is empty")
        try:
            # protocol=2 (RESP2): skip HELLO handshake so older Redis (<6) works.
            client: Redis = Redis.from_url(
                url, decode_responses=True, protocol=2
            )
            if client.ping() is not True:
                raise SessionStoreError(f"Redis ping failed: {url}")
        except RedisError as exc:
            raise SessionStoreError(f"Cannot connect to Redis ({url}): {exc}") from exc
        return cls(client, prefix=prefix)

    def _meta_key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}:meta"

    def _state_key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}:state"

    def _transcript_key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}:transcript"

    def _index_key(self) -> str:
        return f"{self._prefix}:index"

    def save(
        self,
        *,
        session_id: str,
        cwd: str,
        title: str,
        model: str,
        user_turns: int,
        status: str,
        messages: list[dict[str, Any]],
        todos: list[dict[str, Any]],
        plan: dict[str, Any] | None,
        transcript: list[dict[str, Any]],
        created_at: str | None = None,
    ) -> SessionMeta:
        now = _utcnow_iso()
        existing = self.get_meta(session_id)
        meta = SessionMeta(
            id=session_id,
            cwd=str(Path(cwd).resolve()),
            title=(title or (existing.title if existing else "") or session_id)[:80],
            created_at=created_at or (existing.created_at if existing else now),
            updated_at=now,
            model=model,
            user_turns=user_turns,
            status=status,
        )
        state = {
            "messages": messages,
            "todos": todos,
            "plan": plan,
        }
        pipe = self._r.pipeline()
        pipe.set(self._meta_key(session_id), json.dumps(meta.as_dict(), ensure_ascii=False))
        pipe.set(self._state_key(session_id), json.dumps(state, ensure_ascii=False))
        pipe.set(
            self._transcript_key(session_id),
            json.dumps(transcript, ensure_ascii=False),
        )
        # score = unix ts for ordering
        try:
            score = datetime.fromisoformat(now).timestamp()
        except ValueError:
            score = datetime.now(UTC).timestamp()
        pipe.zadd(self._index_key(), {session_id: score})
        pipe.execute()
        return meta

    def get_meta(self, session_id: str) -> SessionMeta | None:
        raw = self._r.get(self._meta_key(session_id))
        if not raw:
            return None
        return SessionMeta.from_dict(json.loads(raw))

    def get_state(self, session_id: str) -> dict[str, Any] | None:
        raw = self._r.get(self._state_key(session_id))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None

    def get_transcript(self, session_id: str) -> list[dict[str, Any]]:
        raw = self._r.get(self._transcript_key(session_id))
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []

    def list_sessions(
        self, *, cwd: str | None = None, limit: int = 50
    ) -> list[SessionMeta]:
        ids = self._r.zrevrange(self._index_key(), 0, max(limit * 4, 50) - 1)
        out: list[SessionMeta] = []
        cwd_res = str(Path(cwd).resolve()) if cwd else None
        for sid in ids:
            meta = self.get_meta(str(sid))
            if meta is None:
                continue
            if cwd_res is not None and meta.cwd != cwd_res:
                continue
            out.append(meta)
            if len(out) >= limit:
                break
        return out

    def delete(self, session_id: str) -> bool:
        existed = self._r.exists(self._meta_key(session_id)) > 0
        pipe = self._r.pipeline()
        pipe.delete(self._meta_key(session_id))
        pipe.delete(self._state_key(session_id))
        pipe.delete(self._transcript_key(session_id))
        pipe.zrem(self._index_key(), session_id)
        pipe.execute()
        return bool(existed)

    def prune(self, *, cwd: str | None = None) -> list[str]:
        """Delete sessions (optionally only those bound to cwd). Returns deleted ids."""
        ids = self._r.zrange(self._index_key(), 0, -1)
        deleted: list[str] = []
        cwd_res = str(Path(cwd).resolve()) if cwd else None
        for sid in ids:
            sid_s = str(sid)
            meta = self.get_meta(sid_s)
            if meta is None:
                self.delete(sid_s)
                deleted.append(sid_s)
                continue
            if cwd_res is not None and meta.cwd != cwd_res:
                continue
            self.delete(sid_s)
            deleted.append(sid_s)
        return deleted
