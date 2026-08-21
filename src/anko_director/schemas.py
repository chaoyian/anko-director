from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class KeepMode(StrEnum):
    HIGHEST = "highest"
    LOWEST = "lowest"


class DiceSpec(BaseModel):
    count: Annotated[int, Field(ge=1, le=100)] = 1
    sides: Annotated[int, Field(ge=2, le=100_000)]
    modifier: Annotated[int, Field(ge=-1_000_000, le=1_000_000)] = 0
    multiplier: Annotated[int, Field(ge=1, le=1_000)] = 1
    keep: Annotated[int | None, Field(ge=1, le=100)] = None
    keep_mode: KeepMode | None = None

    @model_validator(mode="after")
    def validate_keep(self) -> DiceSpec:
        if self.keep is not None and self.keep > self.count:
            raise ValueError("保留骰数量不能大于投骰数量")
        if (self.keep is None) != (self.keep_mode is None):
            raise ValueError("keep 与 keep_mode 必须同时设置")
        return self


class WeightedOption(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=500)
    weight: Annotated[int, Field(ge=1, le=100_000)] = 1


class DiceOperation(BaseModel):
    kind: Literal["dice"] = "dice"
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=500)
    character_name: str | None = Field(default=None, min_length=1, max_length=200)
    target_asset_id: str | None = None
    dice: DiceSpec
    secret: bool = False
    target: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    comparison: Literal["lte", "gte"] | None = None

    @model_validator(mode="after")
    def validate_target(self) -> DiceOperation:
        if (self.target is None) != (self.comparison is None):
            raise ValueError("目标值与判定方式必须同时设置")
        return self


class TableOperation(BaseModel):
    kind: Literal["table"] = "table"
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=500)
    character_name: str | None = Field(default=None, min_length=1, max_length=200)
    target_asset_id: str | None = None
    options: Annotated[list[WeightedOption], Field(min_length=1, max_length=500)]
    draw_count: Annotated[int, Field(ge=1, le=100)] = 1
    replace: bool = False
    die_sides: Annotated[int | None, Field(ge=2, le=100_000)] = None
    secret: bool = False

    @model_validator(mode="after")
    def validate_table(self) -> TableOperation:
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("选项 id 不能重复")
        if not self.replace and self.draw_count > len(self.options):
            raise ValueError("不放回抽取数量不能超过选项数")
        total = sum(option.weight for option in self.options)
        if self.die_sides is not None and total != self.die_sides:
            raise ValueError("显式骰面数必须等于所有选项权重之和")
        return self


RollOperation = Annotated[DiceOperation | TableOperation, Field(discriminator="kind")]


class RollProposal(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(default="", max_length=2000)
    character_name: str | None = Field(default=None, min_length=1, max_length=200)
    target_asset_id: str | None = None
    create_character: bool = False
    writeback_to_character: bool = Field(
        default=False,
        description=(
            "只有导演明确要求生成或永久重掷人物属性时才为 true。"
            "普通技能检定、剧情判定和连续尝试必须为 false。"
        ),
    )
    operations: Annotated[list[RollOperation], Field(min_length=1, max_length=100)]
    repeat_count: Annotated[int, Field(ge=1, le=100)] = 1
    sequence_mode: Literal["batch", "stepwise"] = "batch"
    warnings: list[str] = Field(default_factory=list, max_length=50)


class RollRequest(BaseModel):
    proposal: RollProposal
    seed_hex: str | None = None


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    premise: str = Field(default="", max_length=20_000)


class ParseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=20_000)


class StoryRequest(BaseModel):
    instruction: str = Field(default="继续故事", max_length=20_000)


class PublishRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class DiscordBindRequest(BaseModel):
    forum_channel_id: int
    post_name: str = Field(min_length=1, max_length=100)


class AssetKind(StrEnum):
    CHARACTER = "character"
    SCENE = "scene"
    PROP = "prop"
    RULE = "rule"


