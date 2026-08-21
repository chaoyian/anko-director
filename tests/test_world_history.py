from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anko_director.config import AppSettings, ConfigStore
from anko_director.database import Database
from anko_director.schemas import (
    DiceOperation,
    DiceSpec,
    MemoryExtraction,
    RollProposal,
    WorldDelta,
)
from anko_director.web.app import create_app


class LocalConfigStore(ConfigStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.settings = AppSettings()

    def load(self) -> AppSettings:
        return self.settings

    def get_secret(self, name: str) -> str | None:
        return None


class WorldAI:
    def __init__(self) -> None:
        self.parse_calls = 0
        self.story_calls = 0
        self.world_calls = 0
        self.memory_calls = 0

    async def parse_proposal(self, instruction: str) -> RollProposal:
        self.parse_calls += 1
        return RollProposal(
            title="观察判定",
            operations=[DiceOperation(id="notice", label="观察", dice=DiceSpec(sides=100))],
        )

    async def write_story(self, *args, **kwargs):
        self.story_calls += 1
        assert kwargs["world_context"]["status_cards"]
        return "两人发现门后的脚步声，并决定彼此照应。", "fake", "story"

    async def analyze_world_delta(self, story, assets, status_cards, relations, outcomes):
        self.world_calls += 1
        return WorldDelta(
            outcome={
                "title": "发现追踪者",
                "summary": "两人确认门外有人跟踪。",
                "participant_asset_ids": [asset["id"] for asset in assets],
                "previous_outcome_ids": [],
            },
            status_operations=[
                {
                    "action": "create",
                    "target_asset_id": assets[0]["id"],
                    "duration_turns": 1,
                    "description": "保持警戒",
                }
            ],
            relation_operations=[
                {
                    "action": "create",
                    "source_asset_id": assets[0]["id"],
                    "target_asset_id": assets[1]["id"],
                    "label": "互相照应",
                    "description": "共同面对跟踪者",
                }
            ],
        )

    async def extract_memories(self, story, characters):
        self.memory_calls += 1
        return MemoryExtraction(
            memories=[
                {
                    "owner_asset_id": character["id"],
                    "involved_asset_ids": [item["id"] for item in characters],
                    "summary": "发现了跟踪者",
                    "interpretation": "必须与同伴合作",
                    "emotion": "警觉",
                    "importance": 4,
                }
                for character in characters
            ]
        )


def test_turn_postprocessing_is_split_idempotent_and_committed(tmp_path: Path) -> None:
    db = Database(tmp_path / "world.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    ai = WorldAI()
    app.state.ai = ai
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "世界状态"}).json()
        characters = [
            client.post(
                f"/api/campaigns/{campaign['id']}/assets",
                json={"kind": "character", "name": name, "color": "#d89469"},
            ).json()
            for name in ("林雾", "祝遥")
        ]
        old_card = client.post(
            f"/api/campaigns/{campaign['id']}/status-cards",
            json={
                "target_asset_id": characters[0]["id"],
                "duration_turns": 2,
                "description": "暂时耳鸣",
            },
        ).json()
        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={
                "instruction": "观察门外并推进故事",
                "asset_ids": [item["id"] for item in characters],
            },
        ).json()
        first = client.post(f"/api/turns/{prepared['turn']['id']}/execute")
        repeated = client.post(f"/api/turns/{prepared['turn']['id']}/execute")

        assert first.status_code == repeated.status_code == 200
        assert (ai.parse_calls, ai.story_calls, ai.world_calls, ai.memory_calls) == (1, 1, 1, 1)
        assert first.json()["commit"]["id"] == repeated.json()["commit"]["id"]
        cards = db.list_status_cards(campaign["id"])
        assert next(item for item in cards if item["id"] == old_card["id"])["remaining_turns"] == 1
        assert any(item["description"] == "保持警戒" for item in cards)
        assert [item["label"] for item in db.list_relations(campaign["id"])] == ["互相照应"]
        assert len(db.list_outcomes(campaign["id"])) == 1
        assert len(db.list_character_memories(campaign["id"])) == 2
        assert db.get_world_analysis(prepared["turn"]["id"])["status"] == "complete"


