"""
Hermes-Mythos State Manager — SQLite with connection pooling.

Uses a single persistent connection with WAL mode for concurrent reads,
plus a context-manager-based connection pool for thread safety.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Thread-safe SQLite connection pool using WAL mode.

    Maintains a pool of connections that are checked out and returned.
    All connections use WAL journal mode for better concurrent read performance.
    """

    def __init__(self, db_path: str, pool_size: int = 5):
        self._db_path = db_path
        self._pool_size = pool_size
        self._pool: List[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._in_use: set = set()

        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Pre-allocate connections
        for _ in range(pool_size):
            conn = self._create_connection()
            self._pool.append(conn)

        logger.info("Connection pool initialized with %d connections to %s", pool_size, db_path)

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection with optimal settings."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextlib.contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager to check out and return a connection."""
        conn = None
        with self._lock:
            if self._pool:
                conn = self._pool.pop()
                self._in_use.add(id(conn))
        if conn is None:
            # Pool exhausted, create a temporary connection
            conn = self._create_connection()
            logger.debug("Pool exhausted, created temporary connection")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            with self._lock:
                if id(conn) in self._in_use:
                    self._in_use.discard(id(conn))
                    if len(self._pool) < self._pool_size:
                        self._pool.append(conn)
                    else:
                        conn.close()

    def close_all(self) -> None:
        """Close all pooled connections."""
        with self._lock:
            for conn in self._pool:
                conn.close()
            self._pool.clear()
            self._in_use.clear()
        logger.info("All connections closed")


class StateManager:
    """Persistent pipeline state backed by SQLite with connection pooling.

    Tracks manuscripts, pipeline runs, layer completions, and narrative
    fragments for resume support.
    """

    def __init__(self, db_path: str = "data/hermes.db"):
        self._pool = ConnectionPool(db_path)
        self._init_schema()

    # -------------------------------------------------------------------
    # Schema
    # -------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with self._pool.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS manuscripts (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    genre       TEXT NOT NULL DEFAULT 'fiction',
                    premise     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    metadata    TEXT DEFAULT '{}',
                    status      TEXT NOT NULL DEFAULT 'draft'
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id              TEXT PRIMARY KEY,
                    manuscript_id   TEXT NOT NULL REFERENCES manuscripts(id),
                    status          TEXT NOT NULL DEFAULT 'running',
                    current_layer   TEXT,
                    started_at      TEXT NOT NULL,
                    completed_at    TEXT,
                    error           TEXT,
                    layer_states    TEXT DEFAULT '{}',
                    FOREIGN KEY (manuscript_id) REFERENCES manuscripts(id)
                );

                CREATE TABLE IF NOT EXISTS layer_completions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL REFERENCES pipeline_runs(id),
                    layer_name      TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'completed',
                    output          TEXT,
                    started_at      TEXT NOT NULL,
                    completed_at    TEXT,
                    duration_secs   REAL,
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
                );

                CREATE TABLE IF NOT EXISTS narrative_fragments (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    manuscript_id   TEXT NOT NULL,
                    run_id          TEXT NOT NULL,
                    chapter_num     INTEGER NOT NULL,
                    title           TEXT,
                    content         TEXT NOT NULL,
                    word_count      INTEGER NOT NULL DEFAULT 0,
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (manuscript_id) REFERENCES manuscripts(id),
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_manuscript
                    ON pipeline_runs(manuscript_id);
                CREATE INDEX IF NOT EXISTS idx_fragments_manuscript
                    ON narrative_fragments(manuscript_id);
                CREATE INDEX IF NOT EXISTS idx_completions_run
                    ON layer_completions(run_id);
            """)
            conn.commit()
        logger.info("Database schema initialized")

    # -------------------------------------------------------------------
    # Manuscripts
    # -------------------------------------------------------------------

    def create_manuscript(
        self, ms_id: str, title: str, genre: str, premise: str
    ) -> Dict[str, Any]:
        """Create a new manuscript record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO manuscripts (id, title, genre, premise, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ms_id, title, genre, premise, now, now),
            )
            conn.commit()
        return {"id": ms_id, "title": title, "genre": genre, "premise": premise}

    def get_manuscript(self, ms_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a manuscript by ID."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM manuscripts WHERE id = ?", (ms_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_manuscripts(self) -> List[Dict[str, Any]]:
        """List all manuscripts, newest first."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM manuscripts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_manuscript_status(self, ms_id: str, status: str) -> None:
        """Update manuscript status."""
        now = datetime.now(timezone.utc).isoformat()
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE manuscripts SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, ms_id),
            )
            conn.commit()

    # -------------------------------------------------------------------
    # Pipeline runs
    # -------------------------------------------------------------------

    def create_run(self, run_id: str, manuscript_id: str) -> Dict[str, Any]:
        """Start a new pipeline run."""
        now = datetime.now(timezone.utc).isoformat()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, manuscript_id, started_at) "
                "VALUES (?, ?, ?)",
                (run_id, manuscript_id, now),
            )
            conn.commit()
        return {"id": run_id, "manuscript_id": manuscript_id, "status": "running"}

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a pipeline run by ID."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["layer_states"] = json.loads(result.get("layer_states") or "{}")
        return result

    def get_latest_run(self, manuscript_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the most recent run for a manuscript."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE manuscript_id = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (manuscript_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["layer_states"] = json.loads(result.get("layer_states") or "{}")
        return result

    def update_run(
        self,
        run_id: str,
        status: Optional[str] = None,
        current_layer: Optional[str] = None,
        layer_states: Optional[Dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update pipeline run fields."""
        parts = []
        params = []
        if status is not None:
            parts.append("status = ?")
            params.append(status)
        if current_layer is not None:
            parts.append("current_layer = ?")
            params.append(current_layer)
        if layer_states is not None:
            parts.append("layer_states = ?")
            params.append(json.dumps(layer_states))
        if error is not None:
            parts.append("error = ?")
            params.append(error)
        if status in ("completed", "failed"):
            parts.append("completed_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())

        if parts:
            params.append(run_id)
            with self._pool.connection() as conn:
                conn.execute(
                    f"UPDATE pipeline_runs SET {', '.join(parts)} WHERE id = ?",
                    params,
                )
                conn.commit()

    # -------------------------------------------------------------------
    # Layer completions
    # -------------------------------------------------------------------

    def record_layer_completion(
        self,
        run_id: str,
        layer_name: str,
        output: str,
        started_at: str,
        duration_secs: float,
        status: str = "completed",
    ) -> None:
        """Record that a layer finished execution."""
        now = datetime.now(timezone.utc).isoformat()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO layer_completions "
                "(run_id, layer_name, status, output, started_at, completed_at, duration_secs) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, layer_name, status, output, started_at, now, duration_secs),
            )
            conn.commit()

    def get_layer_completions(self, run_id: str) -> List[Dict[str, Any]]:
        """Get all layer completions for a run."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM layer_completions WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Narrative fragments (chapter output)
    # -------------------------------------------------------------------

    def save_fragment(
        self,
        manuscript_id: str,
        run_id: str,
        chapter_num: int,
        title: str,
        content: str,
    ) -> None:
        """Save a completed chapter fragment."""
        now = datetime.now(timezone.utc).isoformat()
        word_count = len(content.split())
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO narrative_fragments "
                "(manuscript_id, run_id, chapter_num, title, content, word_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (manuscript_id, run_id, chapter_num, title, content, word_count, now),
            )
            conn.commit()

    def get_fragments(self, manuscript_id: str) -> List[Dict[str, Any]]:
        """Get all narrative fragments for a manuscript, ordered by chapter."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM narrative_fragments "
                "WHERE manuscript_id = ? ORDER BY chapter_num",
                (manuscript_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_fragment_count(self, manuscript_id: str) -> int:
        """Count completed fragments for a manuscript."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM narrative_fragments "
                "WHERE manuscript_id = ?",
                (manuscript_id,),
            ).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------

    def close(self) -> None:
        """Close all connections in the pool."""
        self._pool.close_all()
