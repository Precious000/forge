import sqlite3
import os
import json
from pathlib import Path
from datetime import datetime

DB_PATH = os.environ.get("REGISTRY_DB_PATH", "/data/db/registry.db")

def _get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            publisher TEXT NOT NULL,
            deps TEXT DEFAULT '[]',
            published_at TEXT DEFAULT (datetime('now')),
            UNIQUE(name, version)
        )
    """)
    conn.commit()
    return conn

def insert_artifact(name, version, sha256, size, publisher, deps=None):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO artifacts (name, version, sha256, size, publisher, deps) VALUES (?,?,?,?,?,?)",
            (name, version, sha256, size, publisher, json.dumps(deps or []))
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"CONFLICT: {name}@{version} already exists")
    finally:
        conn.close()

def get_artifact(name, version):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT name, version, sha256, size, publisher, deps, published_at FROM artifacts WHERE name=? AND version=?",
            (name, version)
        ).fetchone()
        if not row:
            return None
        return {
            "name": row[0], "version": row[1], "sha256": row[2],
            "size": row[3], "publisher": row[4],
            "deps": json.loads(row[5]), "published_at": row[6]
        }
    finally:
        conn.close()

def list_versions(name):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT version, sha256, size, published_at FROM artifacts WHERE name=? ORDER BY published_at DESC",
            (name,)
        ).fetchall()
        return [{"version": r[0], "sha256": r[1], "size": r[2], "published_at": r[3]} for r in rows]
    finally:
        conn.close()

def get_all_versions(name):
    return [v["version"] for v in list_versions(name)]
