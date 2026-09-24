"""SQLite memory. The session table is the raw log and is never compacted away."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Memory:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "memory.sqlite"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    body TEXT NOT NULL,
                    evidence TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    mentions INTEGER NOT NULL DEFAULT 0,
                    last_ts TEXT NOT NULL,
                    emotion TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL DEFAULT '',
                    recurring INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS research (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    schedule TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS digests (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_message(self, session_id: str, role: str, content: str, raw: dict | None = None) -> None:
        payload = raw if raw is not None else {"role": role, "content": content}
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (session_id, ts, role, content, raw_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, utc_now(), role, content, json.dumps(payload)),
            )
            self._conn.commit()

    def list_sessions(self, limit: int = 12) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute(
                    """
                    SELECT session_id, MAX(ts) AS ts, COUNT(*) AS messages
                    FROM sessions
                    GROUP BY session_id
                    ORDER BY MAX(id) DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def search_messages(self, words: list[str], limit: int = 5) -> list[sqlite3.Row]:
        cleaned = [word for word in words if len(word) > 2][:6]
        if not cleaned:
            return []
        clause = " OR ".join("content LIKE ?" for _ in cleaned)
        params = [f"%{word}%" for word in cleaned]
        params.append(limit)
        with self._lock:
            return list(
                self._conn.execute(
                    f"SELECT session_id, ts, role, content FROM sessions WHERE {clause} ORDER BY id DESC LIMIT ?",
                    params,
                )
            )

    def load_transcript(self, session_id: str, limit: int = 40) -> list[dict]:
        rows = self.session_rows(session_id)
        loaded: list[dict] = []
        for row in rows[-limit:]:
            try:
                raw = json.loads(row["raw_json"])
            except json.JSONDecodeError:
                raw = {"role": row["role"], "content": row["content"]}
            if isinstance(raw, dict) and raw.get("role"):
                loaded.append(raw)
        return loaded

    def session_rows(self, session_id: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if session_id is None:
                return list(self._conn.execute("SELECT * FROM sessions ORDER BY id"))
            return list(
                self._conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ? ORDER BY id",
                    (session_id,),
                )
            )

    def add_note(self, session_id: str, topic: str, body: str, evidence: str) -> int | None:
        if not evidence.strip():
            return None
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO notes (ts, session_id, topic, body, evidence) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), session_id, topic, body, evidence),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def recent_notes(self, limit: int = 8) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)))

    def search_notes(self, words: list[str], limit: int = 5) -> list[sqlite3.Row]:
        cleaned = [word for word in words if len(word) > 3][:6]
        with self._lock:
            if not cleaned:
                return list(self._conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)))
            clauses = []
            params: list[object] = []
            for word in cleaned:
                clauses.append("(topic LIKE ? OR body LIKE ? OR evidence LIKE ?)")
                like = f"%{word}%"
                params.extend((like, like, like))
            params.append(limit)
            sql = f"SELECT * FROM notes WHERE {' OR '.join(clauses)} ORDER BY id DESC LIMIT ?"
            return list(self._conn.execute(sql, params))

    def bump_topic(self, name: str, emotion: str, action: str, snippet: str, ts: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM topics WHERE name = ?", (name,)).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO topics (name, mentions, last_ts, emotion, action, snippet, recurring)
                    VALUES (?, 1, ?, ?, ?, ?, 0)
                    """,
                    (name, ts, emotion, action, snippet),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE topics
                    SET mentions = mentions + 1, last_ts = ?, emotion = ?, action = ?, snippet = ?
                    WHERE name = ?
                    """,
                    (ts, emotion or row["emotion"], action or row["action"], snippet, name),
                )
            self._conn.commit()

    def flag_recurring(self, now: datetime, min_mentions: int = 3, within_days: int = 14) -> None:
        with self._lock:
            rows = list(self._conn.execute("SELECT * FROM topics"))
            for row in rows:
                last = datetime.fromisoformat(row["last_ts"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                recent = (now - last).days <= within_days
                if row["mentions"] >= min_mentions and (row["emotion"] or row["action"]) and recent:
                    self._conn.execute("UPDATE topics SET recurring = 1 WHERE id = ?", (row["id"],))
            self._conn.commit()

    def recurring_topics(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute("SELECT * FROM topics WHERE recurring = 1 ORDER BY mentions DESC"))

    def add_research(self, topic: str, source: str, confidence: str, body: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO research (ts, topic, source, confidence, body) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), topic, source, confidence, body),
            )
            self._conn.commit()

    def recent_research(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute("SELECT * FROM research ORDER BY id DESC LIMIT ?", (limit,)))

    def add_job(self, name: str, schedule: str, prompt: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (name, schedule, prompt, enabled, last_run)
                VALUES (?, ?, ?, 1, '')
                ON CONFLICT(name) DO UPDATE SET schedule = excluded.schedule, prompt = excluded.prompt, enabled = 1
                """,
                (name, schedule, prompt),
            )
            self._conn.commit()

    def list_jobs(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute("SELECT * FROM jobs ORDER BY name"))

    def mark_job(self, name: str, ts: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE jobs SET last_run = ? WHERE name = ?", (ts, name))
            self._conn.commit()

    def add_digest(self, body: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO digests (ts, body) VALUES (?, ?)", (utc_now(), body))
            self._conn.commit()

    def latest_digest(self) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM digests ORDER BY id DESC LIMIT 1").fetchone()

    def export_markdown(self, out: Path, skills_root: Path) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        for folder in ("sessions", "notes", "skills", "research"):
            (out / folder).mkdir(parents=True, exist_ok=True)
        rows = self.session_rows()
        buckets: dict[str, list] = {}
        for row in rows:
            buckets.setdefault(row["session_id"], []).append(row)
        index = ["# sessions", ""]
        for session_id, items in buckets.items():
            lines = [f"# session {session_id}", ""]
            for item in items:
                lines.append(f"## {item['role']} {item['ts']}")
                lines.append("")
                lines.append(item["content"])
                lines.append("")
            (out / "sessions" / f"{session_id}.md").write_text("\n".join(lines), encoding="utf-8")
            index.append(f"- [{session_id}]({session_id}.md)")
        if len(index) == 2:
            index.append("No sessions yet.")
        (out / "sessions" / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")

        notes = list(reversed(self.recent_notes(limit=500)))
        note_lines = ["# notes", ""]
        if not notes:
            note_lines.append("No verified notes yet.")
        for note in notes:
            note_lines.extend(
                [
                    f"## {note['topic']}",
                    "",
                    note["body"],
                    "",
                    "Evidence:",
                    "",
                    note["evidence"],
                    "",
                ]
            )
        (out / "notes" / "index.md").write_text("\n".join(note_lines), encoding="utf-8")

        research = list(reversed(self.recent_research(limit=500)))
        research_lines = ["# research", ""]
        if not research:
            research_lines.append("No research yet.")
        for item in research:
            research_lines.extend(
                [
                    f"## {item['topic']}",
                    "",
                    f"- source: {item['source']}",
                    f"- confidence: {item['confidence']}",
                    f"- date: {item['ts']}",
                    "",
                    item["body"],
                    "",
                ]
            )
        (out / "research" / "index.md").write_text("\n".join(research_lines), encoding="utf-8")

        if skills_root.exists():
            for path in skills_root.rglob("*.md"):
                relative = path.relative_to(skills_root)
                dest = out / "skills" / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        if not any((out / "skills").glob("*.md")) and not any((out / "skills").rglob("*.md")):
            (out / "skills" / "index.md").write_text("# skills\n\nNo skills yet.\n", encoding="utf-8")
        return out
