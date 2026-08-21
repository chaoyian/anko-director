from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ..config import AppSettings, ConfigStore
from ..schemas import (
    DiceOperation,
    DiceSpec,
    MemoryExtraction,
    RollProposal,
    TableOperation,
    WeightedOption,
    WorldDelta,
)
from .providers import (
    AIProvider,
    AnthropicProvider,
    OpenAIProvider,
    PoeProvider,
    ProviderError,
)

PARAMETER_SYSTEM = """你是安科游戏的规则解析器，不是随机数生成器。
把导演的自然语言整理成严格的投骰方案。绝不编造、预判或模拟骰子结果。
未指定人物能力值规则时，每项使用独立 1d100。
未指定特殊能力抽取数量时，默认不放回抽取 1 个。
显式指定 d4 至 d12 的分支骰时，各选项整数权重总和必须等于骰面数。
导演必须看到并确认方案后，外部 Python 引擎才会真正投骰。
普通剧情检定绝不能修改人物卡，writeback_to_character 必须为 false；
只有导演明确要求生成或永久重掷人物属性时，才设为 true。
所有 operation id 和 option id 使用简短稳定的 ASCII 标识。"""

STORY_SYSTEM = """你是安科游戏的叙事助手。导演拥有最终决定权。
严格尊重已发生事件、人物状态和真实骰子结果，不修改、不补投、不伪造随机结果。
引用资产是本回合的确定事实；未在资产或事件中定义的信息必须保持克制，不得冒充既定设定。
必须先用“【判定说明】”说明本次点数造成的成功程度、效果和意义，再用“【故事】”续写正文。
判定说明应简洁明确，不能重复投骰；故事应可直接编辑和发布。
界面按纯文本显示，请使用短段落，不要输出 Markdown 标记（例如 #、** 或代码围栏）。
除非导演明确要求长篇展开，单回合判定说明与故事合计控制在 600 至 1000 个中文字符；
这是连续故事的一回合，应留下能承接下一轮的具体变化，不要在每一轮重复完整背景。
不要声称自己执行了任何工具。"""

CHAT_SYSTEM = """你是安科游戏导演的本地创作助手。
这是一段非正史讨论，不得擅自投骰、修改存档或宣称内容已经发布。
回答应简洁、可操作，并严格区分已知设定与建议。"""

MEMORY_SYSTEM = """你是安科游戏的角色记忆整理器。
只能根据给定的已确认剧情，为指定角色分别生成一条主观记忆。
客观发生的事情写入 summary，角色自己的理解写入 interpretation；两者不得混淆。
每条记忆必须使用给定的 owner_asset_id，并保留涉及角色的 asset id。
不得创建新角色，不得添加剧情中没有发生的事实。"""

WORLD_DELTA_SYSTEM = """你是安科游戏的回合状态整理器。
只根据本回合已确认故事，生成一个事件结果，并对给定资产之间的关系和人物状态卡提出结构化增删改。
只能使用输入中给出的资产、关系和状态卡 ID；不得创建、删除或改名资产，不得靠名字猜测对象。
状态卡只包含对象、持续回合数或永久、简短说明。瞬时影响使用 1 回合，
明确持续的影响按剧情给出正整数，无法判断结束时间才设为永久。
update 和 delete 必须使用现有 ID；没有明确变化时返回空操作列表。
关系标签应简短，只有剧情明确建立、改变或结束关系时才操作。
事件结果必须概括本回合真正发生的结果，不复述背景，不虚构骰外事实。"""


class AIServiceError(RuntimeError):
    pass


