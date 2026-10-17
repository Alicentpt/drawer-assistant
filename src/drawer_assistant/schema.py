# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Share bounded table declarations between SQLite and PostgreSQL migrations."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, client TEXT NOT NULL,
 notes TEXT NOT NULL, status TEXT NOT NULL, due TEXT NOT NULL,
 timezone TEXT NOT NULL, remind_at TEXT NOT NULL,
 sent_at TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS refs (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
 order_id TEXT NOT NULL, notes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, prompt TEXT NOT NULL, workflow TEXT NOT NULL,
 prompt_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'submitting',
 file TEXT NOT NULL DEFAULT '', generation_seconds TEXT NOT NULL DEFAULT ''
);
PRAGMA user_version = 2;
"""

ALIBABA_SCHEMA = """
CREATE TABLE IF NOT EXISTS alibaba_jobs (
 id TEXT PRIMARY KEY, model TEXT NOT NULL, base TEXT NOT NULL,
 fingerprint TEXT NOT NULL, request TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'submitting', file TEXT NOT NULL DEFAULT '',
 generation_seconds TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT ''
)
"""

ASSETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
 sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL
)
"""

REFERENCE_FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS reference_files (
 reference_id TEXT PRIMARY KEY REFERENCES refs(id),
 sha256 TEXT NOT NULL REFERENCES assets(sha256), filename TEXT NOT NULL
)
"""

BATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_batches (
 id TEXT PRIMARY KEY, spec TEXT NOT NULL, file TEXT NOT NULL DEFAULT '',
 assembly_seconds REAL NOT NULL DEFAULT 0
)
"""

GENERATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS generation_tasks (
 id TEXT PRIMARY KEY, spec TEXT NOT NULL, base TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'submitting', prompt_id TEXT NOT NULL DEFAULT '',
 result TEXT NOT NULL DEFAULT '{}'
)
"""

STAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_stages (
 order_id TEXT NOT NULL REFERENCES orders(id), id TEXT NOT NULL,
 parent_id TEXT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 comments TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
 due TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0,
 completed_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(order_id,id),
 FOREIGN KEY(order_id,parent_id) REFERENCES order_stages(order_id,id)
);
CREATE TABLE IF NOT EXISTS stage_images (
 order_id TEXT NOT NULL, stage_id TEXT NOT NULL,
 reference_id TEXT NOT NULL REFERENCES refs(id),
 PRIMARY KEY(order_id,stage_id,reference_id),
 FOREIGN KEY(order_id,stage_id) REFERENCES order_stages(order_id,id)
)
"""
