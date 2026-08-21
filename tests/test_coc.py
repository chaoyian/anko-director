from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from anko_director.coc import SuccessLevel, apply_damage, resolve_combat_action, success_level
from anko_director.config import AppSettings, ConfigStore
from anko_director.database import Database
from anko_director.dice import DeterministicRandom, execute_proposal, parse_dice_notation
from anko_director.schemas import DiceOperation, RollProposal
from anko_director.web.app import create_app


class LocalConfigStore(ConfigStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.settings = AppSettings()

    def load(self) -> AppSettings:
        return self.settings

    def get_secret(self, name: str) -> str | None:
        return None


def test_character_creation_notation_supports_coc_multiplier() -> None:
    proposal = RollProposal(
        title="COC属性生成",
        operations=[
            DiceOperation(id="str", label="STR", dice=parse_dice_notation("3d6*5")),
            DiceOperation(id="siz", label="SIZ", dice=parse_dice_notation("(2d6+6)*5")),
        ],
    )
    result = execute_proposal(proposal, seed_hex="11" * 32)

    assert result["results"][0]["notation"] == "3d6*5"
    assert result["results"][1]["notation"] == "(2d6+6)*5"
    assert all(item["total"] % 5 == 0 for item in result["results"])


def test_coc_success_levels_and_major_wound() -> None:
    assert success_level(1, 60) is SuccessLevel.CRITICAL
    assert success_level(12, 60) is SuccessLevel.EXTREME
    assert success_level(30, 60) is SuccessLevel.HARD
    assert success_level(60, 60) is SuccessLevel.REGULAR
    assert success_level(96, 40) is SuccessLevel.FUMBLE
    assert success_level(96, 60) is SuccessLevel.FAILURE

    state = {
        "max_hp": 10,
        "current_hp": 10,
        "armor": 2,
        "major_wound": False,
        "unconscious": False,
        "dying": False,
        "dead": False,
    }
    updated, damage = apply_damage(
        DeterministicRandom.from_hex("22" * 32), state, raw_damage=7, constitution=100
    )
    assert damage["inflicted"] == 5
    assert damage["major_wound"] is True
    assert updated["current_hp"] == 5
    assert updated["major_wound"] is True


def test_coc_dodge_wins_ties_and_attacker_wins_fight_back_ties(monkeypatch) -> None:
    rolls = iter(
        [
            {"roll": 40, "target": 60, "level_rank": 2, "level_label": "成功", "success": True},
            {"roll": 35, "target": 60, "level_rank": 2, "level_label": "成功", "success": True},
        ]
    )
    monkeypatch.setattr("anko_director.coc.roll_percentile", lambda *args, **kwargs: next(rolls))
    profile = {
        "name": "甲",
        "brawl": 60,
        "firearm": 50,
        "dodge": 60,
        "constitution": 60,
        "build": 0,
        "damage_bonus": "0",
        "state": {
            "max_hp": 10,
            "current_hp": 10,
            "armor": 0,
            "major_wound": False,
            "unconscious": False,
            "dying": False,
            "dead": False,
        },
    }
    defender = {**profile, "name": "乙", "state": dict(profile["state"])}
    action = {
        "attack_kind": "melee",
        "defense": "dodge",
        "attack_skill": 60,
        "defense_skill": 60,
        "bonus_dice": 0,
        "weapon_name": "拳头",
        "damage": "1d3",
        "use_damage_bonus": True,
        "impaling": False,
        "shots": 1,
    }
    dodged = resolve_combat_action(action, profile, defender, seed_hex="33" * 32)
    assert dodged["resolutions"][0]["hit"] is False

    rolls = iter(
        [
            {"roll": 40, "target": 60, "level_rank": 2, "level_label": "成功", "success": True},
            {"roll": 35, "target": 60, "level_rank": 2, "level_label": "成功", "success": True},
        ]
    )
    monkeypatch.setattr("anko_director.coc.roll_percentile", lambda *args, **kwargs: next(rolls))
    action["defense"] = "fight_back"
    fought = resolve_combat_action(action, profile, defender, seed_hex="44" * 32)
    assert fought["resolutions"][0]["hit"] is True
    assert fought["resolutions"][0]["fight_back_hit"] is False


def test_coc_template_derives_profile_and_combat_request_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "coc.sqlite3")
    app = create_app(db, LocalConfigStore(tmp_path / "config"), start_integrations=False)
    with TestClient(app) as client:
        campaign = client.post("/api/campaigns", json={"title": "COC测试"}).json()
        assets = []
        for name in ("调查员", "食尸鬼"):
            asset = client.post(
                f"/api/campaigns/{campaign['id']}/assets",
                json={"kind": "character", "name": name, "color": "#d89469"},
            ).json()
            applied = client.post(
                f"/api/assets/{asset['id']}/stats/apply-template",
                json={"template_id": "coc7", "mode": "replace", "roll_now": True},
            )
            assert applied.status_code == 200
            profile = applied.json()["coc_profile"]
            assert profile["state"]["max_hp"] > 0
            assert profile["state"]["current_hp"] == profile["state"]["max_hp"]
            assert profile["damage_bonus"] in {"-2", "-1", "0", "1d4", "1d6", "2d6"}
            assets.append(asset)

        payload = {
            "request_id": "same-combat-request",
            "attacker_asset_id": assets[0]["id"],
            "defender_asset_id": assets[1]["id"],
            "attack_kind": "melee",
            "defense": "none",
            "attack_skill": 100,
            "defense_skill": 25,
            "weapon_name": "球棒",
            "damage": "1d8",
            "use_damage_bonus": True,
        }
        first = client.post(f"/api/campaigns/{campaign['id']}/coc/combat", json=payload)
        second = client.post(f"/api/campaigns/{campaign['id']}/coc/combat", json=payload)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["replayed"] is True
        assert first.json()["result"]["seed_hex"] == second.json()["result"]["seed_hex"]
        assert (
            len(
                [event for event in db.list_events(campaign["id"]) if event["kind"] == "coc_combat"]
            )
            == 1
        )
        messages = db.list_chat_messages(campaign["id"])
        assert len([message for message in messages if message["source"] == "coc_combat"]) == 1

        wounded = db.get_coc_profile(assets[1]["id"])
        dying_state = {
            **wounded["state"],
            "current_hp": 0,
            "major_wound": True,
            "unconscious": True,
            "dying": True,
            "dead": False,
        }
        assert (
            client.put(f"/api/assets/{assets[1]['id']}/coc-state", json=dying_state).status_code
            == 200
        )
        advanced = client.post(f"/api/campaigns/{campaign['id']}/coc/next-round")
        assert advanced.status_code == 200
        assert len(advanced.json()["dying_checks"]) == 1
        checked = db.get_coc_profile(assets[1]["id"])["state"]
        assert checked["dying"] or checked["dead"]