class AIService:
    def __init__(self, settings: AppSettings, secrets: ConfigStore) -> None:
        self.settings = settings
        self.secrets = secrets

    def _provider(self) -> AIProvider:
        name = self.settings.provider.provider.lower()
        secret_name = {
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
            "poe": "poe_api_key",
        }.get(name)
        if not secret_name:
            raise AIServiceError(f"不支持的模型厂商：{name}")
        api_key = self.secrets.get_secret(secret_name)
        if not api_key:
            raise AIServiceError(f"尚未配置 {name} API Key")
        provider_type = {
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
            "poe": PoeProvider,
        }[name]
        return provider_type(api_key, self.settings.provider.base_url)

    async def parse_proposal(self, instruction: str) -> RollProposal:
        """Always routes parameter extraction through the configured small model."""
        try:
            provider = self._provider()
        except AIServiceError:
            return heuristic_proposal(instruction)
        model = self.settings.provider.parameter_model
        if not model.strip():
            raise AIServiceError("必须配置用于传参的小模型")
        schema = RollProposal.model_json_schema()
        prompt = f"请解析以下导演指令，只提交投骰方案：\n\n{instruction}"
        last_error = ""
        for _attempt in range(3):
            try:
                raw = await provider.structured(model, PARAMETER_SYSTEM, prompt, schema)
                return RollProposal.model_validate(raw)
            except (ProviderError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                prompt = (
                    f"上一份方案未通过本地校验：{last_error}\n"
                    "重新输出完整方案，不要输出解释或随机结果。\n\n"
                    f"原始导演指令：{instruction}"
                )
        raise AIServiceError(f"小模型连续三次未能生成合法投骰方案：{last_error}")

    async def write_story(
        self,
        campaign: dict[str, Any],
        events: list[dict[str, Any]],
        discord_messages: list[dict[str, Any]],
        instruction: str,
        assets: list[dict[str, Any]] | None = None,
        roll_result: dict[str, Any] | None = None,
        memories: list[dict[str, Any]] | None = None,
        roll_announcement: str = "",
        world_context: dict[str, Any] | None = None,
        historical_references: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str]:
        provider = self._provider()
        model = self.settings.provider.story_model
        context = {
            "game": {"title": campaign["title"], "premise": campaign["premise"]},
            "events": events[-40:],
            "discord_suggestions": discord_messages[-30:],
            "referenced_assets": assets or [],
            "current_roll": roll_result,
            "relevant_character_memories": memories or [],
            "active_world_graph": world_context or {},
            "historical_references": historical_references or [],
        }
        prompt = (
            f"导演要求：{instruction}\n\n"
            f"本次投骰公开播报（必须据此先作判定）：\n{roll_announcement}\n\n"
            f"以下是本地权威上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        )
        try:
            content = await provider.text(model, STORY_SYSTEM, prompt)
        except ProviderError as exc:
            raise AIServiceError(str(exc)) from exc
        return content.strip(), self.settings.provider.provider, model

    async def analyze_world_delta(
        self,
        story: str,
        assets: list[dict[str, Any]],
        status_cards: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        recent_outcomes: list[dict[str, Any]],
    ) -> WorldDelta:
        allowed_ids = {asset["id"] for asset in assets}
        fallback = WorldDelta(
            outcome={
                "title": "本回合结果",
                "summary": story.strip()[:1000] or "本回合已经完成。",
                "participant_asset_ids": sorted(allowed_ids),
                "previous_outcome_ids": [],
            }
        )
        try:
            provider = self._provider()
        except AIServiceError:
            return fallback
        model = self.settings.provider.parameter_model
        context = {
            "allowed_assets": [
                {"id": asset["id"], "name": asset["name"], "kind": asset["kind"]}
                for asset in assets
            ],
            "active_status_cards": status_cards,
            "active_relations": relations,
            "recent_outcomes": recent_outcomes[-10:],
            "confirmed_story": story,
        }
        prompt = json.dumps(context, ensure_ascii=False, indent=2)
        schema = WorldDelta.model_json_schema()
        last_error = ""
        for attempt in range(2):
            try:
                raw = await provider.structured(model, WORLD_DELTA_SYSTEM, prompt, schema)
                delta = WorldDelta.model_validate(raw)
                participants = set(delta.outcome.participant_asset_ids)
                if not participants.issubset(allowed_ids):
                    raise ValueError("事件结果引用了未授权资产")
                return delta
            except (ProviderError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                if attempt == 0:
                    prompt = (
                        f"上一份世界变化未通过本地校验：{last_error}\n"
                        "请只使用允许的 ID，重新输出完整结果。\n\n"
                        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
                    )
        raise AIServiceError(f"小模型连续两次未能生成合法世界变化：{last_error}")

    async def local_chat(
        self,
        campaign: dict[str, Any],
        messages: list[dict[str, Any]],
        content: str,
    ) -> tuple[str, str, str]:
        provider = self._provider()
        model = self.settings.provider.story_model
        context = {
            "game": {"title": campaign["title"], "premise": campaign["premise"]},
            "recent_chat": [
                {
                    "author": message["author_name"],
                    "source": message["source"],
                    "content": message["content"],
                }
                for message in messages[-30:]
            ],
        }
        prompt = (
            f"导演消息：{content}\n\n"
            f"本地讨论上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        )
        try:
            reply = await provider.text(model, CHAT_SYSTEM, prompt)
        except ProviderError as exc:
            raise AIServiceError(str(exc)) from exc
        return reply.strip(), self.settings.provider.provider, model

    async def extract_memories(
        self,
        story: str,
        characters: list[dict[str, Any]],
    ) -> MemoryExtraction:
        allowed_ids = {character["id"] for character in characters}
        if not characters:
            return MemoryExtraction()
        try:
            provider = self._provider()
        except AIServiceError:
            excerpt = story.strip()[:500]
            return MemoryExtraction(
                memories=[
                    {
                        "owner_asset_id": character["id"],
                        "involved_asset_ids": sorted(allowed_ids),
                        "summary": excerpt,
                        "interpretation": "这个角色记住了本回合发生的事情。",
                        "emotion": "待导演补充",
                        "importance": 3,
                    }
                    for character in characters
                ]
            )
        model = self.settings.provider.parameter_model
        context = [
            {
                "id": character["id"],
                "name": character["name"],
                "summary": character.get("summary", ""),
                "body": character.get("body", ""),
                "derived_facts": character.get("derived_facts", {}),
            }
            for character in characters
        ]
        prompt = (
            f"指定角色：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            f"已确认剧情：\n{story}"
        )
        last_error = ""
        schema = MemoryExtraction.model_json_schema()
        for attempt in range(2):
            try:
                raw = await provider.structured(model, MEMORY_SYSTEM, prompt, schema)
                extraction = MemoryExtraction.model_validate(raw)
                owners = {memory.owner_asset_id for memory in extraction.memories}
                if owners != allowed_ids:
                    raise ValueError("必须为每个指定角色各生成一条记忆，且不能重复")
                if any(
                    not set(memory.involved_asset_ids).issubset(allowed_ids)
                    for memory in extraction.memories
                ):
                    raise ValueError("记忆引用了本回合之外的人物")
                return extraction
            except (ProviderError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                if attempt == 0:
                    prompt = (
                        f"上一份角色记忆未通过本地校验：{last_error}\n"
                        "请纠正并重新输出完整结果。必须严格为下面每个 owner_asset_id "
                        "各生成一条，不得遗漏、重复或使用角色名代替 ID：\n"
                        f"{json.dumps(sorted(allowed_ids), ensure_ascii=False)}\n\n"
                        f"原始指定角色：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
                        f"已确认剧情：\n{story}"
                    )
        raise AIServiceError(f"小模型连续两次未能生成合法角色记忆：{last_error}")


def _safe_id(text: str, index: int) -> str:
    ascii_id = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return ascii_id[:60] or f"item_{index}"


def heuristic_proposal(instruction: str) -> RollProposal:
    """Offline fallback for setup/demo; cloud small models remain the normal parser."""
    operations: list[DiceOperation | TableOperation] = []
    warnings = ["未配置模型 API Key，当前方案由本地基础解析器生成，请仔细检查。"]

    ability_match = re.search(r"(?:能力值|属性)[^（(]*[（(]([^）)]+)[）)]", instruction)
    if ability_match:
        names = [name.strip() for name in re.split(r"[/／、,，]", ability_match.group(1))]
        for index, name in enumerate(filter(None, names), start=1):
            operations.append(
                DiceOperation(id=f"stat_{index}", label=name, dice=DiceSpec(count=1, sides=100))
            )

    numbered = re.findall(r"(?m)^\s*\d+[.、．]\s*(.+?)\s*$", instruction)
    if numbered:
        options = [
            WeightedOption(id=f"trait_{index}", label=label.strip(), weight=1)
            for index, label in enumerate(numbered, start=1)
        ]
        operations.append(
            TableOperation(
                id="special_trait",
                label="特殊能力抽取",
                options=options,
                draw_count=1,
                replace=False,
            )
        )

    if not operations:
        notations = re.findall(r"(?i)(?:\d+)?d\d+(?:k[hl]\d+)?(?:[+-]\d+)?", instruction)
        from ..dice import parse_dice_notation

        for index, notation in enumerate(notations, start=1):
            operations.append(
                DiceOperation(
                    id=f"roll_{index}",
                    label=f"投骰 {notation}",
                    dice=parse_dice_notation(notation),
                )
            )

    if not operations:
        operations.append(
            DiceOperation(id="default_roll", label="默认判定", dice=DiceSpec(sides=100))
        )
        warnings.append("没有识别出明确骰式，按默认规则生成了 1d100。")
    create_character = bool(re.search(r"(?:建立|创建|新建)\s*.+?\s*人物卡", instruction))
    return RollProposal(
        title="导演投骰方案",
        explanation="确认前不会消耗随机数；可以直接编辑结构化方案。",
        character_name=(
            character_match.group(1).strip()
            if (character_match := re.search(r"建立\s*(.+?)\s*人物卡", instruction))
            else None
        ),
        create_character=create_character,
        writeback_to_character=create_character,
        operations=operations,
        warnings=warnings,
    )
