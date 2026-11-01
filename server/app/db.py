"""SQLite storage (§4). File data/studio.db; flexible attrs as TEXT(JSON).

Foreign keys on. Times are UTC ISO8601 TEXT.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT

DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS works (
  id INTEGER PRIMARY KEY,
  uid TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  logline TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  stage TEXT NOT NULL DEFAULT 'ideas',
  structure_json TEXT NOT NULL DEFAULT '{}',
  ideas_locked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS story_designs (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES works(id),
  version INTEGER NOT NULL,
  scope_total_episodes INTEGER NOT NULL,
  concept_json TEXT NOT NULL,
  sections_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  approved_at TEXT,
  UNIQUE (work_id, version)
);
CREATE TABLE IF NOT EXISTS episode_outlines (
  id INTEGER PRIMARY KEY,
  design_id INTEGER NOT NULL REFERENCES story_designs(id),
  episode_no INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  goal TEXT NOT NULL DEFAULT '',
  events_json TEXT NOT NULL DEFAULT '[]',
  char_changes_json TEXT NOT NULL DEFAULT '[]',
  foreshadowing_json TEXT NOT NULL DEFAULT '[]',
  links_json TEXT NOT NULL DEFAULT '{}',
  constraints_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (design_id, episode_no)
);
CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  first_episode INTEGER NOT NULL,
  description TEXT DEFAULT '',
  lifecycle_status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_id INTEGER PRIMARY KEY,
  entity_id TEXT REFERENCES entities(entity_id),
  alias TEXT NOT NULL,
  basis TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS manuscript_revisions (
  revision_id INTEGER PRIMARY KEY,
  episode_no INTEGER NOT NULL,
  version INTEGER NOT NULL,
  text TEXT NOT NULL,
  approval_status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  UNIQUE (episode_no, version)
);
CREATE TABLE IF NOT EXISTS change_proposals (
  proposal_set_id INTEGER PRIMARY KEY,
  episode_no INTEGER NOT NULL,
  manuscript_revision_id INTEGER NOT NULL REFERENCES manuscript_revisions(revision_id),
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_proposal_items (
  item_id INTEGER PRIMARY KEY,
  proposal_set_id INTEGER NOT NULL REFERENCES change_proposals(proposal_set_id),
  entity_id TEXT,
  candidate_name TEXT DEFAULT '',
  operation TEXT NOT NULL,
  attribute_path TEXT NOT NULL DEFAULT '',
  old_value TEXT DEFAULT '',
  new_value TEXT DEFAULT '',
  story_time TEXT DEFAULT '',
  evidence_quote TEXT NOT NULL DEFAULT '',
  evidence_start INTEGER, evidence_end INTEGER,
  validation TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS entity_changes (
  change_id INTEGER PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(entity_id),
  episode_no INTEGER NOT NULL,
  manuscript_revision_id INTEGER NOT NULL,
  attribute_path TEXT NOT NULL,
  operation TEXT NOT NULL,
  old_value TEXT DEFAULT '',
  new_value TEXT DEFAULT '',
  story_time TEXT DEFAULT '',
  evidence_quote TEXT DEFAULT '',
  committed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_snapshots (
  entity_id TEXT NOT NULL REFERENCES entities(entity_id),
  episode_no INTEGER NOT NULL,
  attributes_json TEXT NOT NULL,
  PRIMARY KEY (entity_id, episode_no)
);
CREATE TABLE IF NOT EXISTS entity_relations (
  relation_id INTEGER PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES entities(entity_id),
  predicate TEXT NOT NULL,
  object_id TEXT NOT NULL REFERENCES entities(entity_id),
  valid_from_episode INTEGER NOT NULL,
  valid_to_episode INTEGER,
  evidence_quote TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY,
  episode_no INTEGER NOT NULL,
  summary TEXT NOT NULL,
  story_time TEXT DEFAULT '',
  cause TEXT DEFAULT '',
  consequence TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS event_participants (
  event_id INTEGER NOT NULL REFERENCES events(event_id),
  entity_id TEXT NOT NULL REFERENCES entities(entity_id),
  role TEXT DEFAULT '',
  PRIMARY KEY (event_id, entity_id)
);
CREATE TABLE IF NOT EXISTS entity_mentions (
  mention_id INTEGER PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(entity_id),
  episode_no INTEGER NOT NULL,
  manuscript_revision_id INTEGER NOT NULL,
  quote TEXT NOT NULL,
  start_offset INTEGER, end_offset INTEGER
);
CREATE TABLE IF NOT EXISTS episode_jobs (
  episode_no INTEGER PRIMARY KEY,
  design_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  manuscript_revision_id INTEGER,
  proposal_set_id INTEGER,
  last_error TEXT DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  bundle TEXT NOT NULL,
  episode_no INTEGER,
  work_id INTEGER,
  model TEXT NOT NULL,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  latency_ms INTEGER DEFAULT 0,
  ok INTEGER NOT NULL,
  error TEXT DEFAULT '',
  prompt_system TEXT NOT NULL DEFAULT '',
  prompt_user TEXT NOT NULL DEFAULT '',
  output_text TEXT NOT NULL DEFAULT '',
  finish_reason TEXT NOT NULL DEFAULT '',
  reasoning_tokens INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS style_policies (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES works(id),
  pov TEXT DEFAULT '', diction TEXT DEFAULT '',
  length_target TEXT DEFAULT '',
  feedback TEXT DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idea_threads (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES works(id),
  title TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idea_messages (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES idea_threads(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  refs_json TEXT NOT NULL DEFAULT '[]',
  choices_json TEXT NOT NULL DEFAULT '[]',
  proposals_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS story_synopsis (
  work_id INTEGER PRIMARY KEY REFERENCES works(id),
  content TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS synopsis_revisions (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES works(id),
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setting_entries (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES works(id),
  code TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'misc',
  title TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS design_nodes (
  id TEXT PRIMARY KEY,
  design_id INTEGER NOT NULL REFERENCES story_designs(id),
  parent_id TEXT REFERENCES design_nodes(id),
  kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  logline TEXT NOT NULL DEFAULT '',
  body_json TEXT NOT NULL DEFAULT '{}',
  sort_order INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_settings (
  id INTEGER PRIMARY KEY,
  node_id TEXT NOT NULL REFERENCES design_nodes(id),
  code TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'misc',
  title TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_uid(nbytes: int = 9) -> str:
    """URL-safe per-work public id (12 chars). Uniqueness enforced in Python."""
    return secrets.token_urlsafe(nbytes)


def db_path() -> Path:
    override = os.environ.get("STUDIO_DB")
    if override:
        return Path(override)
    return PROJECT_ROOT / "data" / "studio.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs sync deps/endpoints/teardown on
    # different threadpool threads. Each connection is request-scoped and
    # never used by two threads concurrently, so cross-thread handoff is safe.
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    _migrate(conn)
    return conn


def find_work(conn: sqlite3.Connection, key) -> dict | None:
    """Resolve a work by public uid or integer id. Returns None when missing.

    Uid wins on ambiguous all-digit keys so a work is always reachable
    through its own uid.
    """
    row = conn.execute("SELECT * FROM works WHERE uid=?",
                       (str(key),)).fetchone()
    if row is None:
        try:
            row = conn.execute("SELECT * FROM works WHERE id=?",
                               (int(key),)).fetchone()
        except (ValueError, TypeError):
            row = None
    return dict(row) if row else None


def touch_work(conn: sqlite3.Connection, work_id: int,
               stage: str | None = None) -> None:
    """Bump updated_at (and optionally stage). Caller commits."""
    if stage is None:
        conn.execute("UPDATE works SET updated_at=? WHERE id=?",
                     (now_iso(), work_id))
    else:
        conn.execute("UPDATE works SET updated_at=?, stage=? WHERE id=?",
                     (now_iso(), stage, work_id))


def _migrate(conn: sqlite3.Connection) -> None:
    for sql in (
        "ALTER TABLE works ADD COLUMN uid TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE works ADD COLUMN stage TEXT NOT NULL DEFAULT 'ideas'",
        "ALTER TABLE works ADD COLUMN structure_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE works ADD COLUMN ideas_locked INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE works ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE idea_messages ADD COLUMN proposals_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE llm_calls ADD COLUMN work_id INTEGER",
        "ALTER TABLE llm_calls ADD COLUMN prompt_system TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE llm_calls ADD COLUMN prompt_user TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE llm_calls ADD COLUMN output_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE llm_calls ADD COLUMN finish_reason TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE llm_calls ADD COLUMN reasoning_tokens INTEGER DEFAULT 0",
        "ALTER TABLE setting_entries ADD COLUMN code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE episode_jobs ADD COLUMN fix_instruction TEXT DEFAULT ''",
        "ALTER TABLE change_proposals ADD COLUMN events_json TEXT DEFAULT '[]'",
        "ALTER TABLE episode_jobs ADD COLUMN quality_json TEXT DEFAULT '{}'",
        "ALTER TABLE change_proposal_items ADD COLUMN reason TEXT DEFAULT ''",
        "ALTER TABLE change_proposal_items ADD COLUMN counter_quote TEXT DEFAULT ''",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # already migrated
    # Backfill pre-UID rows.
    for row in conn.execute("SELECT id FROM works WHERE uid IS NULL OR uid=''"):
        conn.execute("UPDATE works SET uid=? WHERE id=?", (new_uid(), row["id"]))
    conn.execute("UPDATE works SET updated_at=created_at"
                 " WHERE updated_at IS NULL OR updated_at=''")
    # Backfill per-work D-number codes for pre-existing settings.
    for w in conn.execute("SELECT DISTINCT work_id FROM setting_entries"):
        wid = w["work_id"]
        top = conn.execute(
            "SELECT code FROM setting_entries WHERE work_id=? AND code LIKE 'D%'"
            " ORDER BY code DESC LIMIT 1", (wid,)).fetchone()
        n = 0
        if top and top["code"]:
            try:
                n = int(top["code"][1:])
            except ValueError:
                n = 0
        rows = conn.execute(
            "SELECT id FROM setting_entries"
            " WHERE work_id=? AND (code IS NULL OR code='') ORDER BY id",
            (wid,)).fetchall()
        for r in rows:
            n += 1
            conn.execute("UPDATE setting_entries SET code=? WHERE id=?",
                         (f"D{n:03d}", r["id"]))
    conn.commit()
