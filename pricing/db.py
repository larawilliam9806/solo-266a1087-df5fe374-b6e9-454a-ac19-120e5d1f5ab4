"""SQLite storage for uploaded boxes, ruling lines and human decisions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path, timeout=15.0):
    connection = sqlite3.connect(path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL + a busy timeout keep two gunicorn workers from tripping over each
    # other's short writes.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def init_db(path):
    connection = connect(path)
    try:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(connection)
        connection.commit()
    finally:
        connection.close()


def _migrate(connection):
    """Bring an older database file up to the current schema."""
    columns = {row["name"] for row in
               connection.execute("PRAGMA table_info(jobs)").fetchall()}
    if "revision" not in columns:
        connection.execute(
            "ALTER TABLE jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
        )


def _rows_to_dicts(rows):
    return [dict(row) for row in rows]


def get_job(connection, job_id):
    row = connection.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def list_jobs(connection):
    rows = connection.execute(
        "SELECT j.*, (SELECT COUNT(*) FROM boxes b WHERE b.job_id = j.id) AS box_count,"
        " (SELECT COUNT(*) FROM lines l WHERE l.job_id = j.id) AS line_count"
        " FROM jobs j ORDER BY j.id DESC"
    ).fetchall()
    return _rows_to_dicts(rows)


def create_job(connection, filename):
    cursor = connection.execute(
        "INSERT INTO jobs (filename) VALUES (?)", (filename,)
    )
    connection.commit()
    return cursor.lastrowid


def delete_job(connection, job_id):
    connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    connection.commit()


def replace_box(connection, job_id, row):
    """Insert one OCR box; UNIQUE(job_id,row_no) makes re-upload idempotent."""
    connection.execute(
        "INSERT OR REPLACE INTO boxes"
        " (job_id, row_no, box_ref, text, x1, y1, x2, y2)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, row["row_no"], row["box_ref"], row["text"],
         row["x1"], row["y1"], row["x2"], row["y2"]),
    )


def add_boxes(connection, job_id, rows):
    for row in rows:
        replace_box(connection, job_id, row)
    connection.commit()


def get_boxes(connection, job_id):
    rows = connection.execute(
        "SELECT * FROM boxes WHERE job_id = ? ORDER BY row_no", (job_id,)
    ).fetchall()
    return _rows_to_dicts(rows)


def get_lines(connection, job_id):
    rows = connection.execute(
        "SELECT orientation, position FROM lines WHERE job_id = ?"
        " ORDER BY orientation, position", (job_id,)
    ).fetchall()
    horizontal = [row["position"] for row in rows if row["orientation"] == "h"]
    vertical = [row["position"] for row in rows if row["orientation"] == "v"]
    return horizontal, vertical


def get_decisions(connection, job_id):
    rows = connection.execute(
        "SELECT * FROM decisions WHERE job_id = ?", (job_id,)
    ).fetchall()
    decisions = {}
    for row in rows:
        record = dict(row)
        try:
            record["overlap_cells"] = json.loads(record["overlap_cells"] or "[]")
        except ValueError:
            record["overlap_cells"] = []
        decisions[record["box_id"]] = record
    return decisions


def set_decision(connection, job_id, box_id, kind, target_cell, overlap_cells):
    connection.execute(
        "INSERT INTO decisions"
        " (job_id, box_id, kind, target_cell, overlap_cells)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (job_id, box_id) DO UPDATE SET"
        " kind = excluded.kind, target_cell = excluded.target_cell,"
        " overlap_cells = excluded.overlap_cells,"
        " created_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')",
        (job_id, box_id, kind, target_cell,
         json.dumps(sorted(overlap_cells), ensure_ascii=False)),
    )
    connection.commit()


def clear_decision(connection, job_id, box_id):
    connection.execute(
        "DELETE FROM decisions WHERE job_id = ? AND box_id = ?", (job_id, box_id)
    )
    connection.commit()


# ---------------------------------------------------------------------------
# Revision: one number that advances with every edit that can change rows or
# groups.  An expansion download records it up front and the CSV is built from
# that same snapshot, so interleaved edits can never mix two versions.
# ---------------------------------------------------------------------------

def bump_revision(connection, job_id):
    """Advance and return the job's revision (caller commits)."""
    connection.execute(
        "UPDATE jobs SET revision = revision + 1 WHERE id = ?", (job_id,)
    )
    row = connection.execute(
        "SELECT revision FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return row["revision"] if row else 0


def get_revision(connection, job_id):
    row = connection.execute(
        "SELECT revision FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return row["revision"] if row else 0


def apply_layout(connection, job_id, horizontal, vertical):
    """Replace the lines and carry group marks forward, in one transaction.

    A mark survives an edit only while its exact y coordinate stays an inner
    horizontal line.  Otherwise it is flagged invalid and stays flagged: even
    if a later edit restores the same coordinate, the clerk has to confirm or
    undo the group line explicitly.
    """
    connection.execute("DELETE FROM lines WHERE job_id = ?", (job_id,))
    for position in horizontal:
        connection.execute(
            "INSERT INTO lines (job_id, orientation, position) VALUES (?, 'h', ?)",
            (job_id, position),
        )
    for position in vertical:
        connection.execute(
            "INSERT INTO lines (job_id, orientation, position) VALUES (?, 'v', ?)",
            (job_id, position),
        )
    inner = {round(float(value), 2) for value in horizontal[1:-1]}
    marks = get_group_lines(connection, job_id)
    for mark in marks:
        if mark.get("invalid"):
            continue
        if round(float(mark["position"]), 2) not in inner:
            mark["invalid"] = 1
    replace_group_lines(connection, job_id, marks)
    revision = bump_revision(connection, job_id)
    connection.commit()
    return revision


def get_group_lines(connection, job_id):
    rows = connection.execute(
        "SELECT * FROM group_lines WHERE job_id = ? ORDER BY position", (job_id,)
    ).fetchall()
    return _rows_to_dicts(rows)


def replace_group_lines(connection, job_id, marks):
    """Replace every group mark for the job (caller commits).

    marks is an iterable of {cell_key, position, group_no, invalid}.
    """
    connection.execute("DELETE FROM group_lines WHERE job_id = ?", (job_id,))
    ordered = sorted(marks, key=lambda mark: (mark["position"], mark["cell_key"]))
    for group_no, mark in enumerate(ordered, start=1):
        connection.execute(
            "INSERT INTO group_lines (job_id, cell_key, position, group_no, invalid)"
            " VALUES (?, ?, ?, ?, ?)",
            (job_id, mark["cell_key"], mark["position"], group_no,
             1 if mark.get("invalid") else 0),
        )


def clear_group_lines(connection, job_id):
    connection.execute("DELETE FROM group_lines WHERE job_id = ?", (job_id,))
    connection.commit()
