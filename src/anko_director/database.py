from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .character_templates import CHARACTER_TEMPLATES, coc7_derived_values, stat_value


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._write_lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    premise TEXT NOT NULL DEFAULT '',
                    discord_forum_channel_id TEXT,
                    discord_thread_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(campaign_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(campaign_id, name)
                );
                CREATE TABLE IF NOT EXISTS roll_proposals (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    source_text TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rolls (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    proposal_id TEXT REFERENCES roll_proposals(id),
                    event_id TEXT NOT NULL REFERENCES events(id),
                    result_json TEXT NOT NULL,
                    voided_at TEXT,
                    void_reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    instruction TEXT NOT NULL,
                    content TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_messages (
                    discord_message_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    author_id TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL REFERENCES events(id),
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    discord_message_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(event_id)
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    derived_facts_json TEXT NOT NULL DEFAULT '{}',
                    x REAL NOT NULL DEFAULT 0,
                    y REAL NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 1,
                    archived INTEGER NOT NULL DEFAULT 0,
                    legacy_character_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_view_state (
                    campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
                    pan_x REAL NOT NULL DEFAULT 0,
                    pan_y REAL NOT NULL DEFAULT 0,
                    zoom REAL NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS director_turns (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    branch_id TEXT,
                    instruction TEXT NOT NULL,
                    asset_snapshot_json TEXT NOT NULL,
                    historical_reference_json TEXT NOT NULL DEFAULT '[]',
                    proposal_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    roll_result_json TEXT,
                    draft_id TEXT REFERENCES drafts(id),
                    commit_id TEXT,
                    world_delta_status TEXT NOT NULL DEFAULT 'pending',
                    memory_status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    discord_message_id TEXT UNIQUE,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS character_stats (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    notation TEXT NOT NULL,
                    value INTEGER,
                    source TEXT NOT NULL DEFAULT 'unresolved',
                    source_event_id TEXT REFERENCES events(id),
                    locked INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(asset_id, name)
                );
                CREATE TABLE IF NOT EXISTS character_traits (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    source_event_id TEXT REFERENCES events(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS character_memories (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    owner_asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    involved_asset_ids_json TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL REFERENCES director_turns(id) ON DELETE CASCADE,
                    source_event_id TEXT REFERENCES events(id),
                    summary TEXT NOT NULL,
                    interpretation TEXT NOT NULL,
                    emotion TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    generated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_asset_id, source_turn_id)
                );
                CREATE TABLE IF NOT EXISTS coc_combat_states (
                    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
                    max_hp INTEGER NOT NULL,
                    current_hp INTEGER NOT NULL,
                    armor INTEGER NOT NULL DEFAULT 0,
                    major_wound INTEGER NOT NULL DEFAULT 0,
                    unconscious INTEGER NOT NULL DEFAULT 0,
                    dying INTEGER NOT NULL DEFAULT 0,
                    dead INTEGER NOT NULL DEFAULT 0,
                    skip_next_action INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_encounters (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    round_number INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_actions (
                    id TEXT PRIMARY KEY,
                    encounter_id TEXT NOT NULL REFERENCES coc_encounters(id) ON DELETE CASCADE,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL REFERENCES events(id),
                    action_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS timeline_branches (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    base_commit_id TEXT,
                    head_commit_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(campaign_id, name)
                );
                CREATE TABLE IF NOT EXISTS campaign_timeline_state (
                    campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
                    active_branch_id TEXT NOT NULL REFERENCES timeline_branches(id),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_commits (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    branch_id TEXT NOT NULL REFERENCES timeline_branches(id),
                    parent_commit_id TEXT REFERENCES world_commits(id),
                    director_turn_id TEXT REFERENCES director_turns(id),
                    turn_number INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    delta_json TEXT NOT NULL DEFAULT '{}',
                    event_sequence INTEGER NOT NULL DEFAULT 0,
                    snapshot_hash TEXT NOT NULL,
                    quality TEXT NOT NULL DEFAULT 'full',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS branch_workspaces (
                    branch_id TEXT PRIMARY KEY REFERENCES timeline_branches(id) ON DELETE CASCADE,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_relations (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    source_asset_id TEXT NOT NULL REFERENCES assets(id),
                    target_asset_id TEXT NOT NULL REFERENCES assets(id),
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    source_turn_id TEXT REFERENCES director_turns(id),
                    ended_turn_id TEXT REFERENCES director_turns(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS status_cards (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    target_asset_id TEXT NOT NULL REFERENCES assets(id),
                    duration_turns INTEGER,
                    remaining_turns INTEGER,
                    description TEXT NOT NULL,
                    source_turn_id TEXT REFERENCES director_turns(id),
                    ended_turn_id TEXT REFERENCES director_turns(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turn_outcomes (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    branch_id TEXT NOT NULL REFERENCES timeline_branches(id),
                    turn_id TEXT NOT NULL REFERENCES director_turns(id),
                    turn_number INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    x REAL NOT NULL DEFAULT 0,
                    y REAL NOT NULL DEFAULT 0,
                    analysis_status TEXT NOT NULL DEFAULT 'complete',
                    created_at TEXT NOT NULL,
                    UNIQUE(turn_id)
                );
                CREATE TABLE IF NOT EXISTS outcome_asset_links (
                    outcome_id TEXT NOT NULL REFERENCES turn_outcomes(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL REFERENCES assets(id),
                    label TEXT NOT NULL DEFAULT '参与',
                    PRIMARY KEY(outcome_id, asset_id)
                );
                CREATE TABLE IF NOT EXISTS outcome_links (
                    source_outcome_id TEXT NOT NULL REFERENCES turn_outcomes(id) ON DELETE CASCADE,
                    target_outcome_id TEXT NOT NULL REFERENCES turn_outcomes(id) ON DELETE CASCADE,
                    label TEXT NOT NULL DEFAULT '导致',
                    PRIMARY KEY(source_outcome_id, target_outcome_id)
                );
                CREATE TABLE IF NOT EXISTS turn_world_analysis (
                    turn_id TEXT PRIMARY KEY REFERENCES director_turns(id) ON DELETE CASCADE,
                    delta_json TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_discord_campaign
                    ON discord_messages(campaign_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_assets_campaign
                    ON assets(campaign_id, archived, updated_at);
                CREATE INDEX IF NOT EXISTS idx_turns_campaign
                    ON director_turns(campaign_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_chat_campaign
                    ON chat_messages(campaign_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_chat_status
                    ON chat_messages(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_stats_asset ON character_stats(asset_id, position);
                CREATE INDEX IF NOT EXISTS idx_traits_asset
                    ON character_traits(asset_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_memories_owner
                    ON character_memories(owner_asset_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_coc_encounters_campaign
                    ON coc_encounters(campaign_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_coc_actions_encounter
                    ON coc_actions(encounter_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_world_commits_branch
                    ON world_commits(branch_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_relations_campaign
                    ON world_relations(campaign_id, ended_turn_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_status_cards_campaign
                    ON status_cards(campaign_id, ended_turn_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_outcomes_branch
                    ON turn_outcomes(branch_id, turn_number);
                """
            )
            migration = db.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone()
            if migration is None:
                rows = db.execute("SELECT * FROM characters ORDER BY created_at").fetchall()
                for index, row in enumerate(rows):
                    data = json.loads(row["data_json"])
                    db.execute(
                        "INSERT OR IGNORE INTO assets("
                        "id,campaign_id,kind,name,color,summary,body,derived_facts_json,x,y,"
                        "legacy_character_id,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid.uuid4()),
                            row["campaign_id"],
                            "character",
                            row["name"],
                            "#d89469",
                            "由旧版人物卡迁移",
                            "",
                            canonical_json(data),
                            float((index % 4) * 180),
                            float((index // 4) * 120),
                            row["id"],
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )
                messages = db.execute(
                    "SELECT * FROM discord_messages ORDER BY created_at"
                ).fetchall()
                for row in messages:
                    db.execute(
                        "INSERT OR IGNORE INTO chat_messages("
                        "id,campaign_id,source,author_id,author_name,content,direction,status,"
                        "discord_message_id,metadata_json,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid.uuid4()),
                            row["campaign_id"],
                            "discord",
                            row["author_id"],
                            row["author_name"],
                            row["content"],
                            row["direction"],
                            "sent" if row["direction"] == "outbound" else "received",
                            row["discord_message_id"],
                            "{}",
                            row["created_at"],
                            row["created_at"],
                        ),
                    )
                db.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(1,?)", (utc_now(),)
                )
            prompt_migration = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=2"
            ).fetchone()
            if prompt_migration is None:
                turns = db.execute(
                    "SELECT id,campaign_id,instruction,created_at FROM director_turns "
                    "WHERE status='completed' ORDER BY created_at"
                ).fetchall()
                for turn in turns:
                    message_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"anko-director:turn:{turn['id']}:director-prompt",
                        )
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO chat_messages("
                        "id,campaign_id,source,author_id,author_name,content,direction,status,"
                        "discord_message_id,metadata_json,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            message_id,
                            turn["campaign_id"],
                            "director_prompt",
                            "director",
                            "你（导演）",
                            turn["instruction"],
                            "local",
                            "received",
                            None,
                            canonical_json({"turn_id": turn["id"], "canonical": True}),
                            turn["created_at"],
                            turn["created_at"],
                        ),
                    )
                db.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(2,?)", (utc_now(),)
                )
            combat_migration = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=3"
            ).fetchone()
            if combat_migration is None:
                db.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(3,?)", (utc_now(),)
                )
            world_migration = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=4"
            ).fetchone()
            if world_migration is None:
                columns = {
                    row["name"]
                    for row in db.execute("PRAGMA table_info(director_turns)").fetchall()
                }
                additions = {
                    "branch_id": "TEXT",
                    "historical_reference_json": "TEXT NOT NULL DEFAULT '[]'",
                    "commit_id": "TEXT",
                    "world_delta_status": "TEXT NOT NULL DEFAULT 'pending'",
                    "memory_status": "TEXT NOT NULL DEFAULT 'pending'",
                }
                for name, definition in additions.items():
                    if name not in columns:
                        db.execute(f"ALTER TABLE director_turns ADD COLUMN {name} {definition}")
                db.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(4,?)", (utc_now(),)
                )
        with self.connect() as db:
            campaign_ids = [row["id"] for row in db.execute("SELECT id FROM campaigns").fetchall()]
        for campaign_id in campaign_ids:
            self.ensure_timeline(campaign_id)

    def create_campaign(self, title: str, premise: str = "") -> dict[str, Any]:
        campaign_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO campaigns(id,title,premise,created_at,updated_at) VALUES(?,?,?,?,?)",
                (campaign_id, title, premise, now, now),
            )
        self.ensure_timeline(campaign_id)
        return self.get_campaign(campaign_id)

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM campaigns ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row is None:
            raise KeyError("游戏存档不存在")
        return dict(row)

    def bind_discord(self, campaign_id: str, forum_id: int, thread_id: int) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE campaigns SET discord_forum_channel_id=?,discord_thread_id=?,updated_at=? "
                "WHERE id=?",
                (str(forum_id), str(thread_id), utc_now(), campaign_id),
            )

    def append_event(
        self, campaign_id: str, kind: str, payload: dict[str, Any], status: str = "final"
    ) -> dict[str, Any]:
        with self._write_lock, self.connect() as db:
            last = db.execute(
                "SELECT sequence,event_hash FROM events WHERE campaign_id=? "
                "ORDER BY sequence DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            sequence = (last["sequence"] + 1) if last else 1
            previous_hash = last["event_hash"] if last else "0" * 64
            event_id = str(uuid.uuid4())
            created_at = utc_now()
            body = {
                "id": event_id,
                "campaign_id": campaign_id,
                "sequence": sequence,
                "kind": kind,
                "status": status,
                "payload": payload,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    campaign_id,
                    sequence,
                    kind,
                    status,
                    canonical_json(payload),
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            )
            db.execute("UPDATE campaigns SET updated_at=? WHERE id=?", (created_at, campaign_id))
        return {**body, "event_hash": event_hash}

    def list_events(self, campaign_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE campaign_id=? ORDER BY sequence DESC LIMIT ?",
                (campaign_id, limit),
            ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def verify_event_chain(self, campaign_id: str) -> bool:
        events = self.list_events(campaign_id, limit=1_000_000)
        expected_previous = "0" * 64
        for event in events:
            if event["previous_hash"] != expected_previous:
                return False
            body = {
                "id": event["id"],
                "campaign_id": event["campaign_id"],
                "sequence": event["sequence"],
                "kind": event["kind"],
                "status": event["status"],
                "payload": event["payload"],
                "previous_hash": event["previous_hash"],
                "created_at": event["created_at"],
            }
            calculated = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            if calculated != event["event_hash"]:
                return False
            expected_previous = event["event_hash"]
        return True

    def upsert_character(self, campaign_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._write_lock, self.connect() as db:
            existing = db.execute(
                "SELECT id,data_json FROM characters WHERE campaign_id=? AND name=?",
                (campaign_id, name),
            ).fetchone()
            if existing:
                merged = json.loads(existing["data_json"])
                merged.update(data)
                character_id = existing["id"]
                db.execute(
                    "UPDATE characters SET data_json=?,updated_at=? WHERE id=?",
                    (canonical_json(merged), now, character_id),
                )
            else:
                character_id = str(uuid.uuid4())
                merged = data
                db.execute(
                    "INSERT INTO characters VALUES(?,?,?,?,?,?)",
                    (character_id, campaign_id, name, canonical_json(merged), now, now),
                )
        self.upsert_character_asset(campaign_id, character_id, name, merged)
        return {"id": character_id, "campaign_id": campaign_id, "name": name, "data": merged}

    def upsert_character_asset(
        self, campaign_id: str, character_id: str, name: str, facts: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self._write_lock, self.connect() as db:
            existing = db.execute(
                "SELECT id FROM assets WHERE legacy_character_id=?", (character_id,)
            ).fetchone()
            if existing is None:
                same_name = db.execute(
                    "SELECT id FROM assets WHERE campaign_id=? AND kind='character' "
                    "AND archived=0 AND name=? ORDER BY created_at",
                    (campaign_id, name),
                ).fetchall()
                if len(same_name) == 1:
                    existing = same_name[0]
            if existing:
                db.execute(
                    "UPDATE assets SET name=?,derived_facts_json=?,legacy_character_id=?,"
                    "revision=revision+1,"
                    "updated_at=? "
                    "WHERE id=?",
                    (name, canonical_json(facts), character_id, now, existing["id"]),
                )
                asset_id = existing["id"]
            else:
                asset_id = str(uuid.uuid4())
                count = db.execute(
                    "SELECT COUNT(*) AS count FROM assets WHERE campaign_id=?", (campaign_id,)
                ).fetchone()["count"]
                db.execute(
                    "INSERT INTO assets("
                    "id,campaign_id,kind,name,color,summary,body,derived_facts_json,x,y,"
                    "legacy_character_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        campaign_id,
                        "character",
                        name,
                        "#d89469",
                        "由投骰生成的人物资产",
                        "",
                        canonical_json(facts),
                        float((count % 4) * 180),
                        float((count // 4) * 120),
                        character_id,
                        now,
                        now,
                    ),
                )
        return self.get_asset(asset_id)

    def apply_roll_to_character(
        self, campaign_id: str, name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for operation in result["results"]:
            if operation["kind"] == "dice":
                values[operation["label"]] = operation["total"]
            else:
                values[operation["label"]] = [
                    draw["selected"]["label"] for draw in operation["draws"]
                ]
        return self.upsert_character(campaign_id, name, values)

    def list_characters(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM characters WHERE campaign_id=? ORDER BY created_at", (campaign_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json"))
            result.append(item)
        return result

    def save_proposal(self, campaign_id: str, source: str, proposal: dict[str, Any]) -> str:
        proposal_id = str(uuid.uuid4())
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO roll_proposals VALUES(?,?,?,?,?,?)",
                (proposal_id, campaign_id, source, canonical_json(proposal), "pending", utc_now()),
            )
        return proposal_id

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM roll_proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError("投骰提案不存在")
        item = dict(row)
        item["proposal"] = json.loads(item.pop("proposal_json"))
        return item

    def record_roll(
        self, campaign_id: str, proposal_id: str | None, result: dict[str, Any]
    ) -> dict[str, Any]:
        event = self.append_event(campaign_id, "roll", result)
        roll_id = str(uuid.uuid4())
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO rolls(id,campaign_id,proposal_id,event_id,result_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (roll_id, campaign_id, proposal_id, event["id"], canonical_json(result), utc_now()),
            )
            if proposal_id:
                db.execute("UPDATE roll_proposals SET status='executed' WHERE id=?", (proposal_id,))
        return {"roll_id": roll_id, "event": event, "result": result}

    def unpublished_rolls(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            last_story = db.execute(
                "SELECT COALESCE(MAX(sequence),0) AS sequence FROM events "
                "WHERE campaign_id=? AND kind='story'",
                (campaign_id,),
            ).fetchone()["sequence"]
            rows = db.execute(
                "SELECT r.id,r.result_json,e.sequence FROM rolls r "
                "JOIN events e ON e.id=r.event_id "
                "WHERE r.campaign_id=? AND e.sequence>? AND r.voided_at IS NULL "
                "ORDER BY e.sequence",
                (campaign_id, last_story),
            ).fetchall()
        return [
            {"id": row["id"], "sequence": row["sequence"], "result": json.loads(row["result_json"])}
            for row in rows
        ]

    def save_draft(
        self,
        campaign_id: str,
        instruction: str,
        content: str,
        provider: str,
        model: str,
    ) -> dict[str, Any]:
        draft_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?)",
                (draft_id, campaign_id, instruction, content, provider, model, "draft", now, now),
            )
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError("故事草稿不存在")
        return dict(row)

    def publish_draft(
        self, draft_id: str, content: str, outbox_content: str | None = None
    ) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        event = self.append_event(draft["campaign_id"], "story", {"content": content})
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE drafts SET content=?,status='published',updated_at=? WHERE id=?",
                (content, now, draft_id),
            )
            outbox_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO outbox(id,campaign_id,event_id,content,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    outbox_id,
                    draft["campaign_id"],
                    event["id"],
                    outbox_content or content,
                    "pending",
                    now,
                    now,
                ),
            )
        return {"event": event, "outbox_id": outbox_id}

    def pending_outbox(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT o.*,c.discord_thread_id FROM outbox o JOIN campaigns c "
                "ON c.id=o.campaign_id WHERE o.status IN ('pending','retry') "
                "ORDER BY o.created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_outbox_sent(self, outbox_id: str, message_ids: list[int]) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE outbox SET status='sent',discord_message_ids_json=?,updated_at=? "
                "WHERE id=?",
                (canonical_json(message_ids), utc_now(), outbox_id),
            )

    def mark_outbox_failed(self, outbox_id: str, error: str) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE outbox SET status='retry',attempts=attempts+1,last_error=?,updated_at=? "
                "WHERE id=?",
                (error[:2000], utc_now(), outbox_id),
            )

    def record_discord_message(
        self,
        message_id: int,
        campaign_id: str,
        author_id: int,
        author_name: str,
        content: str,
        direction: str,
    ) -> bool:
        with self._write_lock, self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO discord_messages VALUES(?,?,?,?,?,?,?)",
                (
                    str(message_id),
                    campaign_id,
                    str(author_id),
                    author_name,
                    content,
                    direction,
                    utc_now(),
                ),
            )
        return cursor.rowcount > 0

    def list_discord_messages(self, campaign_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM discord_messages WHERE campaign_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (campaign_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def campaign_for_thread(self, thread_id: int) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM campaigns WHERE discord_thread_id=?", (str(thread_id),)
            ).fetchone()
        return row["id"] if row else None

    def create_asset(
        self,
        campaign_id: str,
        kind: str,
        name: str,
        color: str,
        summary: str = "",
        body: str = "",
        x: float = 0,
        y: float = 0,
    ) -> dict[str, Any]:
        self.get_campaign(campaign_id)
        asset_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO assets("
                "id,campaign_id,kind,name,color,summary,body,x,y,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (asset_id, campaign_id, kind, name, color, summary, body, x, y, now, now),
            )
        return self.get_asset(asset_id)

    def _asset_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["derived_facts"] = json.loads(item.pop("derived_facts_json"))
        item["archived"] = bool(item["archived"])
        return item

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError("资产不存在")
        return self._asset_from_row(row)

    def list_assets(self, campaign_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        self.get_campaign(campaign_id)
        where = "campaign_id=?" if include_archived else "campaign_id=? AND archived=0"
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM assets WHERE {where} ORDER BY created_at", (campaign_id,)
            ).fetchall()
        return [self._asset_from_row(row) for row in rows]

    def update_asset(
        self, asset_id: str, kind: str, name: str, color: str, summary: str, body: str
    ) -> dict[str, Any]:
        self.get_asset(asset_id)
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE assets SET kind=?,name=?,color=?,summary=?,body=?,"
                "revision=revision+1,updated_at=? WHERE id=?",
                (kind, name, color, summary, body, utc_now(), asset_id),
            )
        return self.get_asset(asset_id)

    def update_asset_position(self, asset_id: str, x: float, y: float) -> dict[str, Any]:
        self.get_asset(asset_id)
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE assets SET x=?,y=?,updated_at=? WHERE id=?", (x, y, utc_now(), asset_id)
            )
        return self.get_asset(asset_id)

    def archive_asset(self, asset_id: str) -> dict[str, Any]:
        self.get_asset(asset_id)
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE assets SET archived=1,revision=revision+1,updated_at=? WHERE id=?",
                (utc_now(), asset_id),
            )
        return self.get_asset(asset_id)

    def asset_snapshots(self, campaign_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        seen: set[str] = set()
        for asset_id in asset_ids:
            if asset_id in seen:
                continue
            seen.add(asset_id)
            asset = self.get_asset(asset_id)
            if asset["campaign_id"] != campaign_id or asset["archived"]:
                raise ValueError("引用资产不属于当前存档或已归档")
            snapshot = {
                "id": asset["id"],
                "kind": asset["kind"],
                "name": asset["name"],
                "color": asset["color"],
                "summary": asset["summary"],
                "body": asset["body"],
                "derived_facts": asset["derived_facts"],
                "revision": asset["revision"],
                "updated_at": asset["updated_at"],
                "content_hash": hashlib.sha256(
                    canonical_json(
                        {
                            "summary": asset["summary"],
                            "body": asset["body"],
                            "derived_facts": asset["derived_facts"],
                        }
                    ).encode()
                ).hexdigest(),
            }
            if asset["kind"] == "character":
                snapshot["stats"] = self.list_character_stats(asset["id"])
                snapshot["traits"] = self.list_character_traits(asset["id"])
            snapshots.append(snapshot)
        return snapshots

    def get_canvas_view(self, campaign_id: str) -> dict[str, float]:
        self.get_campaign(campaign_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT pan_x,pan_y,zoom FROM campaign_view_state WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
        return dict(row) if row else {"pan_x": 0.0, "pan_y": 0.0, "zoom": 1.0}

    def save_canvas_view(
        self, campaign_id: str, pan_x: float, pan_y: float, zoom: float
    ) -> dict[str, float]:
        self.get_campaign(campaign_id)
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO campaign_view_state(campaign_id,pan_x,pan_y,zoom,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET "
                "pan_x=excluded.pan_x,pan_y=excluded.pan_y,zoom=excluded.zoom,"
                "updated_at=excluded.updated_at",
                (campaign_id, pan_x, pan_y, zoom, utc_now()),
            )
        return {"pan_x": pan_x, "pan_y": pan_y, "zoom": zoom}

    def create_turn(
        self,
        campaign_id: str,
        instruction: str,
        asset_snapshots: list[dict[str, Any]],
        proposal: dict[str, Any],
        historical_references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        turn_id = str(uuid.uuid4())
        now = utc_now()
        branch = self.get_active_branch(campaign_id)
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO director_turns("
                "id,campaign_id,branch_id,instruction,asset_snapshot_json,"
                "historical_reference_json,proposal_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id,
                    campaign_id,
                    branch["id"],
                    instruction,
                    canonical_json(asset_snapshots),
                    canonical_json(historical_references or []),
                    canonical_json(proposal),
                    "pending",
                    now,
                    now,
                ),
            )
        return self.get_turn(turn_id)

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM director_turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            raise KeyError("导演回合不存在")
        item = dict(row)
        item["asset_snapshots"] = json.loads(item.pop("asset_snapshot_json"))
        item["historical_references"] = json.loads(
            item.pop("historical_reference_json", "[]") or "[]"
        )
        item["proposal"] = json.loads(item.pop("proposal_json"))
        item["roll_result"] = (
            json.loads(item.pop("roll_result_json")) if item["roll_result_json"] else None
        )
        item.pop("roll_result_json", None)
        return item

    def complete_turn(
        self, turn_id: str, roll_result: dict[str, Any], draft_id: str
    ) -> dict[str, Any]:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE director_turns SET status='completed',roll_result_json=?,draft_id=?,"
                "error=NULL,updated_at=? WHERE id=?",
                (canonical_json(roll_result), draft_id, utc_now(), turn_id),
            )
        return self.get_turn(turn_id)

    def save_turn_roll(self, turn_id: str, roll_result: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE director_turns SET status='executing',roll_result_json=?,error=NULL,"
                "updated_at=? WHERE id=? AND roll_result_json IS NULL",
                (canonical_json(roll_result), utc_now(), turn_id),
            )
        return self.get_turn(turn_id)

    def save_turn_roll_progress(self, turn_id: str, roll_result: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE director_turns SET status='executing',roll_result_json=?,error=NULL,"
                "updated_at=? WHERE id=? AND status!='completed'",
                (canonical_json(roll_result), utc_now(), turn_id),
            )
        return self.get_turn(turn_id)

    def fail_turn(self, turn_id: str, error: str) -> dict[str, Any]:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE director_turns SET status='failed',error=?,updated_at=? WHERE id=?",
                (error[:2000], utc_now(), turn_id),
            )
        return self.get_turn(turn_id)

    def create_chat_message(
        self,
        campaign_id: str,
        source: str,
        author_id: str,
        author_name: str,
        content: str,
        direction: str,
        status: str,
        discord_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_campaign(campaign_id)
        message_id = message_id or str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    message_id,
                    campaign_id,
                    source,
                    author_id,
                    author_name,
                    content,
                    direction,
                    status,
                    discord_message_id,
                    canonical_json(metadata or {}),
                    now,
                    now,
                ),
            )
            if discord_message_id:
                row = db.execute(
                    "SELECT id FROM chat_messages WHERE discord_message_id=?",
                    (discord_message_id,),
                ).fetchone()
                message_id = row["id"]
        return self.get_chat_message(message_id)

    def _chat_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def get_chat_message(self, message_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chat_messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise KeyError("消息不存在")
        return self._chat_from_row(row)

    def list_chat_messages(self, campaign_id: str, limit: int = 200) -> list[dict[str, Any]]:
        self.get_campaign(campaign_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM chat_messages WHERE campaign_id=? ORDER BY created_at DESC LIMIT ?",
                (campaign_id, limit),
            ).fetchall()
        return [self._chat_from_row(row) for row in reversed(rows)]

    def pending_chat_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT m.*,c.discord_thread_id FROM chat_messages m JOIN campaigns c "
                "ON c.id=m.campaign_id WHERE m.direction='outbound' "
                "AND m.status IN ('pending','retry') ORDER BY m.created_at LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def mark_chat_sent(self, message_id: str, discord_message_id: int) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE chat_messages SET status='sent',discord_message_id=?,updated_at=? "
                "WHERE id=?",
                (str(discord_message_id), utc_now(), message_id),
            )

    def mark_chat_failed(self, message_id: str, error: str) -> None:
        with self._write_lock, self.connect() as db:
            row = db.execute(
                "SELECT metadata_json FROM chat_messages WHERE id=?", (message_id,)
            ).fetchone()
            metadata = json.loads(row["metadata_json"]) if row else {}
            metadata["last_error"] = error[:1000]
            db.execute(
                "UPDATE chat_messages SET status='retry',metadata_json=?,updated_at=? WHERE id=?",
                (canonical_json(metadata), utc_now(), message_id),
            )

    def mark_story_preview_published(self, draft_id: str) -> None:
        with self._write_lock, self.connect() as db:
            rows = db.execute(
                "SELECT id,metadata_json FROM chat_messages WHERE source='story_preview' "
                "AND status='preview'"
            ).fetchall()
            ids = [
                row["id"]
                for row in rows
                if json.loads(row["metadata_json"]).get("draft_id") == draft_id
            ]
            if ids:
                db.executemany(
                    "UPDATE chat_messages SET status='published',updated_at=? WHERE id=?",
                    [(utc_now(), message_id) for message_id in ids],
                )

    def find_character_assets_by_name(self, campaign_id: str, name: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM assets WHERE campaign_id=? AND kind='character' "
                "AND archived=0 AND name=? ORDER BY created_at",
                (campaign_id, name),
            ).fetchall()
        return [self._asset_from_row(row) for row in rows]

    def apply_roll_to_asset(self, asset_id: str, result: dict[str, Any]) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("投骰目标不是人物资产")
        values: dict[str, Any] = {}
        locked_names = {
            stat["name"] for stat in self.list_character_stats(asset_id) if stat["locked"]
        }
        for operation in result["results"]:
            if operation["label"] in locked_names:
                continue
            if operation["kind"] == "dice":
                values[operation["label"]] = operation["total"]
            else:
                values[operation["label"]] = [
                    draw["selected"]["label"] for draw in operation["draws"]
                ]
        merged = {**asset["derived_facts"], **values}
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE assets SET derived_facts_json=?,revision=revision+1,"
                "updated_at=? WHERE id=?",
                (canonical_json(merged), now, asset_id),
            )
            for position, operation in enumerate(result["results"]):
                if operation["kind"] != "dice":
                    continue
                existing = db.execute(
                    "SELECT id,locked FROM character_stats WHERE asset_id=? AND name=?",
                    (asset_id, operation["label"]),
                ).fetchone()
                if existing and existing["locked"]:
                    continue
                notation = operation.get("notation") or "1d100"
                if existing:
                    db.execute(
                        "UPDATE character_stats SET notation=?,value=?,source='roll',"
                        "source_event_id=?,updated_at=? WHERE id=?",
                        (
                            notation,
                            operation["total"],
                            result.get("event_id"),
                            now,
                            existing["id"],
                        ),
                    )
                else:
                    db.execute(
                        "INSERT INTO character_stats VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid.uuid4()),
                            asset_id,
                            operation["label"],
                            notation,
                            operation["total"],
                            "roll",
                            result.get("event_id"),
                            0,
                            position,
                            now,
                            now,
                        ),
                    )
        return self.get_asset(asset_id)

    def ensure_character_stats(self, asset_id: str) -> list[dict[str, Any]]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        defaults = ["家庭背景", "音乐能力", "身体素质", "学习能力", "语言能力"]
        now = utc_now()
        with self._write_lock, self.connect() as db:
            existing = db.execute(
                "SELECT COUNT(*) AS count FROM character_stats WHERE asset_id=?", (asset_id,)
            ).fetchone()["count"]
            if existing == 0:
                for position, name in enumerate(defaults):
                    db.execute(
                        "INSERT INTO character_stats("
                        "id,asset_id,name,notation,position,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), asset_id, name, "1d100", position, now, now),
                    )
        return self.list_character_stats(asset_id)

    def _stat_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["locked"] = bool(item["locked"])
        return item

    def list_character_stats(self, asset_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM character_stats WHERE asset_id=? ORDER BY position,created_at",
                (asset_id,),
            ).fetchall()
        return [self._stat_from_row(row) for row in rows]

    def create_character_stat(self, asset_id: str, name: str, notation: str) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        now = utc_now()
        with self._write_lock, self.connect() as db:
            position = db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 AS position FROM character_stats "
                "WHERE asset_id=?",
                (asset_id,),
            ).fetchone()["position"]
            stat_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO character_stats VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    stat_id,
                    asset_id,
                    name,
                    notation,
                    None,
                    "unresolved",
                    None,
                    0,
                    position,
                    now,
                    now,
                ),
            )
        return self.get_character_stat(stat_id)

    def apply_character_template(
        self, asset_id: str, template_id: str, mode: str = "merge"
    ) -> list[dict[str, Any]]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        template = CHARACTER_TEMPLATES.get(template_id)
        if template is None:
            raise ValueError("人物模板不存在")
        if mode not in {"merge", "replace"}:
            raise ValueError("模板应用方式无效")
        now = utc_now()
        with self._write_lock, self.connect() as db:
            if mode == "replace":
                old_names = [
                    row["name"]
                    for row in db.execute(
                        "SELECT name FROM character_stats WHERE asset_id=?", (asset_id,)
                    ).fetchall()
                ]
                db.execute("DELETE FROM character_stats WHERE asset_id=?", (asset_id,))
                facts = asset["derived_facts"]
                for name in old_names:
                    facts.pop(name, None)
            else:
                facts = dict(asset["derived_facts"])
            position = db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 AS position "
                "FROM character_stats WHERE asset_id=?",
                (asset_id,),
            ).fetchone()["position"]
            for offset, definition in enumerate(template["stats"]):
                existing = db.execute(
                    "SELECT id FROM character_stats WHERE asset_id=? AND name=?",
                    (asset_id, definition["name"]),
                ).fetchone()
                if existing:
                    continue
                value = definition.get("value")
                db.execute(
                    "INSERT INTO character_stats VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        asset_id,
                        definition["name"],
                        definition["notation"],
                        value,
                        "template" if value is not None else "unresolved",
                        None,
                        int(definition.get("locked", False)),
                        position + offset,
                        now,
                        now,
                    ),
                )
                if value is not None:
                    facts[definition["name"]] = value
            db.execute(
                "UPDATE assets SET derived_facts_json=?,revision=revision+1,"
                "updated_at=? WHERE id=?",
                (canonical_json(facts), now, asset_id),
            )
        self.sync_coc_profile(asset_id, reset_hp=template_id == "coc7" and mode == "replace")
        return self.list_character_stats(asset_id)

    def batch_update_character_stats(
        self, asset_id: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        names = [str(item["name"]).strip() for item in items]
        if len(names) != len(set(names)):
            raise ValueError("同一人物不能有重名属性")
        current = {item["id"]: item for item in self.list_character_stats(asset_id)}
        if any(item["id"] not in current for item in items):
            raise ValueError("批量修改中包含不属于该人物的属性")
        now = utc_now()
        facts = dict(asset["derived_facts"])
        with self._write_lock, self.connect() as db:
            for position, item in enumerate(items):
                old = current[item["id"]]
                value = item.get("value")
                source = "manual" if value is not None else "unresolved"
                db.execute(
                    "UPDATE character_stats SET name=?,notation=?,value=?,source=?,locked=?,"
                    "position=?,updated_at=? WHERE id=?",
                    (
                        item["name"],
                        item["notation"],
                        value,
                        source,
                        int(item.get("locked", False)),
                        position,
                        now,
                        item["id"],
                    ),
                )
                facts.pop(old["name"], None)
                if value is not None:
                    facts[item["name"]] = value
            db.execute(
                "UPDATE assets SET derived_facts_json=?,revision=revision+1,"
                "updated_at=? WHERE id=?",
                (canonical_json(facts), now, asset_id),
            )
        self.sync_coc_profile(asset_id)
        return self.list_character_stats(asset_id)

    def get_character_stat(self, stat_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM character_stats WHERE id=?", (stat_id,)).fetchone()
        if row is None:
            raise KeyError("人物属性不存在")
        return self._stat_from_row(row)

    def update_character_stat(
        self, stat_id: str, name: str, notation: str, value: int | None, locked: bool
    ) -> dict[str, Any]:
        stat = self.get_character_stat(stat_id)
        now = utc_now()
        source = "manual" if value is not None else "unresolved"
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE character_stats SET name=?,notation=?,value=?,source=?,locked=?,"
                "updated_at=? WHERE id=?",
                (name, notation, value, source, int(locked), now, stat_id),
            )
            asset = self.get_asset(stat["asset_id"])
            facts = asset["derived_facts"]
            if value is None:
                facts.pop(stat["name"], None)
            else:
                if name != stat["name"]:
                    facts.pop(stat["name"], None)
                facts[name] = value
            db.execute(
                "UPDATE assets SET derived_facts_json=?,revision=revision+1,"
                "updated_at=? WHERE id=?",
                (canonical_json(facts), now, stat["asset_id"]),
            )
        return self.get_character_stat(stat_id)

    def delete_character_stat(self, stat_id: str) -> None:
        stat = self.get_character_stat(stat_id)
        asset = self.get_asset(stat["asset_id"])
        facts = asset["derived_facts"]
        facts.pop(stat["name"], None)
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute("DELETE FROM character_stats WHERE id=?", (stat_id,))
            db.execute(
                "UPDATE assets SET derived_facts_json=?,revision=revision+1,updated_at=? "
                "WHERE id=?",
                (canonical_json(facts), now, stat["asset_id"]),
            )

    def record_stat_roll(
        self, stat_id: str, value: int, event_id: str, notation: str
    ) -> dict[str, Any]:
        stat = self.get_character_stat(stat_id)
        if stat["locked"]:
            raise ValueError("该属性已经锁定")
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE character_stats SET value=?,notation=?,source='roll',source_event_id=?,"
                "updated_at=? WHERE id=?",
                (value, notation, event_id, now, stat_id),
            )
            asset = self.get_asset(stat["asset_id"])
            facts = {**asset["derived_facts"], stat["name"]: value}
            db.execute(
                "UPDATE assets SET derived_facts_json=?,revision=revision+1,"
                "updated_at=? WHERE id=?",
                (canonical_json(facts), now, stat["asset_id"]),
            )
        return self.get_character_stat(stat_id)

    def _combat_state_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("major_wound", "unconscious", "dying", "dead", "skip_next_action"):
            item[key] = bool(item[key])
        return item

    def sync_coc_profile(self, asset_id: str, reset_hp: bool = False) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        stats = self.list_character_stats(asset_id)
        values = {item["name"]: int(item["value"]) for item in stats if item["value"] is not None}
        derived = coc7_derived_values(values)
        has_coc_core = any("STR" in name or "CON" in name or "SIZ" in name for name in values)
        now = utc_now()
        if has_coc_core:
            with self._write_lock, self.connect() as db:
                facts = dict(asset["derived_facts"])
                for name, value in derived.items():
                    if name == "伤害加值 Damage Bonus" or not value:
                        continue
                    row = db.execute(
                        "SELECT id FROM character_stats WHERE asset_id=? AND name=?",
                        (asset_id, name),
                    ).fetchone()
                    if row and isinstance(value, int):
                        db.execute(
                            "UPDATE character_stats SET value=?,source='derived',"
                            "updated_at=? WHERE id=?",
                            (value, now, row["id"]),
                        )
                    facts[name] = value
                db.execute(
                    "UPDATE assets SET derived_facts_json=?,revision=revision+1,"
                    "updated_at=? WHERE id=?",
                    (canonical_json(facts), now, asset_id),
                )
            stats = self.list_character_stats(asset_id)
            values = {
                item["name"]: int(item["value"]) for item in stats if item["value"] is not None
            }
        max_hp = stat_value(values, "hp", int(derived.get("生命值 HP", 0) or 10)) or 10
        with self._write_lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM coc_combat_states WHERE asset_id=?", (asset_id,)
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO coc_combat_states VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (asset_id, max_hp, max_hp, 0, 0, 0, 0, 0, 0, now),
                )
            else:
                old = self._combat_state_from_row(row)
                current = (
                    max_hp
                    if reset_hp or old["current_hp"] == old["max_hp"]
                    else min(old["current_hp"], max_hp)
                )
                db.execute(
                    "UPDATE coc_combat_states SET max_hp=?,current_hp=?,major_wound=?,"
                    "unconscious=?,dying=?,dead=?,skip_next_action=?,updated_at=? WHERE asset_id=?",
                    (
                        max_hp,
                        current,
                        0 if reset_hp else int(old["major_wound"]),
                        0 if reset_hp else int(old["unconscious"]),
                        0 if reset_hp else int(old["dying"]),
                        0 if reset_hp else int(old["dead"]),
                        0 if reset_hp else int(old["skip_next_action"]),
                        now,
                        asset_id,
                    ),
                )
        return self.get_coc_profile(asset_id)

    def get_coc_profile(self, asset_id: str) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM coc_combat_states WHERE asset_id=?", (asset_id,)
            ).fetchone()
        if row is None:
            return self.sync_coc_profile(asset_id)
        stats = self.list_character_stats(asset_id)
        values = {item["name"]: int(item["value"]) for item in stats if item["value"] is not None}
        derived = coc7_derived_values(values)
        return {
            "asset_id": asset_id,
            "campaign_id": asset["campaign_id"],
            "name": asset["name"],
            "color": asset["color"],
            "dexterity": stat_value(values, "dex", 50),
            "constitution": stat_value(values, "con", 50),
            "dodge": stat_value(values, "dodge", stat_value(values, "dex", 50) // 2),
            "brawl": stat_value(values, "brawl", 25),
            "firearm": stat_value(values, "handgun", 20),
            "first_aid": stat_value(values, "first aid", 30),
            "build": int(derived.get("体格 Build", 0)),
            "damage_bonus": str(derived.get("伤害加值 Damage Bonus", "0")),
            "state": self._combat_state_from_row(row),
            "stats": stats,
        }

    def list_coc_profiles(self, campaign_id: str) -> list[dict[str, Any]]:
        profiles = [
            self.get_coc_profile(asset["id"])
            for asset in self.list_assets(campaign_id)
            if asset["kind"] == "character"
        ]
        return sorted(profiles, key=lambda item: (-item["dexterity"], item["name"]))

    def update_coc_state(self, asset_id: str, state: dict[str, Any]) -> dict[str, Any]:
        profile = self.get_coc_profile(asset_id)
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE coc_combat_states SET max_hp=?,current_hp=?,armor=?,major_wound=?,"
                "unconscious=?,dying=?,dead=?,updated_at=? WHERE asset_id=?",
                (
                    state["max_hp"],
                    state["current_hp"],
                    state.get("armor", 0),
                    int(state.get("major_wound", False)),
                    int(state.get("unconscious", False)),
                    int(state.get("dying", False)),
                    int(state.get("dead", False)),
                    now,
                    profile["asset_id"],
                ),
            )
        return self.get_coc_profile(asset_id)

    def apply_coc_combat_states(
        self,
        attacker_asset_id: str,
        attacker_state: dict[str, Any],
        defender_asset_id: str,
        defender_state: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._write_lock, self.connect() as db:
            for asset_id, state in (
                (attacker_asset_id, attacker_state),
                (defender_asset_id, defender_state),
            ):
                db.execute(
                    "UPDATE coc_combat_states SET max_hp=?,current_hp=?,armor=?,major_wound=?,"
                    "unconscious=?,dying=?,dead=?,skip_next_action=?,updated_at=? WHERE asset_id=?",
                    (
                        state["max_hp"],
                        state["current_hp"],
                        state.get("armor", 0),
                        int(state.get("major_wound", False)),
                        int(state.get("unconscious", False)),
                        int(state.get("dying", False)),
                        int(state.get("dead", False)),
                        int(state.get("skip_next_action", False)),
                        now,
                        asset_id,
                    ),
                )

    def get_or_create_coc_encounter(self, campaign_id: str) -> dict[str, Any]:
        self.get_campaign(campaign_id)
        with self._write_lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM coc_encounters WHERE campaign_id=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if row is None:
                encounter_id = str(uuid.uuid4())
                now = utc_now()
                db.execute(
                    "INSERT INTO coc_encounters VALUES(?,?,?,?,?,?,?)",
                    (encounter_id, campaign_id, "COC战斗", 1, "active", now, now),
                )
                row = db.execute(
                    "SELECT * FROM coc_encounters WHERE id=?", (encounter_id,)
                ).fetchone()
        return dict(row)

    def advance_coc_round(self, campaign_id: str) -> dict[str, Any]:
        encounter = self.get_or_create_coc_encounter(campaign_id)
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE coc_encounters SET round_number=round_number+1,updated_at=? WHERE id=?",
                (now, encounter["id"]),
            )
            db.execute(
                "UPDATE coc_combat_states SET skip_next_action=0,updated_at=? WHERE asset_id IN "
                "(SELECT id FROM assets WHERE campaign_id=?)",
                (now, campaign_id),
            )
            row = db.execute(
                "SELECT * FROM coc_encounters WHERE id=?", (encounter["id"],)
            ).fetchone()
        return dict(row)

    def get_coc_action_by_request(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM coc_actions WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["action"] = json.loads(item.pop("action_json"))
        item["result"] = json.loads(item.pop("result_json"))
        return item

    def record_coc_action(
        self,
        encounter_id: str,
        campaign_id: str,
        request_id: str,
        event_id: str,
        action: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.get_coc_action_by_request(request_id)
        if existing:
            return existing
        action_id = str(uuid.uuid4())
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO coc_actions VALUES(?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    encounter_id,
                    campaign_id,
                    request_id,
                    event_id,
                    canonical_json(action),
                    canonical_json(result),
                    utc_now(),
                ),
            )
        return self.get_coc_action_by_request(request_id)  # type: ignore[return-value]

    def create_character_trait(
        self,
        asset_id: str,
        label: str,
        description: str,
        source: str = "manual",
        source_event_id: str | None = None,
    ) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if asset["kind"] != "character":
            raise ValueError("资产不是人物")
        trait_id = str(uuid.uuid4())
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO character_traits VALUES(?,?,?,?,?,?,?)",
                (trait_id, asset_id, label, description, source, source_event_id, utc_now()),
            )
        return self.get_character_trait(trait_id)

    def get_character_trait(self, trait_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM character_traits WHERE id=?", (trait_id,)).fetchone()
        if row is None:
            raise KeyError("人物特质不存在")
        return dict(row)

    def list_character_traits(self, asset_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM character_traits WHERE asset_id=? ORDER BY created_at",
                (asset_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_completed_turn(self, campaign_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM director_turns WHERE campaign_id=? AND status='completed' "
                "ORDER BY updated_at DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
        if row is None:
            raise KeyError("还没有可生成记忆的完成回合")
        return self.get_turn(row["id"])

    def save_character_memories(
        self,
        campaign_id: str,
        turn_id: str,
        source_event_id: str | None,
        memories: list[dict[str, Any]],
        generated_by: str,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        with self._write_lock, self.connect() as db:
            for memory in memories:
                db.execute(
                    "INSERT INTO character_memories("
                    "id,campaign_id,owner_asset_id,involved_asset_ids_json,source_turn_id,"
                    "source_event_id,summary,interpretation,emotion,importance,status,"
                    "generated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(owner_asset_id,source_turn_id) DO UPDATE SET "
                    "involved_asset_ids_json=excluded.involved_asset_ids_json,"
                    "summary=excluded.summary,interpretation=excluded.interpretation,"
                    "emotion=excluded.emotion,importance=excluded.importance,"
                    "generated_by=excluded.generated_by,updated_at=excluded.updated_at",
                    (
                        str(uuid.uuid4()),
                        campaign_id,
                        memory["owner_asset_id"],
                        canonical_json(memory.get("involved_asset_ids", [])),
                        turn_id,
                        source_event_id,
                        memory["summary"],
                        memory.get("interpretation", ""),
                        memory.get("emotion", ""),
                        memory.get("importance", 3),
                        "active",
                        generated_by,
                        now,
                        now,
                    ),
                )
        return self.list_character_memories(campaign_id)

    def _memory_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["involved_asset_ids"] = json.loads(item.pop("involved_asset_ids_json"))
        return item

    def list_character_memories(
        self, campaign_id: str, owner_asset_id: str | None = None, limit: int = 300
    ) -> list[dict[str, Any]]:
        where = "campaign_id=?"
        params: list[Any] = [campaign_id]
        if owner_asset_id:
            where += " AND owner_asset_id=?"
            params.append(owner_asset_id)
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM character_memories WHERE {where} AND status='active' "
                "ORDER BY importance DESC,created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def memories_for_assets(
        self, campaign_id: str, asset_ids: list[str], per_character: int = 8
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for asset_id in asset_ids:
            result.extend(self.list_character_memories(campaign_id, asset_id, per_character))
        return result

    # World graph and Git-like turn history

    @staticmethod
    def _plain_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def build_world_snapshot(self, campaign_id: str) -> dict[str, Any]:
        """Capture the complete materialized workspace for an immutable commit."""
        self.get_campaign(campaign_id)
        with self.connect() as db:
            assets = self._plain_rows(
                db.execute(
                    "SELECT * FROM assets WHERE campaign_id=? ORDER BY created_at", (campaign_id,)
                ).fetchall()
            )
            for asset in assets:
                asset["derived_facts"] = json.loads(asset.pop("derived_facts_json"))
                asset["archived"] = bool(asset["archived"])
            asset_ids = [asset["id"] for asset in assets]
            if asset_ids:
                placeholders = ",".join("?" for _ in asset_ids)
                stats = self._plain_rows(
                    db.execute(
                        f"SELECT * FROM character_stats WHERE asset_id IN ({placeholders}) "
                        "ORDER BY asset_id,position,created_at",
                        asset_ids,
                    ).fetchall()
                )
                traits = self._plain_rows(
                    db.execute(
                        f"SELECT * FROM character_traits WHERE asset_id IN ({placeholders}) "
                        "ORDER BY asset_id,created_at",
                        asset_ids,
                    ).fetchall()
                )
                coc_states = self._plain_rows(
                    db.execute(
                        f"SELECT * FROM coc_combat_states WHERE asset_id IN ({placeholders})",
                        asset_ids,
                    ).fetchall()
                )
            else:
                stats, traits, coc_states = [], [], []
            memories = self._plain_rows(
                db.execute(
                    "SELECT * FROM character_memories WHERE campaign_id=? ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
            )
            for memory in memories:
                memory["involved_asset_ids"] = json.loads(memory.pop("involved_asset_ids_json"))
            relations = self._plain_rows(
                db.execute(
                    "SELECT * FROM world_relations WHERE campaign_id=? ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
            )
            status_cards = self._plain_rows(
                db.execute(
                    "SELECT * FROM status_cards WHERE campaign_id=? ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
            )
            outcomes = self._plain_rows(
                db.execute(
                    "SELECT * FROM turn_outcomes WHERE campaign_id=? "
                    "ORDER BY turn_number,created_at",
                    (campaign_id,),
                ).fetchall()
            )
            outcome_ids = [outcome["id"] for outcome in outcomes]
            if outcome_ids:
                placeholders = ",".join("?" for _ in outcome_ids)
                outcome_asset_links = self._plain_rows(
                    db.execute(
                        f"SELECT * FROM outcome_asset_links WHERE outcome_id IN ({placeholders})",
                        outcome_ids,
                    ).fetchall()
                )
                outcome_links = self._plain_rows(
                    db.execute(
                        f"SELECT * FROM outcome_links WHERE source_outcome_id IN ({placeholders}) "
                        f"AND target_outcome_id IN ({placeholders})",
                        [*outcome_ids, *outcome_ids],
                    ).fetchall()
                )
            else:
                outcome_asset_links, outcome_links = [], []
            view = db.execute(
                "SELECT pan_x,pan_y,zoom FROM campaign_view_state WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
        return {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "assets": assets,
            "character_stats": stats,
            "character_traits": traits,
            "character_memories": memories,
            "coc_combat_states": coc_states,
            "relations": relations,
            "status_cards": status_cards,
            "outcomes": outcomes,
            "outcome_asset_links": outcome_asset_links,
            "outcome_links": outcome_links,
            "canvas_view": dict(view) if view else {"pan_x": 0.0, "pan_y": 0.0, "zoom": 1.0},
        }

    def ensure_timeline(self, campaign_id: str) -> dict[str, Any]:
        self.get_campaign(campaign_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT b.* FROM campaign_timeline_state s JOIN timeline_branches b "
                "ON b.id=s.active_branch_id WHERE s.campaign_id=?",
                (campaign_id,),
            ).fetchone()
        if row:
            return dict(row)
        branch_id = str(uuid.uuid4())
        commit_id = str(uuid.uuid4())
        now = utc_now()
        snapshot = self.build_world_snapshot(campaign_id)
        snapshot_json = canonical_json(snapshot)
        snapshot_hash = self._world_commit_hash(snapshot_json, None)
        with self._write_lock, self.connect() as db:
            existing = db.execute(
                "SELECT b.* FROM campaign_timeline_state s JOIN timeline_branches b "
                "ON b.id=s.active_branch_id WHERE s.campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                "INSERT INTO timeline_branches VALUES(?,?,?,?,?,?,?)",
                (branch_id, campaign_id, "主线", commit_id, commit_id, now, now),
            )
            db.execute(
                "INSERT INTO campaign_timeline_state VALUES(?,?,?)",
                (campaign_id, branch_id, now),
            )
            db.execute(
                "INSERT INTO world_commits("
                "id,campaign_id,branch_id,parent_commit_id,director_turn_id,turn_number,kind,"
                "summary,snapshot_json,delta_json,event_sequence,snapshot_hash,quality,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    commit_id,
                    campaign_id,
                    branch_id,
                    None,
                    None,
                    0,
                    "baseline",
                    "导入基线",
                    snapshot_json,
                    "{}",
                    0,
                    snapshot_hash,
                    "full",
                    now,
                ),
            )
            db.execute(
                "INSERT INTO branch_workspaces VALUES(?,?,?)",
                (branch_id, snapshot_json, now),
            )
            db.execute(
                "UPDATE director_turns SET branch_id=? WHERE campaign_id=? AND branch_id IS NULL",
                (branch_id, campaign_id),
            )
        return self.get_active_branch(campaign_id)

    def get_active_branch(self, campaign_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT b.* FROM campaign_timeline_state s JOIN timeline_branches b "
                "ON b.id=s.active_branch_id WHERE s.campaign_id=?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return self.ensure_timeline(campaign_id)
        return dict(row)

    def get_branch(self, branch_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM timeline_branches WHERE id=?", (branch_id,)).fetchone()
        if row is None:
            raise KeyError("时间线分支不存在")
        return dict(row)

    def get_commit(self, commit_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM world_commits WHERE id=?", (commit_id,)).fetchone()
        if row is None:
            raise KeyError("回合提交不存在")
        item = dict(row)
        item["snapshot"] = json.loads(item.pop("snapshot_json"))
        item["delta"] = json.loads(item.pop("delta_json"))
        return item

    def get_timeline(self, campaign_id: str) -> dict[str, Any]:
        active = self.get_active_branch(campaign_id)
        with self.connect() as db:
            branches = self._plain_rows(
                db.execute(
                    "SELECT * FROM timeline_branches WHERE campaign_id=? ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
            )
            commits = self._plain_rows(
                db.execute(
                    "SELECT id,campaign_id,branch_id,parent_commit_id,director_turn_id,turn_number,"
                    "kind,summary,event_sequence,snapshot_hash,quality,created_at "
                    "FROM world_commits WHERE campaign_id=? ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
            )
            legacy_turns = self._plain_rows(
                db.execute(
                    "SELECT id,instruction,created_at FROM director_turns WHERE campaign_id=? "
                    "AND status='completed' AND commit_id IS NULL ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
            )
        return {
            "active_branch_id": active["id"],
            "branches": branches,
            "commits": commits,
            "legacy_turns": legacy_turns,
            "history_verified": self.verify_world_history(campaign_id),
        }

    @staticmethod
    def _world_commit_hash(snapshot_json: str, parent_hash: str | None) -> str:
        payload = f"anko-world-v1\n{parent_hash or ''}\n{snapshot_json}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def verify_world_history(self, campaign_id: str) -> bool:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,parent_commit_id,snapshot_json,snapshot_hash FROM world_commits "
                "WHERE campaign_id=?",
                (campaign_id,),
            ).fetchall()
        hashes = {row["id"]: row["snapshot_hash"] for row in rows}
        for row in rows:
            parent_hash = hashes.get(row["parent_commit_id"])
            if row["parent_commit_id"] and parent_hash is None:
                return False
            expected = self._world_commit_hash(row["snapshot_json"], parent_hash)
            legacy = hashlib.sha256(row["snapshot_json"].encode()).hexdigest()
            if row["snapshot_hash"] not in {expected, legacy}:
                return False
        return True

    def get_world_at_commit(self, campaign_id: str, commit_id: str) -> dict[str, Any]:
        commit = self.get_commit(commit_id)
        if commit["campaign_id"] != campaign_id:
            raise ValueError("回合提交不属于当前存档")
        return {
            "commit": {key: value for key, value in commit.items() if key != "snapshot"},
            "world": commit["snapshot"],
        }

    def save_active_workspace(self, campaign_id: str) -> dict[str, Any]:
        branch = self.get_active_branch(campaign_id)
        snapshot = self.build_world_snapshot(campaign_id)
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO branch_workspaces VALUES(?,?,?) ON CONFLICT(branch_id) DO UPDATE SET "
                "snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at",
                (branch["id"], canonical_json(snapshot), now),
            )
        return snapshot

    def _materialize_world(self, campaign_id: str, snapshot: dict[str, Any]) -> None:
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute("UPDATE assets SET archived=1 WHERE campaign_id=?", (campaign_id,))
            for asset in snapshot.get("assets", []):
                db.execute(
                    "INSERT INTO assets("
                    "id,campaign_id,kind,name,color,summary,body,derived_facts_json,x,y,revision,"
                    "archived,legacy_character_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,"
                    "color=excluded.color,summary=excluded.summary,body=excluded.body,"
                    "derived_facts_json=excluded.derived_facts_json,x=excluded.x,y=excluded.y,"
                    "revision=excluded.revision,archived=excluded.archived,updated_at=excluded.updated_at",
                    (
                        asset["id"],
                        campaign_id,
                        asset["kind"],
                        asset["name"],
                        asset["color"],
                        asset.get("summary", ""),
                        asset.get("body", ""),
                        canonical_json(asset.get("derived_facts", {})),
                        asset.get("x", 0),
                        asset.get("y", 0),
                        asset.get("revision", 1),
                        int(asset.get("archived", False)),
                        asset.get("legacy_character_id"),
                        asset.get("created_at", now),
                        asset.get("updated_at", now),
                    ),
                )
            asset_ids = [
                row["id"]
                for row in db.execute("SELECT id FROM assets WHERE campaign_id=?", (campaign_id,))
            ]
            if asset_ids:
                placeholders = ",".join("?" for _ in asset_ids)
                db.execute(
                    f"DELETE FROM character_stats WHERE asset_id IN ({placeholders})", asset_ids
                )
                db.execute(
                    f"DELETE FROM character_traits WHERE asset_id IN ({placeholders})", asset_ids
                )
                db.execute(
                    f"DELETE FROM coc_combat_states WHERE asset_id IN ({placeholders})", asset_ids
                )
            for stat in snapshot.get("character_stats", []):
                db.execute(
                    "INSERT INTO character_stats VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        stat[key]
                        for key in (
                            "id",
                            "asset_id",
                            "name",
                            "notation",
                            "value",
                            "source",
                            "source_event_id",
                            "locked",
                            "position",
                            "created_at",
                            "updated_at",
                        )
                    ),
                )
            for trait in snapshot.get("character_traits", []):
                db.execute(
                    "INSERT INTO character_traits VALUES(?,?,?,?,?,?,?)",
                    tuple(
                        trait[key]
                        for key in (
                            "id",
                            "asset_id",
                            "label",
                            "description",
                            "source",
                            "source_event_id",
                            "created_at",
                        )
                    ),
                )
            for state in snapshot.get("coc_combat_states", []):
                db.execute(
                    "INSERT INTO coc_combat_states VALUES(?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        state[key]
                        for key in (
                            "asset_id",
                            "max_hp",
                            "current_hp",
                            "armor",
                            "major_wound",
                            "unconscious",
                            "dying",
                            "dead",
                            "skip_next_action",
                            "updated_at",
                        )
                    ),
                )
            db.execute("DELETE FROM character_memories WHERE campaign_id=?", (campaign_id,))
            for memory in snapshot.get("character_memories", []):
                db.execute(
                    "INSERT INTO character_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        memory["id"],
                        campaign_id,
                        memory["owner_asset_id"],
                        canonical_json(memory.get("involved_asset_ids", [])),
                        memory["source_turn_id"],
                        memory.get("source_event_id"),
                        memory["summary"],
                        memory.get("interpretation", ""),
                        memory.get("emotion", ""),
                        memory.get("importance", 3),
                        memory.get("status", "active"),
                        memory.get("generated_by", "migration"),
                        memory.get("created_at", now),
                        memory.get("updated_at", now),
                    ),
                )
            db.execute("DELETE FROM world_relations WHERE campaign_id=?", (campaign_id,))
            for relation in snapshot.get("relations", []):
                db.execute(
                    "INSERT INTO world_relations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        relation.get(key)
                        for key in (
                            "id",
                            "campaign_id",
                            "source_asset_id",
                            "target_asset_id",
                            "label",
                            "description",
                            "source_turn_id",
                            "ended_turn_id",
                            "created_at",
                            "updated_at",
                        )
                    ),
                )
            db.execute("DELETE FROM status_cards WHERE campaign_id=?", (campaign_id,))
            for card in snapshot.get("status_cards", []):
                db.execute(
                    "INSERT INTO status_cards VALUES(?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        card.get(key)
                        for key in (
                            "id",
                            "campaign_id",
                            "target_asset_id",
                            "duration_turns",
                            "remaining_turns",
                            "description",
                            "source_turn_id",
                            "ended_turn_id",
                            "created_at",
                            "updated_at",
                        )
                    ),
                )
            db.execute("DELETE FROM turn_outcomes WHERE campaign_id=?", (campaign_id,))
            for outcome in snapshot.get("outcomes", []):
                db.execute(
                    "INSERT INTO turn_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        outcome.get(key)
                        for key in (
                            "id",
                            "campaign_id",
                            "branch_id",
                            "turn_id",
                            "turn_number",
                            "title",
                            "summary",
                            "x",
                            "y",
                            "analysis_status",
                            "created_at",
                        )
                    ),
                )
            for link in snapshot.get("outcome_asset_links", []):
                db.execute(
                    "INSERT OR IGNORE INTO outcome_asset_links VALUES(?,?,?)",
                    (link["outcome_id"], link["asset_id"], link.get("label", "参与")),
                )
            for link in snapshot.get("outcome_links", []):
                db.execute(
                    "INSERT OR IGNORE INTO outcome_links VALUES(?,?,?)",
                    (
                        link["source_outcome_id"],
                        link["target_outcome_id"],
                        link.get("label", "导致"),
                    ),
                )
            view = snapshot.get("canvas_view", {})
            db.execute(
                "INSERT INTO campaign_view_state VALUES(?,?,?,?,?) "
                "ON CONFLICT(campaign_id) DO UPDATE SET "
                "pan_x=excluded.pan_x,pan_y=excluded.pan_y,zoom=excluded.zoom,updated_at=excluded.updated_at",
                (campaign_id, view.get("pan_x", 0), view.get("pan_y", 0), view.get("zoom", 1), now),
            )

    def activate_branch(self, branch_id: str) -> dict[str, Any]:
        branch = self.get_branch(branch_id)
        campaign_id = branch["campaign_id"]
        current = self.get_active_branch(campaign_id)
        if current["id"] == branch_id:
            return branch
        self.save_active_workspace(campaign_id)
        with self.connect() as db:
            workspace = db.execute(
                "SELECT snapshot_json FROM branch_workspaces WHERE branch_id=?", (branch_id,)
            ).fetchone()
        snapshot = (
            json.loads(workspace["snapshot_json"])
            if workspace
            else self.get_commit(branch["head_commit_id"])["snapshot"]
        )
        self._materialize_world(campaign_id, snapshot)
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE campaign_timeline_state SET active_branch_id=?,updated_at=? "
                "WHERE campaign_id=?",
                (branch_id, utc_now(), campaign_id),
            )
        return self.get_branch(branch_id)

    def create_branch(
        self, campaign_id: str, from_commit_id: str | None = None, name: str | None = None
    ) -> dict[str, Any]:
        active = self.get_active_branch(campaign_id)
        self.save_active_workspace(campaign_id)
        commit_id = from_commit_id or active["head_commit_id"]
        commit = self.get_commit(commit_id)
        if commit["campaign_id"] != campaign_id or commit["quality"] != "full":
            raise ValueError("只能从当前存档的完整回合提交建立分支")
        branch_id = str(uuid.uuid4())
        now = utc_now()
        default_name = f"第 {commit['turn_number']} 回支线"
        branch_name = (name or default_name).strip()
        with self._write_lock, self.connect() as db:
            existing_names = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM timeline_branches WHERE campaign_id=?", (campaign_id,)
                ).fetchall()
            }
            if branch_name in existing_names:
                index = 2
                while f"{branch_name} {index}" in existing_names:
                    index += 1
                branch_name = f"{branch_name} {index}"
            db.execute(
                "INSERT INTO timeline_branches VALUES(?,?,?,?,?,?,?)",
                (branch_id, campaign_id, branch_name, commit_id, commit_id, now, now),
            )
            db.execute(
                "INSERT INTO branch_workspaces VALUES(?,?,?)",
                (branch_id, canonical_json(commit["snapshot"]), now),
            )
        return self.activate_branch(branch_id)

    def resolve_historical_references(
        self, campaign_id: str, references: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for reference in references:
            commit = self.get_commit(reference["commit_id"])
            if commit["campaign_id"] != campaign_id:
                raise ValueError("历史引用不属于当前存档")
            key = {
                "asset": "assets",
                "status_card": "status_cards",
                "relation": "relations",
                "outcome": "outcomes",
            }[reference["entity_type"]]
            entity = next(
                (
                    item
                    for item in commit["snapshot"].get(key, [])
                    if item["id"] == reference["entity_id"]
                ),
                None,
            )
            if entity is None:
                raise ValueError("历史提交中不存在所引用的内容")
            resolved.append(
                {
                    "commit_id": commit["id"],
                    "branch_id": commit["branch_id"],
                    "turn_number": commit["turn_number"],
                    "entity_type": reference["entity_type"],
                    "entity": entity,
                }
            )
        return resolved

    def list_status_cards(self, campaign_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        where = "campaign_id=?" + (" AND ended_turn_id IS NULL" if active_only else "")
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM status_cards WHERE {where} ORDER BY created_at", (campaign_id,)
            ).fetchall()
        return self._plain_rows(rows)

    def get_status_card(self, status_card_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM status_cards WHERE id=?", (status_card_id,)).fetchone()
        if row is None:
            raise KeyError("状态卡不存在")
        return dict(row)

    def create_status_card(
        self,
        campaign_id: str,
        target_asset_id: str,
        duration_turns: int | None,
        description: str,
        source_turn_id: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        asset = self.get_asset(target_asset_id)
        if asset["campaign_id"] != campaign_id or asset["kind"] != "character" or asset["archived"]:
            raise ValueError("状态卡对象必须是当前存档的有效人物")
        if duration_turns is not None and duration_turns < 1:
            raise ValueError("持续时间必须是正整数或永久")
        card_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO status_cards VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    card_id,
                    campaign_id,
                    target_asset_id,
                    duration_turns,
                    duration_turns,
                    description.strip(),
                    source_turn_id,
                    None,
                    now,
                    now,
                ),
            )
        if checkpoint:
            self.create_world_commit(
                campaign_id, None, "correction", f"添加状态：{description[:40]}"
            )
        return self.get_status_card(card_id)

    def update_status_card(
        self,
        status_card_id: str,
        target_asset_id: str,
        duration_turns: int | None,
        description: str,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        card = self.get_status_card(status_card_id)
        if card["ended_turn_id"]:
            raise ValueError("已经结束的状态卡不能修改")
        asset = self.get_asset(target_asset_id)
        if asset["campaign_id"] != card["campaign_id"] or asset["kind"] != "character":
            raise ValueError("状态卡对象必须是同一存档的人物")
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE status_cards SET target_asset_id=?,duration_turns=?,remaining_turns=?,"
                "description=?,updated_at=? WHERE id=?",
                (
                    target_asset_id,
                    duration_turns,
                    duration_turns,
                    description.strip(),
                    utc_now(),
                    status_card_id,
                ),
            )
        if checkpoint:
            self.create_world_commit(
                card["campaign_id"], None, "correction", f"修改状态：{description[:40]}"
            )
        return self.get_status_card(status_card_id)

    def end_status_card(
        self, status_card_id: str, turn_id: str | None = None, checkpoint: bool = True
    ) -> dict[str, Any]:
        card = self.get_status_card(status_card_id)
        if card["ended_turn_id"] is None:
            with self._write_lock, self.connect() as db:
                if turn_id:
                    db.execute(
                        "UPDATE status_cards SET ended_turn_id=?,updated_at=? WHERE id=?",
                        (turn_id, utc_now(), status_card_id),
                    )
                else:
                    db.execute("DELETE FROM status_cards WHERE id=?", (status_card_id,))
        if checkpoint:
            self.create_world_commit(
                card["campaign_id"], None, "correction", f"移除状态：{card['description'][:40]}"
            )
        return {**card, "ended_turn_id": turn_id or "manual"}

    def expire_status_cards(self, campaign_id: str, turn_id: str) -> None:
        with self._write_lock, self.connect() as db:
            rows = db.execute(
                "SELECT id,remaining_turns FROM status_cards WHERE campaign_id=? "
                "AND ended_turn_id IS NULL AND remaining_turns IS NOT NULL",
                (campaign_id,),
            ).fetchall()
            for row in rows:
                remaining = row["remaining_turns"] - 1
                db.execute(
                    "UPDATE status_cards SET remaining_turns=?,ended_turn_id=?,updated_at=? "
                    "WHERE id=?",
                    (max(remaining, 0), turn_id if remaining <= 0 else None, utc_now(), row["id"]),
                )

    def list_relations(self, campaign_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        where = "campaign_id=?" + (" AND ended_turn_id IS NULL" if active_only else "")
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM world_relations WHERE {where} ORDER BY created_at", (campaign_id,)
            ).fetchall()
        return self._plain_rows(rows)

    def get_relation(self, relation_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM world_relations WHERE id=?", (relation_id,)).fetchone()
        if row is None:
            raise KeyError("关系不存在")
        return dict(row)

    def create_relation(
        self,
        campaign_id: str,
        source_asset_id: str,
        target_asset_id: str,
        label: str,
        description: str = "",
        source_turn_id: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        source = self.get_asset(source_asset_id)
        target = self.get_asset(target_asset_id)
        if source_asset_id == target_asset_id or any(
            asset["campaign_id"] != campaign_id or asset["archived"] for asset in (source, target)
        ):
            raise ValueError("关系两端必须是当前存档中不同的有效资产")
        relation_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO world_relations VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    relation_id,
                    campaign_id,
                    source_asset_id,
                    target_asset_id,
                    label.strip(),
                    description.strip(),
                    source_turn_id,
                    None,
                    now,
                    now,
                ),
            )
        if checkpoint:
            self.create_world_commit(campaign_id, None, "correction", f"建立关系：{label[:40]}")
        return self.get_relation(relation_id)

    def update_relation(
        self,
        relation_id: str,
        source_asset_id: str,
        target_asset_id: str,
        label: str,
        description: str = "",
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        relation = self.get_relation(relation_id)
        if relation["ended_turn_id"]:
            raise ValueError("已经结束的关系不能修改")
        source = self.get_asset(source_asset_id)
        target = self.get_asset(target_asset_id)
        if source_asset_id == target_asset_id or any(
            asset["campaign_id"] != relation["campaign_id"] for asset in (source, target)
        ):
            raise ValueError("关系两端必须属于当前存档且不能相同")
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE world_relations SET source_asset_id=?,target_asset_id=?,label=?,"
                "description=?,updated_at=? WHERE id=?",
                (
                    source_asset_id,
                    target_asset_id,
                    label.strip(),
                    description.strip(),
                    utc_now(),
                    relation_id,
                ),
            )
        if checkpoint:
            self.create_world_commit(
                relation["campaign_id"], None, "correction", f"修改关系：{label[:40]}"
            )
        return self.get_relation(relation_id)

    def end_relation(
        self, relation_id: str, turn_id: str | None = None, checkpoint: bool = True
    ) -> dict[str, Any]:
        relation = self.get_relation(relation_id)
        if relation["ended_turn_id"] is None:
            with self._write_lock, self.connect() as db:
                if turn_id:
                    db.execute(
                        "UPDATE world_relations SET ended_turn_id=?,updated_at=? WHERE id=?",
                        (turn_id, utc_now(), relation_id),
                    )
                else:
                    db.execute("DELETE FROM world_relations WHERE id=?", (relation_id,))
        if checkpoint:
            self.create_world_commit(
                relation["campaign_id"], None, "correction", f"结束关系：{relation['label'][:40]}"
            )
        return {**relation, "ended_turn_id": turn_id or "manual"}

    def next_turn_number(self, branch_id: str) -> int:
        branch = self.get_branch(branch_id)
        if not branch["head_commit_id"]:
            return 1
        return self.get_commit(branch["head_commit_id"])["turn_number"] + 1

    def list_outcomes(
        self, campaign_id: str, branch_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            if branch_id:
                rows = db.execute(
                    "SELECT * FROM turn_outcomes WHERE campaign_id=? AND branch_id=? "
                    "ORDER BY turn_number DESC,created_at DESC LIMIT ?",
                    (campaign_id, branch_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM turn_outcomes WHERE campaign_id=? "
                    "ORDER BY turn_number DESC,created_at DESC LIMIT ?",
                    (campaign_id, limit),
                ).fetchall()
            result = []
            for row in reversed(rows):
                item = dict(row)
                item["participant_asset_ids"] = [
                    link["asset_id"]
                    for link in db.execute(
                        "SELECT asset_id FROM outcome_asset_links WHERE outcome_id=?",
                        (item["id"],),
                    ).fetchall()
                ]
                item["previous_outcome_ids"] = [
                    link["source_outcome_id"]
                    for link in db.execute(
                        "SELECT source_outcome_id FROM outcome_links WHERE target_outcome_id=?",
                        (item["id"],),
                    ).fetchall()
                ]
                result.append(item)
        return result

    def create_turn_outcome(
        self,
        turn_id: str,
        title: str,
        summary: str,
        participant_asset_ids: list[str],
        previous_outcome_ids: list[str],
        analysis_status: str = "complete",
    ) -> dict[str, Any]:
        turn = self.get_turn(turn_id)
        with self.connect() as db:
            existing = db.execute(
                "SELECT * FROM turn_outcomes WHERE turn_id=?", (turn_id,)
            ).fetchone()
        if existing:
            outcome_id = existing["id"]
            with self._write_lock, self.connect() as db:
                db.execute(
                    "UPDATE turn_outcomes SET title=?,summary=?,analysis_status=? WHERE id=?",
                    (title, summary, analysis_status, outcome_id),
                )
                db.execute("DELETE FROM outcome_asset_links WHERE outcome_id=?", (outcome_id,))
                db.execute("DELETE FROM outcome_links WHERE target_outcome_id=?", (outcome_id,))
                for asset_id in dict.fromkeys(participant_asset_ids):
                    asset = self.get_asset(asset_id)
                    if asset["campaign_id"] != turn["campaign_id"]:
                        raise ValueError("事件结果引用了其他存档的资产")
                    db.execute(
                        "INSERT INTO outcome_asset_links VALUES(?,?,?)",
                        (outcome_id, asset_id, "参与"),
                    )
                for previous_id in dict.fromkeys(previous_outcome_ids):
                    previous = db.execute(
                        "SELECT id FROM turn_outcomes WHERE id=? AND campaign_id=?",
                        (previous_id, turn["campaign_id"]),
                    ).fetchone()
                    if previous:
                        db.execute(
                            "INSERT INTO outcome_links VALUES(?,?,?)",
                            (previous_id, outcome_id, "导致"),
                        )
            branch_id = turn["branch_id"] or self.get_active_branch(turn["campaign_id"])["id"]
            return next(
                item
                for item in self.list_outcomes(turn["campaign_id"], branch_id)
                if item["id"] == outcome_id
            )
        branch_id = turn["branch_id"] or self.get_active_branch(turn["campaign_id"])["id"]
        turn_number = self.next_turn_number(branch_id)
        assets = [self.get_asset(asset_id) for asset_id in participant_asset_ids]
        if any(asset["campaign_id"] != turn["campaign_id"] for asset in assets):
            raise ValueError("事件结果引用了其他存档的资产")
        x = sum(asset["x"] for asset in assets) / len(assets) + 220 if assets else 220
        y = sum(asset["y"] for asset in assets) / len(assets) if assets else 0
        outcome_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO turn_outcomes("
                "id,campaign_id,branch_id,turn_id,turn_number,title,summary,x,y,"
                "analysis_status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    outcome_id,
                    turn["campaign_id"],
                    branch_id,
                    turn_id,
                    turn_number,
                    title,
                    summary,
                    x,
                    y,
                    analysis_status,
                    now,
                ),
            )
            for asset_id in dict.fromkeys(participant_asset_ids):
                db.execute(
                    "INSERT OR IGNORE INTO outcome_asset_links VALUES(?,?,?)",
                    (outcome_id, asset_id, "参与"),
                )
            for previous_id in dict.fromkeys(previous_outcome_ids):
                previous = db.execute(
                    "SELECT id FROM turn_outcomes WHERE id=? AND campaign_id=?",
                    (previous_id, turn["campaign_id"]),
                ).fetchone()
                if previous:
                    db.execute(
                        "INSERT OR IGNORE INTO outcome_links VALUES(?,?,?)",
                        (previous_id, outcome_id, "导致"),
                    )
        return next(
            item
            for item in self.list_outcomes(turn["campaign_id"], branch_id)
            if item["id"] == outcome_id
        )

    def save_world_analysis(
        self,
        turn_id: str,
        status: str,
        delta: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO turn_world_analysis VALUES(?,?,?,?,?) "
                "ON CONFLICT(turn_id) DO UPDATE SET "
                "delta_json=excluded.delta_json,status=excluded.status,error=excluded.error,"
                "updated_at=excluded.updated_at",
                (
                    turn_id,
                    canonical_json(delta) if delta is not None else None,
                    status,
                    error,
                    utc_now(),
                ),
            )
            db.execute(
                "UPDATE director_turns SET world_delta_status=?,updated_at=? WHERE id=?",
                (status, utc_now(), turn_id),
            )

    def set_memory_status(self, turn_id: str, status: str) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "UPDATE director_turns SET memory_status=?,updated_at=? WHERE id=?",
                (status, utc_now(), turn_id),
            )

    def get_world_analysis(self, turn_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM turn_world_analysis WHERE turn_id=?", (turn_id,)
            ).fetchone()
        if row is None:
            raise KeyError("这个回合还没有世界变化分析")
        item = dict(row)
        item["delta"] = json.loads(item.pop("delta_json")) if item["delta_json"] else None
        item.pop("delta_json", None)
        return item

    def apply_world_delta(
        self, turn_id: str, delta: dict[str, Any], allowed_asset_ids: list[str]
    ) -> dict[str, Any]:
        turn = self.get_turn(turn_id)
        allowed = set(allowed_asset_ids)
        active_cards = {card["id"]: card for card in self.list_status_cards(turn["campaign_id"])}
        active_relations = {rel["id"]: rel for rel in self.list_relations(turn["campaign_id"])}
        for operation in delta.get("status_operations", []):
            if (
                operation["action"] in {"create", "update"}
                and operation.get("target_asset_id") not in allowed
            ):
                raise ValueError("AI 试图修改未引用人物的状态卡")
            if operation["action"] in {"update", "delete"}:
                card = active_cards.get(operation.get("status_card_id"))
                if card is None or card["target_asset_id"] not in allowed:
                    raise ValueError("AI 试图修改不存在或未授权的状态卡")
        for operation in delta.get("relation_operations", []):
            if operation["action"] in {"create", "update"} and not {
                operation.get("source_asset_id"),
                operation.get("target_asset_id"),
            }.issubset(allowed):
                raise ValueError("AI 试图修改未引用资产之间的关系")
            if operation["action"] in {"update", "delete"}:
                relation = active_relations.get(operation.get("relation_id"))
                if relation is None or not {
                    relation["source_asset_id"],
                    relation["target_asset_id"],
                }.issubset(allowed):
                    raise ValueError("AI 试图修改不存在或未授权的关系")
        changed_cards: list[dict[str, Any]] = []
        changed_relations: list[dict[str, Any]] = []
        for operation in delta.get("status_operations", []):
            if operation["action"] == "create":
                changed_cards.append(
                    self.create_status_card(
                        turn["campaign_id"],
                        operation["target_asset_id"],
                        None if operation.get("permanent") else operation.get("duration_turns"),
                        operation["description"],
                        turn_id,
                        False,
                    )
                )
            elif operation["action"] == "update":
                changed_cards.append(
                    self.update_status_card(
                        operation["status_card_id"],
                        operation["target_asset_id"],
                        None if operation.get("permanent") else operation.get("duration_turns"),
                        operation["description"],
                        False,
                    )
                )
            else:
                changed_cards.append(
                    self.end_status_card(operation["status_card_id"], turn_id, False)
                )
        for operation in delta.get("relation_operations", []):
            if operation["action"] == "create":
                changed_relations.append(
                    self.create_relation(
                        turn["campaign_id"],
                        operation["source_asset_id"],
                        operation["target_asset_id"],
                        operation["label"],
                        operation.get("description", ""),
                        turn_id,
                        False,
                    )
                )
            elif operation["action"] == "update":
                changed_relations.append(
                    self.update_relation(
                        operation["relation_id"],
                        operation["source_asset_id"],
                        operation["target_asset_id"],
                        operation["label"],
                        operation.get("description", ""),
                        False,
                    )
                )
            else:
                changed_relations.append(
                    self.end_relation(operation["relation_id"], turn_id, False)
                )
        outcome_data = delta["outcome"]
        participants = [
            asset_id
            for asset_id in outcome_data.get("participant_asset_ids", [])
            if asset_id in allowed
        ]
        outcome = self.create_turn_outcome(
            turn_id,
            outcome_data["title"],
            outcome_data["summary"],
            participants,
            outcome_data.get("previous_outcome_ids", []),
        )
        self.save_world_analysis(turn_id, "complete", delta)
        return {"status_cards": changed_cards, "relations": changed_relations, "outcome": outcome}

    def create_world_commit(
        self,
        campaign_id: str,
        turn_id: str | None,
        kind: str,
        summary: str,
        delta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        turn = self.get_turn(turn_id) if turn_id else None
        branch = (
            self.get_branch(turn["branch_id"])
            if turn and turn.get("branch_id")
            else self.get_active_branch(campaign_id)
        )
        with self.connect() as db:
            existing = (
                db.execute(
                    "SELECT id FROM world_commits WHERE director_turn_id=?", (turn_id,)
                ).fetchone()
                if turn_id and kind == "turn"
                else None
            )
        if existing:
            return self.get_commit(existing["id"])
        parent_id = branch["head_commit_id"]
        parent = self.get_commit(parent_id) if parent_id else None
        parent_number = parent["turn_number"] if parent else 0
        turn_number = parent_number + 1 if kind == "turn" else parent_number
        snapshot = self.build_world_snapshot(campaign_id)
        snapshot_json = canonical_json(snapshot)
        commit_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as db:
            last_event = db.execute(
                "SELECT COALESCE(MAX(sequence),0) AS sequence FROM events WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()["sequence"]
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO world_commits("
                "id,campaign_id,branch_id,parent_commit_id,director_turn_id,turn_number,kind,"
                "summary,snapshot_json,delta_json,event_sequence,snapshot_hash,quality,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    commit_id,
                    campaign_id,
                    branch["id"],
                    parent_id,
                    turn_id,
                    turn_number,
                    kind,
                    summary.strip()[:500],
                    snapshot_json,
                    canonical_json(delta or {}),
                    last_event,
                    self._world_commit_hash(
                        snapshot_json, parent["snapshot_hash"] if parent else None
                    ),
                    "full",
                    now,
                ),
            )
            db.execute(
                "UPDATE timeline_branches SET head_commit_id=?,updated_at=? WHERE id=?",
                (commit_id, now, branch["id"]),
            )
            db.execute(
                "INSERT INTO branch_workspaces VALUES(?,?,?) ON CONFLICT(branch_id) DO UPDATE SET "
                "snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at",
                (branch["id"], snapshot_json, now),
            )
            if turn_id:
                db.execute(
                    "UPDATE director_turns SET commit_id=?,updated_at=? WHERE id=?",
                    (commit_id, now, turn_id),
                )
        return self.get_commit(commit_id)