def test_world_delta_cannot_touch_unreferenced_asset(tmp_path: Path) -> None:
    db = Database(tmp_path / "guard.sqlite3")
    db.initialize()
    campaign = db.create_campaign("边界")
    first = db.create_asset(campaign["id"], "character", "甲", "#d89469")
    second = db.create_asset(campaign["id"], "character", "乙", "#d89469")
    proposal = RollProposal(
        title="测试", operations=[DiceOperation(id="x", label="测试", dice=DiceSpec(sides=100))]
    )
    turn = db.create_turn(
        campaign["id"],
        "测试",
        db.asset_snapshots(campaign["id"], [first["id"]]),
        proposal.model_dump(),
    )
    delta = WorldDelta(
        outcome={
            "title": "结果",
            "summary": "结果",
            "participant_asset_ids": [first["id"]],
        },
        status_operations=[
            {
                "action": "create",
                "target_asset_id": second["id"],
                "duration_turns": 1,
                "description": "越权状态",
            }
        ],
    )

    with pytest.raises(ValueError, match="未引用"):
        db.apply_world_delta(turn["id"], delta.model_dump(mode="json"), [first["id"]])


def test_branch_switch_restores_exact_workspace_and_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "branches.sqlite3")
    db.initialize()
    campaign = db.create_campaign("分支")
    asset = db.create_asset(campaign["id"], "character", "林雾", "#d89469", body="版本一")
    setup = db.create_world_commit(campaign["id"], None, "correction", "人物建立")
    main = db.get_active_branch(campaign["id"])

    db.update_asset(asset["id"], "character", "林雾", "#d89469", "", "版本二")
    db.create_world_commit(campaign["id"], None, "correction", "主线修改")
    branch = db.create_branch(campaign["id"], setup["id"], "另一种选择")
    assert branch["id"] != main["id"]
    assert db.get_asset(asset["id"])["body"] == "版本一"

    db.update_asset(asset["id"], "character", "林雾", "#d89469", "", "支线版本")
    db.create_world_commit(campaign["id"], None, "correction", "支线修改")
    db.activate_branch(main["id"])
    assert db.get_asset(asset["id"])["body"] == "版本二"
    assert (
        db.get_world_at_commit(campaign["id"], setup["id"])["world"]["assets"][0]["body"]
        == "版本一"
    )


def test_removed_status_stays_in_older_commit(tmp_path: Path) -> None:
    db = Database(tmp_path / "status-history.sqlite3")
    db.initialize()
    campaign = db.create_campaign("状态历史")
    asset = db.create_asset(campaign["id"], "character", "祝遥", "#d89469")
    db.create_world_commit(campaign["id"], None, "correction", "建立人物")
    card = db.create_status_card(campaign["id"], asset["id"], None, "永久戒备")
    with_card = db.get_active_branch(campaign["id"])["head_commit_id"]

    db.end_status_card(card["id"])

    assert db.list_status_cards(campaign["id"]) == []
    old_world = db.get_world_at_commit(campaign["id"], with_card)["world"]
    assert old_world["status_cards"][0]["description"] == "永久戒备"


def test_world_commit_hash_chain_detects_snapshot_tampering(tmp_path: Path) -> None:
    db = Database(tmp_path / "history-hash.sqlite3")
    db.initialize()
    campaign = db.create_campaign("历史校验")
    db.create_asset(campaign["id"], "character", "林雾", "#d89469")
    commit = db.create_world_commit(campaign["id"], None, "correction", "建立人物")

    assert db.verify_world_history(campaign["id"])
    with db.connect() as connection:
        connection.execute(
            "UPDATE world_commits SET snapshot_json=? WHERE id=?",
            ('{"tampered":true}', commit["id"]),
        )
    assert not db.verify_world_history(campaign["id"])
