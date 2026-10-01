"""Projets privés et résumés conservés sur la machine, sans déplacer l'ancien index."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from workspace import WorkspaceError, WorkspaceStore


class ProjectStore(WorkspaceStore):
    def __init__(self, root: Path):
        self.root = root
        super().__init__(root / "projects.sqlite3")

    @contextmanager
    def connection(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "projects.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS summaries (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, detail TEXT NOT NULL,
                    transcript TEXT NOT NULL, sources TEXT NOT NULL, created_at TEXT NOT NULL,
                    llm_model TEXT NOT NULL, tts_model TEXT NOT NULL, collection_name TEXT NOT NULL
                );
            """)
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def list_projects(self, owner: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id, name, created_at, updated_at, deleting FROM projects WHERE owner=? ORDER BY created_at, id",
                    (owner,),
                )
            ]

    def create(self, owner: str, name: str) -> dict:
        return self.create_project(name, owner)

    def owns(self, owner: str, project_id: str) -> bool:
        return super().owns(owner, project_id)

    def general_project(self):
        with self._connect() as db:
            try:
                return self._project(db, "general", allow_deleting=True)
            except WorkspaceError:
                return None

    def delete_project(self, project_id, cleanup):
        def remove_data(project):
            cleanup(project)
            with self.connection() as db:
                rows = db.execute(
                    "SELECT id FROM summaries WHERE project_id=?", (project,)
                ).fetchall()
                for row in rows:
                    (self.root / (row["id"] + ".wav")).unlink(missing_ok=True)
                db.execute("DELETE FROM summaries WHERE project_id=?", (project,))

        return super().delete_project(project_id, remove_data)

    @staticmethod
    def public_summary(row) -> dict:
        value = dict(row)
        value["sources"] = json.loads(value["sources"])
        value["audio_url"] = (
            f"/api/projects/{value['project_id']}/audio/{value['id']}.wav"
        )
        return value

    def summaries(self, project_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                self.public_summary(row)
                for row in db.execute(
                    "SELECT * FROM summaries WHERE project_id=? ORDER BY created_at DESC",
                    (project_id,),
                )
            ]

    def audio_path(self, project_id: str, summary_id: str) -> Path | None:
        with self.connection() as db:
            exists = db.execute(
                "SELECT 1 FROM summaries WHERE id=? AND project_id=?",
                (summary_id, project_id),
            ).fetchone()
        return self.root / (summary_id + ".wav") if exists else None

    def save_summary(
        self, project_id: str, detail: str, result, settings, tts_model: str
    ) -> dict:
        row = {
            "id": uuid4().hex,
            "project_id": project_id,
            "detail": detail,
            "transcript": result.transcript,
            "sources": json.dumps(result.sources, ensure_ascii=False),
            "created_at": datetime.now(UTC).isoformat(),
            "llm_model": settings.llm_model,
            "tts_model": tts_model,
            "collection_name": settings.collection_name,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (row["id"] + ".wav")
        try:
            path.write_bytes(result.audio)
            with self.connection() as db:
                db.execute(
                    "INSERT INTO summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(row.values()),
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.public_summary(row)
