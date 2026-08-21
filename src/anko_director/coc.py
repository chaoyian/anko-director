from __future__ import annotations

from enum import IntEnum
from typing import Any

from .dice import DeterministicRandom, parse_dice_notation


class SuccessLevel(IntEnum):
    FUMBLE = 0
    FAILURE = 1
    REGULAR = 2
    HARD = 3
    EXTREME = 4
    CRITICAL = 5


LEVEL_LABELS = {
    SuccessLevel.FUMBLE: "大失败",
    SuccessLevel.FAILURE: "失败",
    SuccessLevel.REGULAR: "成功",
    SuccessLevel.HARD: "困难成功",
    SuccessLevel.EXTREME: "极难成功",
    SuccessLevel.CRITICAL: "大成功",
}


def success_level(roll: int, target: int) -> SuccessLevel:
    if roll == 1:
        return SuccessLevel.CRITICAL
    if roll == 100 or (target < 50 and roll >= 96):
        return SuccessLevel.FUMBLE
    if roll <= max(1, target // 5):
        return SuccessLevel.EXTREME
    if roll <= target // 2:
        return SuccessLevel.HARD
    if roll <= target:
        return SuccessLevel.REGULAR
    return SuccessLevel.FAILURE


def roll_percentile(rng: DeterministicRandom, target: int, bonus_dice: int = 0) -> dict[str, Any]:
    bonus_dice = max(-2, min(2, bonus_dice))
    units = rng.randbelow(10)
    tens = [rng.randbelow(10) for _ in range(abs(bonus_dice) + 1)]
    candidates = [100 if digit == 0 and units == 0 else digit * 10 + units for digit in tens]
    chosen = (
        min(candidates) if bonus_dice > 0 else max(candidates) if bonus_dice < 0 else candidates[0]
    )
    level = success_level(chosen, target)
    return {
        "roll": chosen,
        "target": target,
        "bonus_dice": bonus_dice,
        "units": units,
        "tens": tens,
        "candidates": candidates,
        "level": level.name.lower(),
        "level_rank": int(level),
        "level_label": LEVEL_LABELS[level],
        "success": level >= SuccessLevel.REGULAR,
    }


def _roll_spec(rng: DeterministicRandom, notation: str) -> tuple[int, list[int]]:
    spec = parse_dice_notation(notation)
    raw = [rng.randbelow(spec.sides) + 1 for _ in range(spec.count)]
    return (sum(raw) + spec.modifier) * spec.multiplier, raw


def _max_spec(notation: str) -> int:
    spec = parse_dice_notation(notation)
    return (spec.count * spec.sides + spec.modifier) * spec.multiplier


def _roll_bonus(rng: DeterministicRandom, notation: str) -> tuple[int, list[int]]:
    try:
        value = int(notation)
    except ValueError:
        return _roll_spec(rng, notation)
    return value, []


def _max_bonus(notation: str) -> int:
    try:
        return int(notation)
    except ValueError:
        return _max_spec(notation)


def roll_damage(
    rng: DeterministicRandom,
    weapon_damage: str,
    damage_bonus: str,
    *,
    use_damage_bonus: bool,
    extreme: bool,
    impaling: bool,
) -> dict[str, Any]:
    bonus = damage_bonus if use_damage_bonus else "0"
    if extreme:
        weapon_max = _max_spec(weapon_damage)
        bonus_max = _max_bonus(bonus)
        if impaling:
            extra, extra_raw = _roll_spec(rng, weapon_damage)
            total = weapon_max + bonus_max + extra
            return {
                "total": total,
                "mode": "impaling_extreme",
                "parts": {
                    "weapon_max": weapon_max,
                    "bonus_max": bonus_max,
                    "extra": extra,
                    "extra_raw": extra_raw,
                },
            }
        total = weapon_max + bonus_max
        return {
            "total": total,
            "mode": "blunt_extreme",
            "parts": {"weapon_max": weapon_max, "bonus_max": bonus_max},
        }
    weapon, weapon_raw = _roll_spec(rng, weapon_damage)
    bonus_value, bonus_raw = _roll_bonus(rng, bonus)
    return {
        "total": max(0, weapon + bonus_value),
        "mode": "normal",
        "parts": {
            "weapon": weapon,
            "weapon_raw": weapon_raw,
            "bonus": bonus_value,
            "bonus_raw": bonus_raw,
        },
    }


def apply_damage(
    rng: DeterministicRandom,
    state: dict[str, Any],
    raw_damage: int,
    constitution: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = dict(state)
    armor = int(updated.get("armor", 0))
    inflicted = max(0, raw_damage - armor)
    max_hp = int(updated["max_hp"])
    before = int(updated["current_hp"])
    after = max(0, before - inflicted)
    major_now = inflicted >= (max_hp + 1) // 2
    instant_death = inflicted >= max_hp
    updated["current_hp"] = after
    updated["major_wound"] = bool(updated.get("major_wound")) or major_now
    con_roll = None
    if instant_death:
        updated.update({"dead": True, "dying": False, "unconscious": True})
    elif after == 0:
        updated["unconscious"] = True
        updated["dying"] = bool(updated["major_wound"])
    elif major_now:
        con_roll = roll_percentile(rng, constitution)
        if not con_roll["success"]:
            updated["unconscious"] = True
    return updated, {
        "raw_damage": raw_damage,
        "armor": armor,
        "inflicted": inflicted,
        "hp_before": before,
        "hp_after": after,
        "major_wound": major_now,
        "instant_death": instant_death,
        "constitution_roll": con_roll,
    }


def resolve_combat_action(
    action: dict[str, Any],
    attacker: dict[str, Any],
    defender: dict[str, Any],
    *,
    seed_hex: str | None = None,
) -> dict[str, Any]:
    rng = DeterministicRandom.from_hex(seed_hex) if seed_hex else DeterministicRandom.fresh()
    kind = action["attack_kind"]
    defense = action["defense"]
    attack_skill = int(
        action.get("attack_skill")
        or (
            attacker["first_aid"]
            if kind == "first_aid"
            else attacker["firearm"]
            if kind == "firearm"
            else attacker["brawl"]
        )
    )
    defense_skill = int(
        action.get("defense_skill")
        or (defender["brawl"] if defense == "fight_back" else defender["dodge"])
    )
    if kind == "first_aid":
        check = roll_percentile(rng, attack_skill, int(action.get("bonus_dice", 0)))
        defender_state = dict(defender["state"])
        before = defender_state["current_hp"]
        stabilized = bool(check["success"] and not defender_state.get("dead"))
        if stabilized:
            defender_state["current_hp"] = min(defender_state["max_hp"], before + 1)
            defender_state["dying"] = False
            defender_state["unconscious"] = False
        verdict = "急救成功，恢复1点生命并解除濒死" if stabilized else "急救失败，伤势没有改善"
        hp_text = f"{defender_state['current_hp']}/{defender_state['max_hp']}"
        announcement = (
            f"⚕️ COC第7版急救 · {attacker['name']}为{defender['name']}进行急救！\n"
            f"急救检定：{check['roll']}/{attack_skill}（{check['level_label']}）\n"
            f"判定：{verdict}。{defender['name']} HP {hp_text}"
        )
        return {
            "algorithm": "coc7-percentile-v1",
            "seed_hex": rng.seed_hex,
            "effective_bonus_dice": int(action.get("bonus_dice", 0)),
            "attack_skill": attack_skill,
            "defense_skill": 0,
            "defense_roll": None,
            "resolutions": [
                {"shot": 1, "attack_roll": check, "hit": stabilized, "fight_back_hit": False}
            ],
            "attacker_state": dict(attacker["state"]),
            "defender_state": defender_state,
            "announcement": announcement,
            "verdict": verdict,
        }
    effective_bonus = int(action.get("bonus_dice", 0))
    defense_roll = None
    if kind == "firearm" and defense == "dive_for_cover":
        defense_roll = roll_percentile(rng, defense_skill)
        if defense_roll["success"]:
            effective_bonus -= 1
    if kind == "firearm" and int(action.get("shots", 1)) > 1:
        effective_bonus -= 1
    if kind == "firearm" and action.get("point_blank"):
        effective_bonus += 1
    if kind != "firearm" and action.get("outnumbered"):
        effective_bonus += 1
    maneuver_gap = int(defender.get("build", 0)) - int(attacker.get("build", 0))
    maneuver_impossible = kind == "maneuver" and maneuver_gap >= 3
    if kind == "maneuver" and maneuver_gap > 0:
        effective_bonus -= min(2, maneuver_gap)
    effective_bonus = max(-2, min(2, effective_bonus))
    if defense in {"dodge", "fight_back"}:
        defense_roll = roll_percentile(rng, defense_skill)

    attacker_state = dict(attacker["state"])
    defender_state = dict(defender["state"])
    shots = int(action.get("shots", 1)) if kind == "firearm" else 1
    resolutions: list[dict[str, Any]] = []
    for shot in range(1, shots + 1):
        attack_roll = roll_percentile(rng, attack_skill, effective_bonus)
        attack_rank = attack_roll["level_rank"]
        defense_rank = defense_roll["level_rank"] if defense_roll else SuccessLevel.FAILURE
        hit = attack_rank >= SuccessLevel.REGULAR
        if maneuver_impossible:
            hit = False
        fight_back_hit = False
        if kind != "firearm" and defense == "dodge":
            hit = hit and attack_rank > defense_rank
        elif kind != "firearm" and defense == "fight_back":
            hit = hit and attack_rank >= defense_rank
            fight_back_hit = defense_rank >= SuccessLevel.REGULAR and defense_rank > attack_rank

        item: dict[str, Any] = {
            "shot": shot,
            "attack_roll": attack_roll,
            "hit": bool(hit),
            "fight_back_hit": bool(fight_back_hit),
        }
        if hit and kind != "maneuver":
            damage = roll_damage(
                rng,
                action["damage"],
                attacker["damage_bonus"],
                use_damage_bonus=bool(action.get("use_damage_bonus", True)),
                extreme=attack_rank >= SuccessLevel.EXTREME,
                impaling=bool(action.get("impaling")),
            )
            defender_state, applied = apply_damage(
                rng, defender_state, damage["total"], defender["constitution"]
            )
            item.update({"damage": damage, "applied_damage": applied})
        elif fight_back_hit:
            damage = roll_damage(
                rng,
                "1d3",
                defender["damage_bonus"],
                use_damage_bonus=True,
                extreme=False,
                impaling=False,
            )
            attacker_state, applied = apply_damage(
                rng, attacker_state, damage["total"], attacker["constitution"]
            )
            item.update({"damage": damage, "applied_damage": applied, "damage_target": "attacker"})
        elif hit and kind == "maneuver":
            item["maneuver_effect"] = action.get("maneuver_effect") or "战技成功"
        resolutions.append(item)

    hits = sum(1 for item in resolutions if item["hit"])
    countered = any(item["fight_back_hit"] for item in resolutions)
    total_damage = sum(
        item.get("applied_damage", {}).get("inflicted", 0)
        for item in resolutions
        if item.get("damage_target") != "attacker"
    )
    if maneuver_impossible:
        verdict = "双方体格相差三级或以上，战技无法生效"
    elif kind == "maneuver" and hits:
        verdict = f"战技成功：{action.get('maneuver_effect') or '效果成立'}"
    elif countered:
        verdict = f"{defender['name']}反击成功"
    elif hits:
        verdict = f"命中 {hits}/{shots} 次，造成 {total_damage} 点伤害"
    else:
        verdict = "攻击未命中"
    state_labels = [
        label
        for active, label in (
            (defender_state.get("dead"), "死亡"),
            (defender_state.get("dying"), "濒死"),
            (defender_state.get("unconscious"), "昏迷"),
            (defender_state.get("major_wound"), "重伤"),
        )
        if active
    ]
    state_text = f"（{'、'.join(state_labels)}）" if state_labels else ""
    defender_hp = f"{defender_state['current_hp']}/{defender_state['max_hp']}"
    announcement = (
        f"⚔️ COC第7版战斗 · {attacker['name']}使用{action['weapon_name']}攻击{defender['name']}！\n"
        f"攻击检定："
        + "、".join(
            f"{item['attack_roll']['roll']}/{attack_skill}（{item['attack_roll']['level_label']}）"
            for item in resolutions
        )
        + (
            f"；防御检定：{defense_roll['roll']}/{defense_skill}（{defense_roll['level_label']}）"
            if defense_roll
            else ""
        )
        + f"\n判定：{verdict}。{defender['name']} HP {defender_hp}{state_text}"
    )
    return {
        "algorithm": "coc7-percentile-v1",
        "seed_hex": rng.seed_hex,
        "effective_bonus_dice": effective_bonus,
        "attack_skill": attack_skill,
        "defense_skill": defense_skill,
        "defense_roll": defense_roll,
        "maneuver_build_gap": maneuver_gap if kind == "maneuver" else 0,
        "maneuver_impossible": maneuver_impossible,
        "resolutions": resolutions,
        "attacker_state": attacker_state,
        "defender_state": defender_state,
        "announcement": announcement,
        "verdict": verdict,
    }
