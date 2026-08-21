from __future__ import annotations

from typing import Any

CHARACTER_TEMPLATES: dict[str, dict[str, Any]] = {
    "anko": {
        "id": "anko",
        "name": "安科自由卡",
        "description": "五项宽松能力，适合纯叙事安科。",
        "stats": [
            {"name": "家庭背景", "notation": "1d100"},
            {"name": "音乐能力", "notation": "1d100"},
            {"name": "身体素质", "notation": "1d100"},
            {"name": "学习能力", "notation": "1d100"},
            {"name": "语言能力", "notation": "1d100"},
        ],
    },
    "coc7": {
        "id": "coc7",
        "name": "COC 7版调查员",
        "description": "九项基础属性、常用技能与自动派生战斗数据。",
        "stats": [
            {"name": "力量 STR", "notation": "3d6*5"},
            {"name": "体质 CON", "notation": "3d6*5"},
            {"name": "体型 SIZ", "notation": "(2d6+6)*5"},
            {"name": "敏捷 DEX", "notation": "3d6*5"},
            {"name": "外貌 APP", "notation": "3d6*5"},
            {"name": "智力 INT", "notation": "(2d6+6)*5"},
            {"name": "意志 POW", "notation": "3d6*5"},
            {"name": "教育 EDU", "notation": "(2d6+6)*5"},
            {"name": "幸运 Luck", "notation": "3d6*5"},
            {"name": "闪避 Dodge", "notation": "1d100", "locked": True},
            {"name": "斗殴 Fighting (Brawl)", "notation": "1d100", "value": 25},
            {"name": "手枪 Firearms (Handgun)", "notation": "1d100", "value": 20},
            {"name": "急救 First Aid", "notation": "1d100", "value": 30},
            {"name": "聆听 Listen", "notation": "1d100", "value": 20},
            {"name": "侦查 Spot Hidden", "notation": "1d100", "value": 25},
            {"name": "图书馆使用 Library Use", "notation": "1d100", "value": 20},
            {"name": "心理学 Psychology", "notation": "1d100", "value": 10},
            {"name": "生命值 HP", "notation": "1d100", "locked": True},
            {"name": "理智 SAN", "notation": "1d100", "locked": True},
            {"name": "魔法值 MP", "notation": "1d100", "locked": True},
            {"name": "移动力 MOV", "notation": "1d100", "locked": True},
            {"name": "体格 Build", "notation": "1d100", "locked": True},
        ],
    },
}


def template_catalog() -> list[dict[str, str]]:
    return [
        {"id": item["id"], "name": item["name"], "description": item["description"]}
        for item in CHARACTER_TEMPLATES.values()
    ]


def stat_value(values: dict[str, int], token: str, default: int = 0) -> int:
    token = token.lower()
    for name, value in values.items():
        if token in name.lower():
            return int(value)
    return default


def damage_bonus_and_build(strength: int, size: int) -> tuple[str, int]:
    total = strength + size
    if total <= 64:
        return "-2", -2
    if total <= 84:
        return "-1", -1
    if total <= 124:
        return "0", 0
    if total <= 164:
        return "1d4", 1
    if total <= 204:
        return "1d6", 2
    extra = (total - 205) // 80
    return f"{2 + extra}d6", 3 + extra


def coc7_derived_values(values: dict[str, int]) -> dict[str, int | str]:
    strength = stat_value(values, "str")
    constitution = stat_value(values, "con")
    size = stat_value(values, "siz")
    dexterity = stat_value(values, "dex")
    power = stat_value(values, "pow")
    dodge = dexterity // 2 if dexterity else 0
    hp = (constitution + size) // 10 if constitution and size else 0
    if strength and dexterity and size:
        if strength < size and dexterity < size:
            move = 7
        elif strength > size and dexterity > size:
            move = 9
        else:
            move = 8
    else:
        move = 0
    damage_bonus, build = damage_bonus_and_build(strength, size)
    return {
        "闪避 Dodge": dodge,
        "生命值 HP": hp,
        "理智 SAN": power,
        "魔法值 MP": power // 5 if power else 0,
        "移动力 MOV": move,
        "体格 Build": build,
        "伤害加值 Damage Bonus": damage_bonus,
    }
