from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from .config import DB_PATH, LEGACY_DB_PATH, LOGGER

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS courses (id TEXT PRIMARY KEY, name TEXT NOT NULL, code TEXT, semester TEXT, academic_year TEXT, description TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lectures (id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id), title TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('lecture','exercise')), lecture_date TEXT, number INTEGER, status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), kind TEXT NOT NULL, original_name TEXT NOT NULL, stored_path TEXT NOT NULL, mime_type TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transcript_segments (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), material_id TEXT NOT NULL REFERENCES materials(id), segment_number INTEGER NOT NULL, start_seconds REAL, end_seconds REAL, text_content TEXT NOT NULL, source_locator TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(material_id, segment_number));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), stage TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outputs (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), kind TEXT NOT NULL CHECK(kind IN ('notebook','exam_focus')), content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS course_outputs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id), kind TEXT NOT NULL CHECK(kind IN ('course_notebook','course_exam_focus')), content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(course_id, kind));
CREATE TABLE IF NOT EXISTS lecture_knowledge (lecture_id TEXT PRIMARY KEY REFERENCES lectures(id), content_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS slides (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), slide_number INTEGER NOT NULL, title TEXT, text_content TEXT, image_path TEXT, metadata_json TEXT, UNIQUE(lecture_id, slide_number));
CREATE TABLE IF NOT EXISTS visual_elements (id TEXT PRIMARY KEY, slide_id TEXT NOT NULL REFERENCES slides(id), kind TEXT, description TEXT, source_reference TEXT, placement_hint TEXT);
CREATE TABLE IF NOT EXISTS alignments (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), segment_id TEXT, source_reference TEXT, slide_numbers_json TEXT, topic TEXT, confidence REAL, relationship TEXT);
CREATE TABLE IF NOT EXISTS source_references (id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), claim TEXT, source_type TEXT, source_locator TEXT, certainty TEXT);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notebook_manual_edits (lecture_id TEXT PRIMARY KEY REFERENCES lectures(id), base_content TEXT NOT NULL, html_content TEXT NOT NULL, pending_content TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exam_manual_edits (lecture_id TEXT PRIMARY KEY REFERENCES lectures(id), base_content TEXT NOT NULL, html_content TEXT NOT NULL, pending_content TEXT, updated_at TEXT NOT NULL);
'''

@contextmanager
def connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally: con.close()

def init_db():
    with connection() as con:
        con.executescript(SCHEMA)
        # Lightweight, idempotent schema version marker for future migrations.
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        con.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, datetime('now'))")
        con.execute("INSERT OR IGNORE INTO app_settings VALUES ('auto_reprocess_on_upload', 'true', datetime('now'))")
        _migrate_legacy_data(con)

def _migrate_legacy_data(destination):
    """Copy existing data once from a legacy database that may be read-only.

    The legacy file is never modified or removed. This is intentionally a copy,
    so a failed migration cannot destroy a student's existing records.
    """
    if not LEGACY_DB_PATH.exists() or LEGACY_DB_PATH == DB_PATH:
        return
    if destination.execute("SELECT 1 FROM courses LIMIT 1").fetchone():
        return
    source = sqlite3.connect(f"file:{LEGACY_DB_PATH.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    tables = ("courses", "lectures", "materials", "jobs", "outputs", "course_outputs", "lecture_knowledge", "slides", "visual_elements", "alignments", "source_references")
    try:
        available = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in available:
                continue
            columns = [row[1] for row in destination.execute(f"PRAGMA table_info({table})")]
            rows_to_copy = source.execute(f"SELECT * FROM {table}").fetchall()
            if not rows_to_copy:
                continue
            placeholders = ",".join("?" for _ in columns)
            destination.executemany(
                f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                [tuple(row[column] if column in row.keys() else None for column in columns) for row in rows_to_copy],
            )
        destination.execute("INSERT OR IGNORE INTO schema_migrations VALUES (2, datetime('now'))")
        LOGGER.info("Migrated legacy data from %s into %s", LEGACY_DB_PATH.name, DB_PATH.name)
    finally:
        source.close()

def rows(sql, values=()):
    with connection() as con: return [dict(r) for r in con.execute(sql, values).fetchall()]

def row(sql, values=()):
    results = rows(sql, values)
    return results[0] if results else None
