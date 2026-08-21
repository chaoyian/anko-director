from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from anko_director.config import AppSettings, ConfigStore
from anko_director.database import Database
from anko_director.schemas import (
    DiceOperation,
    DiceSpec,
    MemoryExtraction,
    RollProposal,
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


class FakeAI:
    async def parse_proposal(self, instruction: str) -> RollProposal:
        return RollProposal(
            title="交流判定",
            character_name="千早爱音",
            operations=[DiceOperation(id="talk", label="交流", dice=DiceSpec(sides=100))],
        )

    async def write_story(self, *args, **kwargs):
        assets = kwargs["assets"]
        assert assets[0]["body"] == "保存时的正文"
        assert "千早爱音" in kwargs["roll_announcement"]
        assert "掷出了 " in kwargs["roll_announcement"]
        assert " 点！" in kwargs["roll_announcement"]
        return "生成的故事草稿", "fake", "story-model"

    async def local_chat(self, campaign, messages, content):
        return f"讨论：{content}", "fake", "story-model"

    async def extract_memories(self, story, characters):
        return MemoryExtraction(
            memories=[
                {
                    "owner_asset_id": character["id"],
                    "involved_asset_ids": [item["id"] for item in characters],
                    "summary": "记住了本回合",
                    "interpretation": f"{character['name']}认为这值得留意",
                    "emotion": "谨慎",
                    "importance": 4,
                }
                for character in characters
            ]
        )


def test_assets_persist_positions_and_snapshot_content(tmp_path: Path) -> None:
    db = Database(tmp_path / "assets.sqlite3")
    db.initialize()
    campaign = db.create_campaign("资产测试")
    asset = db.create_asset(
        campaign["id"], "character", "千早爱音", "#d89469", "摘要", "旧正文", 12, 34
    )
    snapshot = db.asset_snapshots(campaign["id"], [asset["id"]])
    db.update_asset(asset["id"], "character", "千早爱音", "#d89469", "摘要", "新正文")
    moved = db.update_asset_position(asset["id"], 80, -20)

    assert snapshot[0]["body"] == "旧正文"
    assert moved["x"] == 80
    assert moved["y"] == -20
    db.initialize()
    assert len(db.list_assets(campaign["id"])) == 1


def test_character_roll_materializes_as_asset(tmp_path: Path) -> None:
    db = Database(tmp_path / "characters.sqlite3")
    db.initialize()
    campaign = db.create_campaign("人物迁移")
    db.upsert_character(campaign["id"], "高松灯", {"音乐能力": 91})
    assets = db.list_assets(campaign["id"])

    assert len(assets) == 1
    assert assets[0]["kind"] == "character"
    assert assets[0]["derived_facts"]["音乐能力"] == 91


def test_turn_uses_asset_snapshot_and_never_rerolls(tmp_path: Path) -> None:
    db = Database(tmp_path / "turns.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = FakeAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "回合测试"}).json()
        asset = client.post(
            f"/api/campaigns/{campaign['id']}/assets",
            json={
                "kind": "character",
                "name": "千早爱音",
                "color": "#d89469",
                "summary": "测试角色",
                "body": "保存时的正文",
                "x": 0,
                "y": 0,
            },
        ).json()
        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={"instruction": "投交流并继续故事", "asset_ids": [asset["id"]]},
        ).json()
        client.put(
            f"/api/assets/{asset['id']}",
            json={
                "kind": "character",
                "name": "千早爱音",
                "color": "#d89469",
                "summary": "测试角色",
                "body": "准备后修改的正文",
            },
        )

        revealed = client.post(f"/api/turns/{prepared['turn']['id']}/roll")
        revealed_again = client.post(f"/api/turns/{prepared['turn']['id']}/roll")
        first = client.post(f"/api/turns/{prepared['turn']['id']}/execute")
        second = client.post(f"/api/turns/{prepared['turn']['id']}/execute")

        assert revealed.status_code == 200
        assert revealed.json()["turn"]["roll_result"]["roll_id"] == revealed_again.json()[
            "turn"
        ]["roll_result"]["roll_id"]
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["turn"]["roll_result"]["roll_id"] == second.json()["turn"][
            "roll_result"
        ]["roll_id"]
        assert first.json()["draft"]["content"] == "生成的故事草稿"
        assert first.json()["roll_announcement"].startswith("🎲 千早爱音的交流掷出了")
        assert len(db.list_events(campaign["id"])) == 1
        turn_messages = db.list_chat_messages(campaign["id"])
        assert [message["source"] for message in turn_messages] == [
            "director_prompt",
            "story_preview",
        ]
        assert turn_messages[0]["content"] == "投交流并继续故事"
        assert turn_messages[0]["author_name"] == "你（导演）"
        current_assets = db.list_assets(campaign["id"])
        assert len(current_assets) == 1
        assert current_assets[0]["id"] == asset["id"]
        assert "交流" not in current_assets[0]["derived_facts"]


def test_character_profile_stats_traits_and_memories(tmp_path: Path) -> None:
    db = Database(tmp_path / "profile.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = FakeAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "人物管理"}).json()
        asset = client.post(
            f"/api/campaigns/{campaign['id']}/assets",
            json={
                "kind": "character",
                "name": "千早爱音",
                "color": "#d89469",
                "body": "保存时的正文",
            },
        ).json()

        profile = client.get(f"/api/assets/{asset['id']}/character")
        assert profile.status_code == 200
        assert [stat["name"] for stat in profile.json()["stats"]] == [
            "家庭背景",
            "音乐能力",
            "身体素质",
            "学习能力",
            "语言能力",
        ]
        custom = client.post(
            f"/api/assets/{asset['id']}/stats",
            json={"name": "舞台魅力", "notation": "2d20kh1+10"},
        ).json()
        rolled = client.post(f"/api/character-stats/{custom['id']}/roll")
        assert rolled.status_code == 200
        assert next(
            stat for stat in rolled.json()["stats"] if stat["id"] == custom["id"]
        )["source"] == "roll"

        manual_trait = client.post(
            f"/api/assets/{asset['id']}/traits",
            json={"label": "善于交际", "description": "容易与陌生人熟络"},
        )
        assert manual_trait.status_code == 201
        drawn = client.post(
            f"/api/assets/{asset['id']}/traits/draw",
            json={
                "label": "隐藏特质",
                "options": [{"id": "calm", "label": "临危不乱", "weight": 1}],
                "draw_count": 1,
                "replace": False,
            },
        )
        assert drawn.status_code == 200
        assert drawn.json()["traits"][0]["label"] == "临危不乱"

        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={"instruction": "投交流并继续故事", "asset_ids": [asset["id"]]},
        ).json()
        assert client.post(f"/api/turns/{prepared['turn']['id']}/execute").status_code == 200
        generated = client.post(
            f"/api/campaigns/{campaign['id']}/memories/generate", json={}
        )
        assert generated.status_code == 200
        assert len(generated.json()["memories"]) == 1
        assert generated.json()["memories"][0]["owner_asset_id"] == asset["id"]
        repeated = client.post(
            f"/api/campaigns/{campaign['id']}/memories/generate", json={}
        )
        assert repeated.status_code == 200
        assert len(db.list_character_memories(campaign["id"])) == 1


def test_character_template_can_be_renamed_and_trimmed_without_defaults_returning(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "flexible-profile.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "自由模板"}).json()
        asset = client.post(
            f"/api/campaigns/{campaign['id']}/assets",
            json={"kind": "character", "name": "林雾", "color": "#6f91ad"},
        ).json()
        stats = client.get(f"/api/assets/{asset['id']}/character").json()["stats"]
        for stat, name in zip(
            stats,
            ("旧案牵连", "声学工程", "身体耐力", "异常观察", "坦白沟通"),
            strict=True,
        ):
            response = client.put(
                f"/api/character-stats/{stat['id']}",
                json={"name": name, "notation": "1d100", "value": None, "locked": False},
            )
            assert response.status_code == 200
        renamed = client.get(f"/api/assets/{asset['id']}/character").json()["stats"]
        assert [stat["name"] for stat in renamed] == [
            "旧案牵连",
            "声学工程",
            "身体耐力",
            "异常观察",
            "坦白沟通",
        ]
        assert client.delete(f"/api/character-stats/{renamed[0]['id']}").status_code == 204
        assert len(client.get(f"/api/assets/{asset['id']}/character").json()["stats"]) == 4


class ExplicitOnlyAI(FakeAI):
    async def parse_proposal(self, instruction: str) -> RollProposal:
        raise AssertionError("显式骰组不应调用小模型")


def test_explicit_batch_dice_bypasses_small_model(tmp_path: Path) -> None:
    db = Database(tmp_path / "explicit.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = ExplicitOnlyAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "显式骰组"}).json()
        asset = client.post(
            f"/api/campaigns/{campaign['id']}/assets",
            json={
                "kind": "character",
                "name": "千早爱音",
                "color": "#d89469",
                "body": "保存时的正文",
            },
        ).json()
        custom_stat = client.post(
            f"/api/assets/{asset['id']}/stats",
            json={"name": "交流", "notation": "1d100"},
        ).json()
        client.put(
            f"/api/character-stats/{custom_stat['id']}",
            json={"name": "交流", "notation": "1d100", "value": 65, "locked": False},
        )
        proposal = {
            "title": "连续交流判定",
            "character_name": "千早爱音",
            "target_asset_id": asset["id"],
            "operations": [
                {
                    "kind": "dice",
                    "id": "talk",
                    "label": "交流",
                    "dice": {"count": 1, "sides": 100},
                    "target": 65,
                    "comparison": "lte",
                }
            ],
            "repeat_count": 3,
            "sequence_mode": "batch",
        }
        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={
                "instruction": "根据连续判定推进故事",
                "asset_ids": [asset["id"]],
                "proposal": proposal,
            },
        )
        assert prepared.status_code == 200
        assert prepared.json()["proposal_source"] == "explicit"
        result = client.post(f"/api/turns/{prepared.json()['turn']['id']}/execute")
        assert result.status_code == 200
        assert len(result.json()["turn"]["roll_result"]["result"]["results"]) == 3
        assert "第 1/3 次" in result.json()["roll_announcement"]
        unchanged = db.get_character_stat(custom_stat["id"])
        assert unchanged["value"] == 65
        assert unchanged["source"] == "manual"
        assert db.get_asset(asset["id"])["derived_facts"]["交流"] == 65


def test_stepwise_continuous_dice_rolls_once_per_request(tmp_path: Path) -> None:
    db = Database(tmp_path / "stepwise.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = ExplicitOnlyAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "分步连续骰"}).json()
        asset = client.post(
            f"/api/campaigns/{campaign['id']}/assets",
            json={
                "kind": "character",
                "name": "千早爱音",
                "color": "#d89469",
                "body": "保存时的正文",
            },
        ).json()
        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={
                "instruction": "连续尝试三次",
                "asset_ids": [asset["id"]],
                "proposal": {
                    "title": "三次尝试",
                    "character_name": "千早爱音",
                    "target_asset_id": asset["id"],
                    "operations": [
                        {
                            "kind": "dice",
                            "id": "try",
                            "label": "尝试",
                            "dice": {"count": 1, "sides": 100},
                        }
                    ],
                    "repeat_count": 3,
                    "sequence_mode": "stepwise",
                },
            },
        ).json()
        turn_id = prepared["turn"]["id"]
        assert client.post(f"/api/turns/{turn_id}/execute").status_code == 409
        first = client.post(f"/api/turns/{turn_id}/roll-next").json()
        second = client.post(f"/api/turns/{turn_id}/roll-next").json()
        third = client.post(f"/api/turns/{turn_id}/roll-next").json()
        assert (first["current"], second["current"], third["current"]) == (1, 2, 3)
        assert not first["complete"] and third["complete"]
        assert len(db.list_events(campaign["id"])) == 3
        completed = client.post(f"/api/turns/{turn_id}/execute")
        assert completed.status_code == 200
        assert len(completed.json()["turn"]["roll_result"]["roll_ids"]) == 3


def test_cross_campaign_asset_reference_is_rejected(tmp_path: Path) -> None:
    db = Database(tmp_path / "cross.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = FakeAI()
    with TestClient(app) as client:
        first = client.post("/api/campaigns", json={"title": "甲"}).json()
        second = client.post("/api/campaigns", json={"title": "乙"}).json()
        asset = client.post(
            f"/api/campaigns/{first['id']}/assets",
            json={"kind": "scene", "name": "后台", "color": "#75a48d"},
        ).json()
        response = client.post(
            f"/api/campaigns/{second['id']}/turns/prepare",
            json={"instruction": "投 d100", "asset_ids": [asset["id"]]},
        )
        assert response.status_code == 422


def test_unbound_chat_uses_local_bot(tmp_path: Path) -> None:
    db = Database(tmp_path / "chat.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = FakeAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "聊天测试"}).json()
        response = client.post(
            f"/api/campaigns/{campaign['id']}/messages", json={"content": "讨论下一幕"}
        )
        assert response.status_code == 201
        assert response.json()["mode"] == "local"
        messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
        assert [message["author_name"] for message in messages] == ["导演", "Anko Bot"]


def test_bound_turn_automatically_queues_story_for_discord(tmp_path: Path) -> None:
    db = Database(tmp_path / "auto-publish.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = FakeAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "自动发布"}).json()
        db.bind_discord(campaign["id"], 123, 456)
        asset = client.post(
            f"/api/campaigns/{campaign['id']}/assets",
            json={
                "kind": "character",
                "name": "千早爱音",
                "color": "#d89469",
                "body": "保存时的正文",
            },
        ).json()
        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={"instruction": "投交流并继续故事", "asset_ids": [asset["id"]]},
        ).json()
        result = client.post(f"/api/turns/{prepared['turn']['id']}/execute")

        assert result.status_code == 200
        assert result.json()["delivery"] == "discord_queued"
        assert result.json()["draft"]["status"] == "published"
        assert len(db.pending_outbox()) == 1
        assert [event["kind"] for event in db.list_events(campaign["id"])] == ["roll", "story"]


def test_explicit_dice_group_preserves_each_operation_actor(tmp_path: Path) -> None:
    db = Database(tmp_path / "multi_actor.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    app.state.ai = FakeAI()
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "多人骰组"}).json()
        actors = []
        for name in ("千早爱音", "高松灯", "长崎素世"):
            actors.append(
                client.post(
                    f"/api/campaigns/{campaign['id']}/assets",
                    json={
                        "kind": "character",
                        "name": name,
                        "color": "#d89469",
                        "body": "保存时的正文",
                    },
                ).json()
            )
        proposal = {
            "title": "三人终幕",
            "operations": [
                {
                    "kind": "dice",
                    "id": f"actor_{index}",
                    "label": label,
                    "character_name": actor["name"],
                    "target_asset_id": actor["id"],
                    "dice": {"count": 1, "sides": 100},
                    "target": 70,
                    "comparison": "lte",
                }
                for index, (actor, label) in enumerate(
                    zip(actors, ("主动报告", "说出愿望", "提出请求"), strict=True),
                    start=1,
                )
            ],
        }
        prepared = client.post(
            f"/api/campaigns/{campaign['id']}/turns/prepare",
            json={
                "instruction": "三人各自说清下一步",
                "asset_ids": [actor["id"] for actor in actors],
                "proposal": proposal,
            },
        )
        assert prepared.status_code == 200
        assert [item["label"] for item in prepared.json()["preview"]["items"]] == [
            "千早爱音 · 主动报告",
            "高松灯 · 说出愿望",
            "长崎素世 · 提出请求",
        ]
        result = client.post(f"/api/turns/{prepared.json()['turn']['id']}/roll")
        assert result.status_code == 200
        announcement = result.json()["roll_announcement"]
        assert "千早爱音的主动报告" in announcement
        assert "高松灯的说出愿望" in announcement
        assert "长崎素世的提出请求" in announcement
