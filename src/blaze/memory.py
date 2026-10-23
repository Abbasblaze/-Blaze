"""Long-term memory: facts about you, a conversation log and an event log.

Everything lives in one SQLite file. The conversation log is what we'll later
export to fine-tune Blaze's model on how you two actually talk.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .paths import TRAINING_DIR

SESSION_GAP_SECONDS = 15 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id      INTEGER PRIMARY KEY,
    text    TEXT NOT NULL UNIQUE,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    session INTEGER NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY,
    ts     REAL NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class Memory:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock, self._db:
            return self._db.execute(sql, args).fetchall()

    # facts -----------------------------------------------------------------

    def add_fact(self, text: str) -> None:
        self._exec("INSERT OR IGNORE INTO facts (text, created) VALUES (?, ?)", (text.strip(), time.time()))

    def remove_fact(self, fact_id: int) -> None:
        self._exec("DELETE FROM facts WHERE id = ?", (fact_id,))

    def forget_matching(self, text: str) -> int:
        rows = self._exec("SELECT id FROM facts WHERE text LIKE ?", (f"%{text.strip()}%",))
        for row in rows:
            self.remove_fact(row["id"])
        return len(rows)

    def facts(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT id, text, created FROM facts ORDER BY created")]

    # conversation log --------------------------------------------------------

    def log_message(self, role: str, content: str) -> None:
        now = time.time()
        last = self._exec("SELECT ts, session FROM messages ORDER BY id DESC LIMIT 1")
        if last and now - last[0]["ts"] < SESSION_GAP_SECONDS:
            session = last[0]["session"]
        else:
            session = (last[0]["session"] + 1) if last else 1
        self._exec(
            "INSERT INTO messages (ts, session, role, content) VALUES (?, ?, ?, ?)",
            (now, session, role, content),
        )

    def message_count(self) -> int:
        return self._exec("SELECT COUNT(*) AS n FROM messages")[0]["n"]

    def export_training(self, system_prompt: str) -> Path:
        """Write every conversation as one JSONL line in chat fine-tuning format."""
        rows = self._exec("SELECT session, role, content FROM messages ORDER BY id")
        sessions: dict[int, list[dict]] = {}
        for r in rows:
            sessions.setdefault(r["session"], []).append({"role": r["role"], "content": r["content"]})

        out = TRAINING_DIR / f"conversations-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        with out.open("w") as f:
            for msgs in sessions.values():
                if any(m["role"] == "assistant" for m in msgs):
                    f.write(json.dumps({"messages": [{"role": "system", "content": system_prompt}, *msgs]}) + "\n")
        return out

    # events ------------------------------------------------------------------

    def log_event(self, kind: str, detail: str = "") -> None:
        self._exec("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (time.time(), kind, detail))
