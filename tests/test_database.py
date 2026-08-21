from __future__ import annotations

from pathlib import Path

from anko_director.database import Database
from anko_director.dice import execute_proposal
from anko_director.schemas import DiceOperation, DiceSpec, RollProposal


def make_db(path: Path) -> Database:
    database = Database(path / "test.sqlite3")
    database.initialize()
    return database


def test_event_chain_and_character_materialization(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    campaign = db.create_campaign("测试游戏")
    proposal = RollProposal(
        title="人物卡",
        character_name="千早爱音",
        operations=[
            DiceOperation(id="music", label="音乐能力", dice=DiceSpec(sides=100))
        ],
    )
    proposal_id = db.save_proposal(campaign["id"], "建立人物卡", proposal.model_dump(mode="json"))
    result = execute_proposal(proposal, "11" * 32)
    db.record_roll(campaign["id"], proposal_id, result)
    character = db.apply_roll_to_character(campaign["id"], "千早爱音", result)
    assert character["data"]["音乐能力"] == result["results"][0]["total"]
    assert db.verify_event_chain(campaign["id"])

    with db.connect() as connection:
        connection.execute(
            "UPDATE events SET payload_json='{}' WHERE campaign_id=?", (campaign["id"],)
        )
    assert not db.verify_event_chain(campaign["id"])


def test_publish_queues_story_and_unpublished_rolls(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    campaign = db.create_campaign("发布测试")
    proposal = RollProposal(
        title="判定",
        operations=[DiceOperation(id="x", label="幸运", dice=DiceSpec(sides=100))],
    )
    result = execute_proposal(proposal, "22" * 32)
    db.record_roll(campaign["id"], None, result)
    assert len(db.unpublished_rolls(campaign["id"])) == 1
    draft = db.save_draft(campaign["id"], "继续", "故事", "fake", "fake-small")
    db.publish_draft(draft["id"], "故事", "骰子证据\n\n故事")
    assert db.unpublished_rolls(campaign["id"]) == []
    assert db.pending_outbox()[0]["content"] == "骰子证据\n\n故事"