def test_first_aid_stabilizes_dying_character(monkeypatch) -> None:
    monkeypatch.setattr(
        "anko_director.coc.roll_percentile",
        lambda *args, **kwargs: {
            "roll": 20,
            "target": 60,
            "level_rank": 3,
            "level_label": "困难成功",
            "success": True,
        },
    )
    state = {
        "max_hp": 10,
        "current_hp": 0,
        "armor": 0,
        "major_wound": True,
        "unconscious": True,
        "dying": True,
        "dead": False,
    }
    medic = {
        "name": "医生",
        "brawl": 25,
        "firearm": 20,
        "first_aid": 60,
        "dodge": 30,
        "constitution": 60,
        "build": 0,
        "damage_bonus": "0",
        "state": {**state, "current_hp": 10, "dying": False, "unconscious": False},
    }
    patient = {**medic, "name": "伤员", "state": state}
    result = resolve_combat_action(
        {
            "attack_kind": "first_aid",
            "defense": "none",
            "attack_skill": 60,
            "defense_skill": 0,
            "bonus_dice": 0,
            "weapon_name": "急救包",
            "damage": "1d3",
            "use_damage_bonus": False,
            "impaling": False,
            "shots": 1,
        },
        medic,
        patient,
        seed_hex="55" * 32,
    )
    assert result["defender_state"]["current_hp"] == 1
    assert result["defender_state"]["dying"] is False
    assert result["defender_state"]["unconscious"] is False
