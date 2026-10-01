"""Projets et conversations persistants, avec opérations protégées par SQLite."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class WorkspaceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code


def _now():
    return datetime.now(UTC).isoformat(timespec="microseconds")


class WorkspaceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    deleting INTEGER NOT NULL DEFAULT 0, owner TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
                CREATE INDEX IF NOT EXISTS conversations_project ON conversations(project_id);
                CREATE TABLE IF NOT EXISTS operations (
                    token TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    conversation_id TEXT UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
                    pid INTEGER NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
            for column, definition in (("updated_at", "TEXT NOT NULL DEFAULT ''"), ("deleting", "INTEGER NOT NULL DEFAULT 0"), ("owner", "TEXT NOT NULL DEFAULT ''")):
                if column not in columns:
                    db.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")
        with self._transaction() as db:
            if not db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
                now = _now()
                db.execute("INSERT INTO projects (id, name, created_at, updated_at, owner) VALUES (?, ?, ?, ?, '*')", ("general", "Général", now, now))
                db.execute("INSERT INTO meta VALUES ('initialized', '1')")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _project(db, project_id, allow_deleting=False):
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise WorkspaceError("Ce projet n’existe plus.")
        if row["deleting"] and not allow_deleting:
            raise WorkspaceError("La suppression de ce projet doit être terminée. Réessayez sa suppression.", 409)
        return dict(row)

    @staticmethod
    def _conversation(db, project_id, conversation_id):
        row = db.execute("SELECT * FROM conversations WHERE id=? AND project_id=?", (conversation_id, project_id)).fetchone()
        if row is None:
            raise WorkspaceError("Cette conversation n’appartient pas à ce projet.")
        return dict(row)

    @staticmethod
    def _name(name):
        name = name.strip()
        if not name or len(name) > 120:
            raise WorkspaceError("Le nom du projet doit contenir entre 1 et 120 caractères.", 422)
        return name

    @staticmethod
    def _prune_operations(db):
        # Une opération d'un processus arrêté ne doit pas bloquer le projet après redémarrage.
        for row in db.execute("SELECT DISTINCT pid FROM operations").fetchall():
            try:
                os.kill(row["pid"], 0)
            except ProcessLookupError:
                db.execute("DELETE FROM operations WHERE pid=?", (row["pid"],))
            except PermissionError:
                pass

    def list_projects(self, owner=None):
        with self._connect() as db:
            query = "SELECT * FROM projects"
            args = ()
            if owner is not None:
                query += " WHERE owner=? OR owner='*'"
                args = (owner,)
            return [dict(row) for row in db.execute(query + " ORDER BY created_at, id", args)]

    def create_project(self, name, owner=""):
        name = self._name(name)
        now, project_id = _now(), uuid4().hex
        with self._transaction() as db:
            db.execute("INSERT INTO projects (id, name, created_at, updated_at, owner) VALUES (?, ?, ?, ?, ?)", (project_id, name, now, now, owner))
            return self._project(db, project_id)

    def owns(self, owner, project_id):
        with self._connect() as db:
            return db.execute("SELECT 1 FROM projects WHERE id=? AND (owner=? OR owner='*')", (project_id, owner)).fetchone() is not None

    def rename_project(self, project_id, name):
        name = self._name(name)
        with self._transaction() as db:
            self._project(db, project_id)
            db.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (name, _now(), project_id))
            return self._project(db, project_id)

    @contextmanager
    def operation(self, project_id, conversation_id=None):
        token = str(uuid4())
        with self._transaction() as db:
            self._prune_operations(db)
            self._project(db, project_id)
            if conversation_id is not None:
                self._conversation(db, project_id, conversation_id)
                if db.execute("SELECT 1 FROM operations WHERE conversation_id=?", (conversation_id,)).fetchone():
                    raise WorkspaceError("Une réponse est déjà en cours dans cette conversation.", 409)
            db.execute("INSERT INTO operations VALUES (?, ?, ?, ?)", (token, project_id, conversation_id, os.getpid()))
        try:
            yield
        finally:
            with self._transaction() as db:
                db.execute("DELETE FROM operations WHERE token=?", (token,))

    def delete_project(self, project_id, cleanup):
        token = str(uuid4())
        with self._transaction() as db:
            self._prune_operations(db)
            self._project(db, project_id, allow_deleting=True)
            if db.execute("SELECT 1 FROM operations WHERE project_id=?", (project_id,)).fetchone():
                raise WorkspaceError("Ce projet est utilisé par une opération en cours. Réessayez après sa fin.", 409)
            db.execute("UPDATE projects SET deleting=1 WHERE id=?", (project_id,))
            db.execute("INSERT INTO operations VALUES (?, ?, NULL, ?)", (token, project_id, os.getpid()))
        try:
            cleanup(project_id)
            with self._transaction() as db:
                db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        finally:
            with self._transaction() as db:
                db.execute("DELETE FROM operations WHERE token=?", (token,))

    def create_conversation(self, project_id):
        conversation_id, now = str(uuid4()), _now()
        with self._transaction() as db:
            self._project(db, project_id)
            db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?)", (conversation_id, project_id, "Nouvelle discussion", now, now))
            return self._conversation(db, project_id, conversation_id)

    def list_conversations(self, project_id):
        with self._connect() as db:
            self._project(db, project_id)
            return [dict(row) for row in db.execute("SELECT * FROM conversations WHERE project_id=? ORDER BY updated_at DESC, id", (project_id,))]

    def get_conversation(self, project_id, conversation_id):
        with self._connect() as db:
            self._project(db, project_id)
            conversation = self._conversation(db, project_id, conversation_id)
            messages = []
            for row in db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)):
                message = dict(row)
                message["sources"] = json.loads(message["sources"])
                messages.append(message)
            conversation["messages"] = messages
            return conversation

    def start_turn(self, project_id, conversation_id, question):
        with self._transaction() as db:
            self._project(db, project_id)
            conversation = self._conversation(db, project_id, conversation_id)
            rows = db.execute("SELECT role, content FROM messages WHERE conversation_id=? AND status='complete' ORDER BY id DESC LIMIT 12", (conversation_id,)).fetchall()
            history = [dict(row) for row in reversed(rows)]
            turn_id, now = str(uuid4()), _now()
            for role, content in (("user", question), ("assistant", "")):
                db.execute("INSERT INTO messages (turn_id, conversation_id, role, content, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)", (turn_id, conversation_id, role, content, now))
            first_turn = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0] == 2
            title = question[:80] if first_turn else conversation["title"]
            db.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title, now, conversation_id))
            return turn_id, history

    def finish_turn(self, turn_id, content, sources, status, error=None):
        if status not in {"complete", "interrupted", "error"}:
            raise ValueError("Invalid turn status")
        with self._transaction() as db:
            db.execute("UPDATE messages SET status=?, error=? WHERE turn_id=?", (status, error, turn_id))
            db.execute("UPDATE messages SET content=?, sources=? WHERE turn_id=? AND role='assistant'", (content, json.dumps(sources, ensure_ascii=False), turn_id))

    def save_progress(self, turn_id, content, sources):
        with self._transaction() as db:
            db.execute("UPDATE messages SET content=?, sources=? WHERE turn_id=? AND role='assistant'", (content, json.dumps(sources, ensure_ascii=False), turn_id))
