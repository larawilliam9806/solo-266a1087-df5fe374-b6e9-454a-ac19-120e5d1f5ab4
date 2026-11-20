PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS boxes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    row_no      INTEGER NOT NULL,
    box_ref     TEXT    NOT NULL DEFAULT '',
    text        TEXT    NOT NULL DEFAULT '',
    x1          REAL    NOT NULL,
    y1          REAL    NOT NULL,
    x2          REAL    NOT NULL,
    y2          REAL    NOT NULL,
    UNIQUE (job_id, row_no)
);
CREATE INDEX IF NOT EXISTS idx_boxes_job ON boxes(job_id);

CREATE TABLE IF NOT EXISTS lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    orientation TEXT    NOT NULL CHECK (orientation IN ('h','v')),
    position    REAL    NOT NULL,
    UNIQUE (job_id, orientation, position)
);
CREATE INDEX IF NOT EXISTS idx_lines_job ON lines(job_id);

-- Human decisions for boxes that do not sit wholly inside one cell.
-- `overlap_cells` is the JSON list of cell keys (left|top|right|bottom) that
-- intersected the box when the decision was made.  After any line edit the
-- list is recomputed: an unchanged list means the intersecting cells and all
-- of their boundaries are unchanged, so the decision can be reused.
CREATE TABLE IF NOT EXISTS decisions (
    job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    box_id       INTEGER NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL CHECK (kind IN ('cell','exclude')),
    target_cell  TEXT    NOT NULL DEFAULT '',
    overlap_cells TEXT   NOT NULL DEFAULT '[]',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (job_id, box_id)
);