class AssetCreate(BaseModel):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=200)
    color: str = Field(default="#d89469", min_length=4, max_length=32)
    summary: str = Field(default="", max_length=2_000)
    body: str = Field(default="", max_length=100_000)
    x: float = Field(default=0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=0, ge=-1_000_000, le=1_000_000)


class AssetUpdate(BaseModel):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=200)
    color: str = Field(min_length=4, max_length=32)
    summary: str = Field(default="", max_length=2_000)
    body: str = Field(default="", max_length=100_000)


class AssetPosition(BaseModel):
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)


class CanvasView(BaseModel):
    pan_x: float = Field(ge=-1_000_000, le=1_000_000)
    pan_y: float = Field(ge=-1_000_000, le=1_000_000)
    zoom: float = Field(ge=0.25, le=3.0)


class HistoricalReference(BaseModel):
    commit_id: str
    entity_type: Literal["asset", "status_card", "relation", "outcome"]
    entity_id: str


class TurnPrepareRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=20_000)
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    historical_references: list[HistoricalReference] = Field(default_factory=list, max_length=100)
    proposal: RollProposal | None = None


class StatusCardCreate(BaseModel):
    target_asset_id: str
    duration_turns: int | None = Field(default=None, ge=1, le=100_000)
    description: str = Field(min_length=1, max_length=2_000)


class StatusCardUpdate(StatusCardCreate):
    pass


class RelationCreate(BaseModel):
    source_asset_id: str
    target_asset_id: str
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_endpoints(self) -> RelationCreate:
        if self.source_asset_id == self.target_asset_id:
            raise ValueError("关系起点和终点不能相同")
        return self


class RelationUpdate(RelationCreate):
    pass


class BranchCreate(BaseModel):
    from_commit_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)


class StatusCardOperation(BaseModel):
    action: Literal["create", "update", "delete"]
    status_card_id: str | None = None
    target_asset_id: str | None = None
    duration_turns: int | None = Field(default=None, ge=1, le=100_000)
    permanent: bool = False
    description: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_operation(self) -> StatusCardOperation:
        if self.action == "create" and (not self.target_asset_id or not self.description):
            raise ValueError("创建状态卡必须给出对象和说明")
        if self.action == "update" and (
            not self.status_card_id or not self.target_asset_id or not self.description
        ):
            raise ValueError("修改状态卡必须给出状态卡、对象和说明")
        if self.action == "delete" and not self.status_card_id:
            raise ValueError("删除状态卡必须给出状态卡 ID")
        if self.action != "delete" and not self.permanent and self.duration_turns is None:
            raise ValueError("非永久状态卡必须给出持续回合数")
        return self


class RelationOperation(BaseModel):
    action: Literal["create", "update", "delete"]
    relation_id: str | None = None
    source_asset_id: str | None = None
    target_asset_id: str | None = None
    label: str | None = Field(default=None, min_length=1, max_length=100)
    description: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_operation(self) -> RelationOperation:
        if self.action in {"create", "update"}:
            if not self.source_asset_id or not self.target_asset_id or not self.label:
                raise ValueError("创建或修改关系必须给出起点、终点和标签")
            if self.source_asset_id == self.target_asset_id:
                raise ValueError("关系起点和终点不能相同")
        if self.action in {"update", "delete"} and not self.relation_id:
            raise ValueError("修改或删除关系必须给出关系 ID")
        return self


class TurnOutcomeCandidate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4_000)
    participant_asset_ids: list[str] = Field(default_factory=list, max_length=100)
    previous_outcome_ids: list[str] = Field(default_factory=list, max_length=100)


class WorldDelta(BaseModel):
    outcome: TurnOutcomeCandidate
    status_operations: list[StatusCardOperation] = Field(default_factory=list, max_length=100)
    relation_operations: list[RelationOperation] = Field(default_factory=list, max_length=100)


class ChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class CharacterStatCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    notation: str = Field(default="1d100", min_length=2, max_length=80)


class CharacterStatUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    notation: str = Field(min_length=2, max_length=80)
    value: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    locked: bool = False


class CharacterStatBatchItem(CharacterStatUpdate):
    id: str


class CharacterStatBatchUpdate(BaseModel):
    stats: Annotated[list[CharacterStatBatchItem], Field(min_length=1, max_length=200)]


class CharacterTemplateApply(BaseModel):
    template_id: Literal["anko", "coc7"]
    mode: Literal["merge", "replace"] = "merge"
    roll_now: bool = False


class CocStateUpdate(BaseModel):
    max_hp: int = Field(ge=1, le=100_000)
    current_hp: int = Field(ge=0, le=100_000)
    armor: int = Field(default=0, ge=0, le=100_000)
    major_wound: bool = False
    unconscious: bool = False
    dying: bool = False
    dead: bool = False

    @model_validator(mode="after")
    def validate_hp(self) -> CocStateUpdate:
        if self.current_hp > self.max_hp:
            raise ValueError("当前生命值不能高于上限")
        if self.dead and self.current_hp != 0:
            raise ValueError("死亡角色的当前生命值必须为 0")
        return self


class CocCombatAction(BaseModel):
    request_id: str = Field(min_length=8, max_length=100)
    attacker_asset_id: str
    defender_asset_id: str
    attack_kind: Literal["melee", "firearm", "maneuver", "first_aid"] = "melee"
    defense: Literal["dodge", "fight_back", "none", "dive_for_cover"] = "dodge"
    attack_skill: int | None = Field(default=None, ge=0, le=100)
    defense_skill: int | None = Field(default=None, ge=0, le=100)
    bonus_dice: int = Field(default=0, ge=-2, le=2)
    weapon_name: str = Field(default="徒手", min_length=1, max_length=200)
    damage: str = Field(default="1d3", min_length=1, max_length=80)
    use_damage_bonus: bool = True
    impaling: bool = False
    shots: int = Field(default=1, ge=1, le=3)
    point_blank: bool = False
    outnumbered: bool = False
    maneuver_effect: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_combat_action(self) -> CocCombatAction:
        if self.attacker_asset_id == self.defender_asset_id and self.attack_kind != "first_aid":
            raise ValueError("攻击者和防御者不能是同一个人物")
        if self.attack_kind == "firearm" and self.defense == "fight_back":
            raise ValueError("枪械攻击不能用反击应对")
        if self.attack_kind == "firearm" and self.defense == "dodge":
            raise ValueError("枪械攻击不能直接闪避；请改用扑向掩体")
        if self.attack_kind != "firearm" and self.defense == "dive_for_cover":
            raise ValueError("扑向掩体只用于枪械攻击")
        if self.attack_kind != "firearm" and self.shots != 1:
            raise ValueError("只有手枪速射可在一轮内进行多次攻击")
        if self.attack_kind == "first_aid" and self.defense != "none":
            raise ValueError("急救动作不进行对抗防御")
        return self


class CharacterTraitCreate(BaseModel):
    label: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=5_000)


class TraitPoolDrawRequest(BaseModel):
    label: str = Field(default="特殊特质抽取", min_length=1, max_length=500)
    options: Annotated[list[WeightedOption], Field(min_length=1, max_length=500)]
    draw_count: Annotated[int, Field(ge=1, le=100)] = 1
    replace: bool = False


class MemoryGenerateRequest(BaseModel):
    turn_id: str | None = None


class MemoryCandidate(BaseModel):
    owner_asset_id: str
    involved_asset_ids: list[str] = Field(default_factory=list, max_length=100)
    summary: str = Field(min_length=1, max_length=2_000)
    interpretation: str = Field(default="", max_length=4_000)
    emotion: str = Field(default="", max_length=500)
    importance: int = Field(default=3, ge=1, le=5)


class MemoryExtraction(BaseModel):
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=100)
