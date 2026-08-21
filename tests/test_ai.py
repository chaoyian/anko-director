from __future__ import annotations

from anko_director.ai.providers import extract_json
from anko_director.ai.service import AIService, heuristic_proposal
from anko_director.config import AppSettings, ProviderSettings
from anko_director.schemas import DiceOperation, TableOperation

LOVE_NOTE_INSTRUCTION = """建立千早爱音人物卡，先只给出各项能力值，
包括（家庭背景/音乐能力/身体素质/学习能力/语言能力）
并随机抽奖以下几种特殊能力的一个或者若干个
1. 天才操盘手
2. 高情商沟通家
3. 运气爆棚
4. 女同圣体
5. 科研圣体
6. 莽夫体制"""


def test_offline_parser_builds_anon_character_card() -> None:
    proposal = heuristic_proposal(LOVE_NOTE_INSTRUCTION)
    assert proposal.character_name == "千早爱音"
    dice_operations = [item for item in proposal.operations if isinstance(item, DiceOperation)]
    table_operations = [item for item in proposal.operations if isinstance(item, TableOperation)]
    assert [item.label for item in dice_operations] == [
        "家庭背景",
        "音乐能力",
        "身体素质",
        "学习能力",
        "语言能力",
    ]
    assert all(item.dice.sides == 100 for item in dice_operations)
    assert len(table_operations) == 1
    assert table_operations[0].draw_count == 1
    assert table_operations[0].replace is False
    assert len(table_operations[0].options) == 6


def test_extract_json_handles_markdown_fence() -> None:
    assert extract_json("```json\n{\"title\": \"ok\"}\n```") == {"title": "ok"}


async def test_parameter_extraction_always_uses_small_model(monkeypatch) -> None:
    used_models: list[str] = []

    class FakeProvider:
        async def structured(self, model, system, prompt, schema):
            used_models.append(model)
            return heuristic_proposal("投 d100").model_dump(mode="json")

    settings = AppSettings(
        provider=ProviderSettings(parameter_model="tiny-parameter-model", story_model="large-story")
    )
    service = AIService(settings, object())
    monkeypatch.setattr(service, "_provider", lambda: FakeProvider())
    await service.parse_proposal("投 d100")
    assert used_models == ["tiny-parameter-model"]


async def test_memory_extraction_repairs_missing_character_with_small_model(
    monkeypatch,
) -> None:
    used_models: list[str] = []
    prompts: list[str] = []
    characters = [
        {"id": "lin", "name": "林雾", "summary": "", "body": ""},
        {"id": "zhu", "name": "祝遥", "summary": "", "body": ""},
    ]

    class FlakyProvider:
        async def structured(self, model, system, prompt, schema):
            used_models.append(model)
            prompts.append(prompt)
            owners = characters[:1] if len(prompts) == 1 else characters
            return {
                "memories": [
                    {
                        "owner_asset_id": character["id"],
                        "involved_asset_ids": [item["id"] for item in characters],
                        "summary": "完成了本回合",
                        "interpretation": f"{character['name']}记住了自己的选择",
                        "emotion": "紧张",
                        "importance": 4,
                    }
                    for character in owners
                ]
            }

    settings = AppSettings(
        provider=ProviderSettings(
            parameter_model="tiny-parameter-model", story_model="large-story"
        )
    )
    service = AIService(settings, object())
    monkeypatch.setattr(service, "_provider", lambda: FlakyProvider())

    result = await service.extract_memories("已确认剧情", characters)

    assert used_models == ["tiny-parameter-model", "tiny-parameter-model"]
    assert {memory.owner_asset_id for memory in result.memories} == {"lin", "zhu"}
    assert "不得遗漏" in prompts[1]
