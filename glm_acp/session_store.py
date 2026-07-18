"""Persistent session storage.

Saves and loads session state (message history, model, mode, etc.) to
disk as JSON files so conversations survive agent-process restarts.

When Zed restarts and calls ``load_session`` with a previously-issued
``session_id``, the agent can rebuild the exact same conversation state
instead of starting from scratch.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("glm_acp")

# Directory for persisted sessions.  We use a hidden folder in the user's
# home directory so it is stable across process restarts (unlike /tmp which
# may be cleared) yet still easy to find/inspect.
SESSION_DIR = Path(os.path.expanduser("~/.glm-acp/sessions"))
SESSION_PERSISTENCE_ENV = "GLM_ACP_SESSION_PERSISTENCE"
MAX_INDEX_MESSAGE_CHARS = 32_000
_INDEX_SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.I | re.S
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(
        r"\b(api[_-]?key|token|secret|password|credential)\s*[:=]\s*[^\s]+",
        re.I,
    ),
)


def session_persistence_enabled() -> bool:
    return os.environ.get(SESSION_PERSISTENCE_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Save / load serialized session state to individual JSON files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            from .profiles import active_profile

            profile = active_profile()
            base_dir = (
                SESSION_DIR
                if profile == "default"
                else SESSION_DIR.parent / "profiles" / profile / "sessions"
            )
        self._base_dir = base_dir

    def _path(self, session_id: str) -> Path:
        """Return the on-disk path for a session id.

        Sanitizes the session id to prevent path traversal — only
        alphanumeric, dash, and underscore are allowed in filenames.
        """
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
        return self._base_dir / f"{safe_id}.json"

    def _index_path(self) -> Path:
        return self._base_dir / "session-index.sqlite3"

    def _connect_index(self) -> sqlite3.Connection:
        self._base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self._base_dir, 0o700)
        connection = sqlite3.connect(self._index_path(), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS indexed_sessions (
                session_id TEXT PRIMARY KEY,
                cwd TEXT NOT NULL,
                title TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_id UNINDEXED,
                ordinal UNINDEXED,
                role UNINDEXED,
                content,
                tokenize='unicode61'
            )
            """
        )
        if os.name != "nt":
            os.chmod(self._index_path(), 0o600)
        return connection

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            return ""
        for pattern in _INDEX_SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text[:MAX_INDEX_MESSAGE_CHARS]

    def _index_session(self, session_id: str, data: dict[str, Any]) -> None:
        safe_id = self._path(session_id).stem
        try:
            with self._connect_index() as connection:
                connection.execute(
                    """
                    INSERT INTO indexed_sessions(session_id, cwd, title, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        cwd=excluded.cwd,
                        title=excluded.title,
                        updated_at=excluded.updated_at
                    """,
                    (safe_id, data.get("cwd", ""), data.get("title"), data.get("saved_at")),
                )
                connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (safe_id,))
                for ordinal, message in enumerate(data.get("messages", [])):
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role", ""))
                    if role == "system":
                        continue
                    content = self._message_text(message.get("content")).strip()
                    if not content:
                        continue
                    connection.execute(
                        """
                        INSERT INTO messages_fts(session_id, ordinal, role, content)
                        VALUES (?, ?, ?, ?)
                        """,
                        (safe_id, ordinal, role, content),
                    )
        except (OSError, sqlite3.Error):
            logger.warning("Could not update session search index", exc_info=True)

    def _backfill_search_index(self) -> None:
        """Index legacy JSON sessions once so search covers pre-FTS histories."""
        if not self._base_dir.exists():
            return
        try:
            with self._connect_index() as connection:
                indexed = {
                    str(row["session_id"])
                    for row in connection.execute("SELECT session_id FROM indexed_sessions")
                }
        except (OSError, sqlite3.Error):
            return
        for path in self._base_dir.glob("*.json"):
            if path.stem in indexed:
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                self._index_session(path.stem, data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        """Persist *data* for *session_id* atomically.

        A ``saved_at`` timestamp is injected so ``list_sessions`` can sort
        by recency.
        """
        if not session_persistence_enabled():
            return
        data = {**data, "saved_at": _now_iso()}
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self._base_dir, 0o700)
            path = self._path(session_id)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            # Atomic rename so a crash mid-write never leaves a corrupt file.
            os.replace(tmp, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
            metadata = {
                "session_id": path.stem,
                "cwd": data.get("cwd", ""),
                "title": data.get("title"),
                "updated_at": data.get("saved_at"),
                "parent_session_id": data.get("parent_session_id"),
                "branch_root_id": data.get("branch_root_id") or path.stem,
            }
            meta_path = path.with_suffix(".meta")
            meta_tmp = meta_path.with_suffix(".meta.tmp")
            with open(meta_tmp, "w", encoding="utf-8") as fh:
                json.dump(metadata, fh, ensure_ascii=False)
            os.replace(meta_tmp, meta_path)
            if os.name != "nt":
                os.chmod(meta_path, 0o600)
            self._index_session(session_id, data)
        except OSError:
            logger.warning("Could not persist session %s", session_id, exc_info=True)

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Return the stored data for *session_id* or ``None`` if absent."""
        if not session_persistence_enabled():
            return None
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not load session %s", session_id, exc_info=True)
            return None

    def list(self) -> list[dict[str, Any]]:
        """Return metadata for all persisted sessions, most-recent first."""
        if not session_persistence_enabled():
            return []
        results: list[dict[str, Any]] = []
        if not self._base_dir.exists():
            return results
        indexed_ids: set[str] = set()
        for path in self._base_dir.glob("*.meta"):
            try:
                with open(path, encoding="utf-8") as fh:
                    metadata = json.load(fh)
                results.append(metadata)
                indexed_ids.add(path.stem)
            except (OSError, json.JSONDecodeError):
                continue
        # Backward compatibility for sessions written before metadata sidecars.
        for path in self._base_dir.glob("*.json"):
            if path.stem in indexed_ids:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                session_id = path.stem
                results.append(
                    {
                        "session_id": session_id,
                        "cwd": data.get("cwd", ""),
                        "title": data.get("title"),
                        "updated_at": data.get("saved_at"),
                        "parent_session_id": data.get("parent_session_id"),
                        "branch_root_id": data.get("branch_root_id") or session_id,
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        # Sort by updated_at descending (most recent first)
        results.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return results

    def delete(self, session_id: str) -> None:
        """Remove the stored data for *session_id* (best-effort)."""
        path = self._path(session_id)
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".meta").unlink(missing_ok=True)
            try:
                with self._connect_index() as connection:
                    connection.execute(
                        "DELETE FROM messages_fts WHERE session_id = ?", (path.stem,)
                    )
                    connection.execute(
                        "DELETE FROM indexed_sessions WHERE session_id = ?", (path.stem,)
                    )
            except (OSError, sqlite3.Error):
                logger.warning("Could not remove session search index", exc_info=True)
        except OSError:
            logger.warning("Could not delete session %s", session_id, exc_info=True)

    def search(
        self,
        query: str | None = None,
        *,
        limit: int = 5,
        session_id: str | None = None,
        around_ordinal: int | None = None,
        window: int = 5,
    ) -> list[dict[str, Any]]:
        """Search or browse persisted conversations without indexing reasoning traces."""
        if not session_persistence_enabled():
            return []
        self._backfill_search_index()
        bounded_limit = max(1, min(int(limit), 20))
        bounded_window = max(1, min(int(window), 20))
        try:
            with self._connect_index() as connection:
                if session_id:
                    safe_id = self._path(session_id).stem
                    anchor = max(0, int(around_ordinal or 0))
                    rows = connection.execute(
                        """
                        SELECT session_id, CAST(ordinal AS INTEGER) AS ordinal, role, content
                        FROM messages_fts
                        WHERE session_id = ? AND CAST(ordinal AS INTEGER) BETWEEN ? AND ?
                        ORDER BY CAST(ordinal AS INTEGER)
                        """,
                        (safe_id, max(0, anchor - bounded_window), anchor + bounded_window),
                    ).fetchall()
                    return [dict(row) for row in rows]

                normalized_query = (query or "").strip()
                if not normalized_query:
                    rows = connection.execute(
                        """
                        SELECT session_id, cwd, title, updated_at
                        FROM indexed_sessions
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        (bounded_limit,),
                    ).fetchall()
                    return [dict(row) for row in rows]

                terms = re.findall(r"[\w-]+", normalized_query, flags=re.UNICODE)
                if not terms:
                    return []
                literal_query = " AND ".join(
                    '"' + term.replace('"', '""') + '"' for term in terms[:12]
                )
                hits = connection.execute(
                    """
                    SELECT session_id,
                           CAST(ordinal AS INTEGER) AS ordinal,
                           role,
                           snippet(messages_fts, 3, '[', ']', '…', 24) AS snippet,
                           bm25(messages_fts) AS rank
                    FROM messages_fts
                    WHERE messages_fts MATCH ? AND role IN ('user', 'assistant')
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (literal_query, bounded_limit * 3),
                ).fetchall()
                results: list[dict[str, Any]] = []
                seen: set[str] = set()
                for hit in hits:
                    hit_session_id = str(hit["session_id"])
                    if hit_session_id in seen:
                        continue
                    seen.add(hit_session_id)
                    metadata = connection.execute(
                        """
                        SELECT cwd, title, updated_at FROM indexed_sessions
                        WHERE session_id = ?
                        """,
                        (hit_session_id,),
                    ).fetchone()
                    context = connection.execute(
                        """
                        SELECT CAST(ordinal AS INTEGER) AS ordinal, role, content
                        FROM messages_fts
                        WHERE session_id = ? AND CAST(ordinal AS INTEGER) BETWEEN ? AND ?
                        ORDER BY CAST(ordinal AS INTEGER)
                        """,
                        (
                            hit_session_id,
                            max(0, int(hit["ordinal"]) - bounded_window),
                            int(hit["ordinal"]) + bounded_window,
                        ),
                    ).fetchall()
                    results.append(
                        {
                            "session_id": hit_session_id,
                            "cwd": metadata["cwd"] if metadata else "",
                            "title": metadata["title"] if metadata else None,
                            "updated_at": metadata["updated_at"] if metadata else None,
                            "match_ordinal": int(hit["ordinal"]),
                            "snippet": hit["snippet"],
                            "messages": [dict(row) for row in context],
                        }
                    )
                    if len(results) >= bounded_limit:
                        break
                return results
        except (OSError, sqlite3.Error, ValueError):
            logger.warning("Could not search persisted sessions", exc_info=True)
            return []
