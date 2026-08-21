from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..ai import AIService, AIServiceError
from ..character_templates import template_catalog
from ..coc import resolve_combat_action, roll_percentile
from ..config import (
    AppSettings,
    ConfigStore,
    ProviderSettings,
    database_path,
    public_settings,
)
from ..database import Database
from ..dice import DeterministicRandom, execute_proposal, parse_dice_notation
from ..discord_service import DiscordService
from ..schemas import (
    AssetCreate,
    AssetPosition,
    AssetUpdate,
    BranchCreate,
    CampaignCreate,
    CanvasView,
    CharacterStatBatchUpdate,
    CharacterStatCreate,
    CharacterStatUpdate,
    CharacterTemplateApply,
    CharacterTraitCreate,
    ChatMessageRequest,
    CocCombatAction,
    CocStateUpdate,
    DiceOperation,
    DiscordBindRequest,
    MemoryGenerateRequest,
    ParseRequest,
    PublishRequest,
    RelationCreate,
    RelationUpdate,
    RollProposal,
    StatusCardCreate,
    StatusCardUpdate,
    StoryRequest,
    TableOperation,
    TraitPoolDrawRequest,
    TurnPrepareRequest,
    WorldDelta,
)

WEB_DIR = Path(__file__).parent


def create_app(
    database: Database | None = None,
    config_store: ConfigStore | None = None,
    start_integrations: bool = True,
) -> FastAPI:
    db = database or Database(database_path())
    store = config_store or ConfigStore()
    settings = store.load()
    db.initialize()
    ai = AIService(settings, store)
    discord_service = DiscordService(db, store.get_secret("discord_bot_token"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_integrations:
            await discord_service.start()
        yield
        if start_integrations:
            await discord_service.stop()

    app = FastAPI(title="Anko Director", version="0.7.0", lifespan=lifespan)
    app.state.db = db
    app.state.config_store = store
    app.state.settings = settings
    app.state.ai = ai
    app.state.discord = discord_service

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.exception_handler(KeyError)
    async def key_error_handler(_: Request, exc: KeyError):
        return HTMLResponse(str(exc.args[0]), status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "campaigns": db.list_campaigns(),
                "settings": public_settings(app.state.settings, store),
                "discord_ready": app.state.discord.ready,
                "active_page": "archive",
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": public_settings(app.state.settings, store),
                "discord_ready": app.state.discord.ready,
                "active_page": "settings",
            },
        )

    @app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
    async def campaign_page(request: Request, campaign_id: str):
        assets = db.list_assets(campaign_id)
        return templates.TemplateResponse(
            request,
            "campaign.html",
            {
                "campaign": db.get_campaign(campaign_id),
                "events": db.list_events(campaign_id),
                "characters": db.list_characters(campaign_id),
                "assets": assets,
                "character_stats": {
                    asset["id"]: db.ensure_character_stats(asset["id"])
                    for asset in assets
                    if asset["kind"] == "character"
                },
                "coc_profiles": db.list_coc_profiles(campaign_id),
                "canvas_view": db.get_canvas_view(campaign_id),
                "status_cards": db.list_status_cards(campaign_id),
                "relations": db.list_relations(campaign_id),
                "outcomes": db.list_outcomes(campaign_id),
                "timeline": db.get_timeline(campaign_id),
                "chat_messages": db.list_chat_messages(campaign_id),
                "discord_messages": db.list_discord_messages(campaign_id),
                "discord_ready": app.state.discord.ready,
                "active_page": "create",
            },
        )

    @app.get("/campaigns/{campaign_id}/characters/{asset_id}", response_class=HTMLResponse)
    async def character_page(request: Request, campaign_id: str, asset_id: str):
        campaign = db.get_campaign(campaign_id)
        asset = db.get_asset(asset_id)
        if asset["campaign_id"] != campaign_id or asset["kind"] != "character":
            raise HTTPException(404, "人物资产不存在")
        return templates.TemplateResponse(
            request,
            "character.html",
            {
                "campaign": campaign,
                "asset": asset,
                "stats": db.ensure_character_stats(asset_id),
                "traits": db.list_character_traits(asset_id),
                "memories": db.list_character_memories(campaign_id, asset_id),
                "templates": template_catalog(),
                "coc_profile": db.get_coc_profile(asset_id),
                "active_page": "create",
            },
        )

    @app.get("/campaigns/{campaign_id}/memories", response_class=HTMLResponse)
    async def memories_page(request: Request, campaign_id: str):
        campaign = db.get_campaign(campaign_id)
        characters = [
            asset for asset in db.list_assets(campaign_id) if asset["kind"] == "character"
        ]
        return templates.TemplateResponse(
            request,
            "memories.html",
            {
                "campaign": campaign,
                "characters": characters,
                "memories": db.list_character_memories(campaign_id),
                "active_page": "create",
            },
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "discord_configured": app.state.discord.configured,
            "discord_ready": app.state.discord.ready,
            "parameter_model": app.state.settings.provider.parameter_model,
            "story_model": app.state.settings.provider.story_model,
        }

    @app.get("/api/campaigns")
    async def list_campaigns() -> list[dict[str, Any]]:
        return db.list_campaigns()

    @app.post("/api/campaigns", status_code=201)
    async def create_campaign(payload: CampaignCreate) -> dict[str, Any]:
        return db.create_campaign(payload.title, payload.premise)

    @app.get("/api/campaigns/{campaign_id}")
    async def get_campaign(campaign_id: str) -> dict[str, Any]:
        campaign = db.get_campaign(campaign_id)
        campaign["events"] = db.list_events(campaign_id)
        campaign["characters"] = db.list_characters(campaign_id)
        campaign["assets"] = db.list_assets(campaign_id)
        campaign["canvas_view"] = db.get_canvas_view(campaign_id)
        campaign["status_cards"] = db.list_status_cards(campaign_id)
        campaign["relations"] = db.list_relations(campaign_id)
        campaign["outcomes"] = db.list_outcomes(campaign_id)
        campaign["timeline"] = db.get_timeline(campaign_id)
        campaign["chat_messages"] = db.list_chat_messages(campaign_id)
        campaign["discord_messages"] = db.list_discord_messages(campaign_id)
        return campaign

    @app.get("/api/campaigns/{campaign_id}/assets")
    async def list_assets(campaign_id: str) -> list[dict[str, Any]]:
        return db.list_assets(campaign_id)

    @app.post("/api/campaigns/{campaign_id}/assets", status_code=201)
    async def create_asset(campaign_id: str, payload: AssetCreate) -> dict[str, Any]:
        return db.create_asset(
            campaign_id,
            payload.kind.value,
            payload.name,
            payload.color,
            payload.summary,
            payload.body,
            payload.x,
            payload.y,
        )

    @app.put("/api/assets/{asset_id}")
    async def update_asset(asset_id: str, payload: AssetUpdate) -> dict[str, Any]:
        return db.update_asset(
            asset_id,
            payload.kind.value,
            payload.name,
            payload.color,
            payload.summary,
            payload.body,
        )

    @app.patch("/api/assets/{asset_id}/position")
    async def update_asset_position(asset_id: str, payload: AssetPosition) -> dict[str, Any]:
        return db.update_asset_position(asset_id, payload.x, payload.y)

    @app.post("/api/assets/{asset_id}/archive")
    async def archive_asset(asset_id: str) -> dict[str, Any]:
        return db.archive_asset(asset_id)

    @app.get("/api/campaigns/{campaign_id}/status-cards")
    async def list_status_cards(campaign_id: str) -> list[dict[str, Any]]:
        db.get_campaign(campaign_id)
        return db.list_status_cards(campaign_id)

    @app.post("/api/campaigns/{campaign_id}/status-cards", status_code=201)
    async def create_status_card(campaign_id: str, payload: StatusCardCreate) -> dict[str, Any]:
        try:
            return db.create_status_card(
                campaign_id,
                payload.target_asset_id,
                payload.duration_turns,
                payload.description,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/status-cards/{status_card_id}")
    async def update_status_card(status_card_id: str, payload: StatusCardUpdate) -> dict[str, Any]:
        try:
            return db.update_status_card(
                status_card_id,
                payload.target_asset_id,
                payload.duration_turns,
                payload.description,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/status-cards/{status_card_id}")
    async def delete_status_card(status_card_id: str) -> dict[str, Any]:
        return db.end_status_card(status_card_id)

    @app.get("/api/campaigns/{campaign_id}/relations")
    async def list_relations(campaign_id: str) -> list[dict[str, Any]]:
        db.get_campaign(campaign_id)
        return db.list_relations(campaign_id)

    @app.post("/api/campaigns/{campaign_id}/relations", status_code=201)
    async def create_relation(campaign_id: str, payload: RelationCreate) -> dict[str, Any]:
        try:
            return db.create_relation(
                campaign_id,
                payload.source_asset_id,
                payload.target_asset_id,
                payload.label,
                payload.description,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/relations/{relation_id}")
    async def update_relation(relation_id: str, payload: RelationUpdate) -> dict[str, Any]:
        try:
            return db.update_relation(
                relation_id,
                payload.source_asset_id,
                payload.target_asset_id,
                payload.label,
                payload.description,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/relations/{relation_id}")
    async def delete_relation(relation_id: str) -> dict[str, Any]:
        return db.end_relation(relation_id)

    @app.get("/api/campaigns/{campaign_id}/outcomes")
    async def list_outcomes(campaign_id: str) -> list[dict[str, Any]]:
        db.get_campaign(campaign_id)
        return db.list_outcomes(campaign_id)

    @app.get("/api/campaigns/{campaign_id}/timeline")
    async def get_timeline(campaign_id: str) -> dict[str, Any]:
        return db.get_timeline(campaign_id)

    @app.get("/api/campaigns/{campaign_id}/world")
    async def get_world(campaign_id: str, commit_id: str | None = None) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        if commit_id:
            try:
                return db.get_world_at_commit(campaign_id, commit_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return {
            "commit": None,
            "world": db.build_world_snapshot(campaign_id),
            "active_branch": db.get_active_branch(campaign_id),
        }

    @app.post("/api/campaigns/{campaign_id}/branches", status_code=201)
    async def create_branch(campaign_id: str, payload: BranchCreate) -> dict[str, Any]:
        try:
            return db.create_branch(campaign_id, payload.from_commit_id, payload.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/branches/{branch_id}/activate")
    async def activate_branch(branch_id: str) -> dict[str, Any]:
        return db.activate_branch(branch_id)

    def require_character_asset(asset_id: str) -> dict[str, Any]:
        asset = db.get_asset(asset_id)
        if asset["kind"] != "character" or asset["archived"]:
            raise HTTPException(422, "目标不是有效人物资产")
        return asset

    @app.get("/api/assets/{asset_id}/character")
    async def get_character_profile(asset_id: str) -> dict[str, Any]:
        asset = require_character_asset(asset_id)
        return {
            "asset": asset,
            "stats": db.ensure_character_stats(asset_id),
            "traits": db.list_character_traits(asset_id),
            "coc_profile": db.get_coc_profile(asset_id),
        }

    @app.get("/api/character-templates")
    async def list_character_templates() -> list[dict[str, str]]:
        return template_catalog()

    @app.post("/api/assets/{asset_id}/stats/apply-template")
    async def apply_character_template(
        asset_id: str, payload: CharacterTemplateApply
    ) -> dict[str, Any]:
        require_character_asset(asset_id)
        try:
            stats = db.apply_character_template(asset_id, payload.template_id, payload.mode)
            if payload.roll_now:
                pending = [stat for stat in stats if stat["value"] is None and not stat["locked"]]
                if pending:
                    rolled = roll_stats(pending)
                    stats = rolled["stats"]
            profile = db.sync_coc_profile(
                asset_id,
                reset_hp=payload.template_id == "coc7" and payload.mode == "replace",
            )
            return {"stats": stats, "coc_profile": profile}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/assets/{asset_id}/stats/batch")
    async def batch_update_character_stats(
        asset_id: str, payload: CharacterStatBatchUpdate
    ) -> dict[str, Any]:
        require_character_asset(asset_id)
        try:
            for stat in payload.stats:
                parse_dice_notation(stat.notation)
            stats = db.batch_update_character_stats(
                asset_id, [item.model_dump(mode="json") for item in payload.stats]
            )
            return {"stats": stats, "coc_profile": db.get_coc_profile(asset_id)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/assets/{asset_id}/stats", status_code=201)
    async def create_character_stat(asset_id: str, payload: CharacterStatCreate) -> dict[str, Any]:
        require_character_asset(asset_id)
        try:
            parse_dice_notation(payload.notation)
            return db.create_character_stat(asset_id, payload.name, payload.notation)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/character-stats/{stat_id}")
    async def update_character_stat(stat_id: str, payload: CharacterStatUpdate) -> dict[str, Any]:
        try:
            parse_dice_notation(payload.notation)
            return db.update_character_stat(
                stat_id, payload.name, payload.notation, payload.value, payload.locked
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/character-stats/{stat_id}", status_code=204)
    async def delete_character_stat(stat_id: str) -> None:
        try:
            db.delete_character_stat(stat_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    def roll_stats(stats: list[dict[str, Any]]) -> dict[str, Any]:
        if not stats:
            raise HTTPException(422, "没有需要投掷的属性")
        try:
            operations = [
                DiceOperation(
                    id=f"stat_{index}",
                    label=stat["name"],
                    dice=parse_dice_notation(stat["notation"]),
                )
                for index, stat in enumerate(stats, 1)
            ]
            proposal = RollProposal(title="人物属性投掷", operations=operations)
            result = execute_proposal(proposal)
            asset = require_character_asset(stats[0]["asset_id"])
            recorded = db.record_roll(asset["campaign_id"], None, result)
            for stat, operation in zip(stats, result["results"], strict=True):
                db.record_stat_roll(
                    stat["id"], operation["total"], recorded["event"]["id"], operation["notation"]
                )
            profile = db.sync_coc_profile(asset["id"])
            return {
                "result": recorded,
                "stats": db.list_character_stats(asset["id"]),
                "summary": format_roll_result(result),
                "coc_profile": profile,
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/character-stats/{stat_id}/roll")
    async def roll_character_stat(stat_id: str) -> dict[str, Any]:
        stat = db.get_character_stat(stat_id)
        if stat["locked"]:
            raise HTTPException(422, "该属性已经锁定")
        return roll_stats([stat])

    @app.post("/api/assets/{asset_id}/stats/roll-pending")
    async def roll_pending_character_stats(asset_id: str) -> dict[str, Any]:
        require_character_asset(asset_id)
        stats = [
            stat
            for stat in db.ensure_character_stats(asset_id)
            if stat["value"] is None and not stat["locked"]
        ]
        return roll_stats(stats)

    @app.post("/api/assets/{asset_id}/traits", status_code=201)
    async def create_character_trait(
        asset_id: str, payload: CharacterTraitCreate
    ) -> dict[str, Any]:
        require_character_asset(asset_id)
        return db.create_character_trait(asset_id, payload.label, payload.description)

    @app.post("/api/assets/{asset_id}/traits/draw")
    async def draw_character_traits(asset_id: str, payload: TraitPoolDrawRequest) -> dict[str, Any]:
        asset = require_character_asset(asset_id)
        try:
            proposal = RollProposal(
                title=payload.label,
                operations=[
                    TableOperation(
                        id="trait_pool",
                        label=payload.label,
                        options=payload.options,
                        draw_count=payload.draw_count,
                        replace=payload.replace,
                    )
                ],
            )
            result = execute_proposal(proposal)
            recorded = db.record_roll(asset["campaign_id"], None, result)
            created = [
                db.create_character_trait(
                    asset_id,
                    draw["selected"]["label"],
                    f"由特质池《{payload.label}》抽取",
                    "roll",
                    recorded["event"]["id"],
                )
                for draw in result["results"][0]["draws"]
            ]
            return {"result": recorded, "traits": created}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/assets/{asset_id}/coc-state")
    async def get_coc_state(asset_id: str) -> dict[str, Any]:
        require_character_asset(asset_id)
        return db.get_coc_profile(asset_id)

    @app.put("/api/assets/{asset_id}/coc-state")
    async def update_coc_state(asset_id: str, payload: CocStateUpdate) -> dict[str, Any]:
        require_character_asset(asset_id)
        return db.update_coc_state(asset_id, payload.model_dump(mode="json"))

    @app.get("/api/campaigns/{campaign_id}/coc/combatants")
    async def list_coc_combatants(campaign_id: str) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        return {"combatants": db.list_coc_profiles(campaign_id)}

    @app.post("/api/campaigns/{campaign_id}/coc/combat")
    async def execute_coc_combat(campaign_id: str, payload: CocCombatAction) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        duplicate = db.get_coc_action_by_request(payload.request_id)
        if duplicate:
            if duplicate["campaign_id"] != campaign_id:
                raise HTTPException(409, "战斗请求编号已被其他存档使用")
            return {
                "action": duplicate,
                "result": duplicate["result"],
                "combatants": db.list_coc_profiles(campaign_id),
                "replayed": True,
            }
        attacker_asset = require_character_asset(payload.attacker_asset_id)
        defender_asset = require_character_asset(payload.defender_asset_id)
        if (
            attacker_asset["campaign_id"] != campaign_id
            or defender_asset["campaign_id"] != campaign_id
        ):
            raise HTTPException(422, "战斗人物不属于当前存档")
        try:
            parse_dice_notation(payload.damage)
            attacker = db.get_coc_profile(payload.attacker_asset_id)
            defender = db.get_coc_profile(payload.defender_asset_id)
            if attacker["state"]["dead"] or attacker["state"]["unconscious"]:
                raise ValueError("攻击者已死亡或失去意识，不能行动")
            action_data = payload.model_dump(mode="json")
            result = resolve_combat_action(action_data, attacker, defender)
            if payload.defense == "dive_for_cover" and result.get("defense_roll", {}).get(
                "success"
            ):
                result["defender_state"]["skip_next_action"] = True
            db.apply_coc_combat_states(
                attacker["asset_id"],
                result["attacker_state"],
                defender["asset_id"],
                result["defender_state"],
            )
            encounter = db.get_or_create_coc_encounter(campaign_id)
            event = db.append_event(
                campaign_id,
                "coc_combat",
                {
                    "encounter_id": encounter["id"],
                    "round": encounter["round_number"],
                    "action": action_data,
                    "result": result,
                },
            )
            action = db.record_coc_action(
                encounter["id"], campaign_id, payload.request_id, event["id"], action_data, result
            )
            db.create_chat_message(
                campaign_id,
                "coc_combat",
                "anko-director",
                "COC 战斗裁定",
                result["announcement"],
                "local",
                "received",
                metadata={"event_id": event["id"], "encounter_id": encounter["id"]},
                message_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"anko-director:coc:{payload.request_id}")
                ),
            )
            return {
                "action": action,
                "result": result,
                "encounter": encounter,
                "combatants": db.list_coc_profiles(campaign_id),
                "replayed": False,
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/campaigns/{campaign_id}/coc/next-round")
    async def advance_coc_round(campaign_id: str) -> dict[str, Any]:
        current = db.get_or_create_coc_encounter(campaign_id)
        rng = DeterministicRandom.fresh()
        dying_checks: list[dict[str, Any]] = []
        for profile in db.list_coc_profiles(campaign_id):
            state = profile["state"]
            if not state["dying"] or state["dead"]:
                continue
            check = roll_percentile(rng, profile["constitution"])
            updated = dict(state)
            if not check["success"]:
                updated["dead"] = True
                updated["dying"] = False
                updated["unconscious"] = True
            db.update_coc_state(profile["asset_id"], updated)
            dying_checks.append(
                {
                    "asset_id": profile["asset_id"],
                    "name": profile["name"],
                    "roll": check,
                    "survived": check["success"],
                }
            )
        if dying_checks:
            check_lines = []
            for item in dying_checks:
                roll = item["roll"]
                outcome = "暂时撑住" if item["survived"] else "伤重死亡"
                check_lines.append(
                    f"{item['name']} {roll['roll']}/{roll['target']}"
                    f"（{roll['level_label']}），{outcome}"
                )
            lines = ["🫀 回合末濒死检定：" + "；".join(check_lines)]
            announcement = "\n".join(lines)
            event = db.append_event(
                campaign_id,
                "coc_dying_check",
                {
                    "encounter_id": current["id"],
                    "round": current["round_number"],
                    "seed_hex": rng.seed_hex,
                    "checks": dying_checks,
                },
            )
            db.create_chat_message(
                campaign_id,
                "coc_combat",
                "anko-director",
                "COC 战斗裁定",
                announcement,
                "local",
                "received",
                metadata={"event_id": event["id"], "encounter_id": current["id"]},
                message_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"anko-director:coc-dying:{event['id']}")
                ),
            )
        encounter = db.advance_coc_round(campaign_id)
        return {
            "encounter": encounter,
            "combatants": db.list_coc_profiles(campaign_id),
            "dying_checks": dying_checks,
        }

    @app.get("/api/campaigns/{campaign_id}/canvas-view")
    async def get_canvas_view(campaign_id: str) -> dict[str, float]:
        return db.get_canvas_view(campaign_id)

    @app.put("/api/campaigns/{campaign_id}/canvas-view")
    async def save_canvas_view(campaign_id: str, payload: CanvasView) -> dict[str, float]:
        return db.save_canvas_view(campaign_id, payload.pan_x, payload.pan_y, payload.zoom)

    @app.post("/api/campaigns/{campaign_id}/proposals")
    async def parse_proposal(campaign_id: str, payload: ParseRequest) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        try:
            proposal = await app.state.ai.parse_proposal(payload.instruction)
        except AIServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        proposal_id = db.save_proposal(
            campaign_id, payload.instruction, proposal.model_dump(mode="json")
        )
        return {"id": proposal_id, "proposal": proposal}

    @app.put("/api/proposals/{proposal_id}")
    async def update_proposal_preview(proposal_id: str, proposal: RollProposal) -> dict[str, Any]:
        original = db.get_proposal(proposal_id)
        new_id = db.save_proposal(
            original["campaign_id"], original["source_text"], proposal.model_dump(mode="json")
        )
        return {"id": new_id, "proposal": proposal}

    @app.post("/api/proposals/{proposal_id}/execute")
    async def roll_proposal(proposal_id: str) -> dict[str, Any]:
        saved = db.get_proposal(proposal_id)
        if saved["status"] != "pending":
            raise HTTPException(409, "该投骰提案已经执行，重投必须创建新提案")
        try:
            proposal = RollProposal.model_validate(saved["proposal"])
            result = execute_proposal(proposal)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        recorded = db.record_roll(saved["campaign_id"], proposal_id, result)
        if proposal.character_name and (
            proposal.create_character or proposal.writeback_to_character
        ):
            recorded["character"] = db.apply_roll_to_character(
                saved["campaign_id"], proposal.character_name, result
            )
        return recorded

    @app.post("/api/campaigns/{campaign_id}/turns/prepare")
    async def prepare_turn(campaign_id: str, payload: TurnPrepareRequest) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        try:
            assets = db.asset_snapshots(campaign_id, payload.asset_ids)
            historical_references = db.resolve_historical_references(
                campaign_id,
                [item.model_dump(mode="json") for item in payload.historical_references],
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        selected_ids = {item["id"] for item in assets}
        active_status_cards = [
            card
            for card in db.list_status_cards(campaign_id)
            if card["target_asset_id"] in selected_ids
        ]
        active_relations = [
            relation
            for relation in db.list_relations(campaign_id)
            if relation["source_asset_id"] in selected_ids
            and relation["target_asset_id"] in selected_ids
        ]
        asset_context = ""
        if assets:
            asset_context = "\n\n已引用资产（只可用于理解规则）：\n" + "\n".join(
                f"- {item['kind']}《{item['name']}》：{item['summary']}\n{item['body']}\n"
                f"派生事实：{item['derived_facts']}\n"
                f"人物属性：{item.get('stats', [])}\n"
                f"人物特质：{item.get('traits', [])}"
                for item in assets
            )
            asset_context += (
                f"\n有效状态卡：{active_status_cards}\n引用资产之间的关系：{active_relations}"
            )
        if historical_references:
            asset_context += "\n\n明确引用的历史内容（仅代表对应旧回合）：\n" + "\n".join(
                str(item) for item in historical_references
            )
        if payload.proposal is not None:
            proposal = payload.proposal
            proposal_source = "explicit"
        else:
            try:
                proposal = await app.state.ai.parse_proposal(payload.instruction + asset_context)
            except AIServiceError as exc:
                raise HTTPException(502, str(exc)) from exc
            proposal_source = "small_model"
        selected_characters = [item for item in assets if item["kind"] == "character"]
        selected_character_ids = {item["id"] for item in selected_characters}
        normalized_operations = []
        for operation in proposal.operations:
            operation_target = None
            if operation.target_asset_id:
                operation_target = require_character_asset(operation.target_asset_id)
                if operation_target["campaign_id"] != campaign_id:
                    raise HTTPException(422, "骰组中的人物不属于当前存档")
                if operation_target["id"] not in selected_character_ids:
                    raise HTTPException(422, "骰组中的人物必须先在资产桌布中引用")
            elif operation.character_name:
                matches = [
                    item for item in selected_characters if item["name"] == operation.character_name
                ]
                if len(matches) == 1:
                    operation_target = matches[0]
            normalized_operations.append(
                operation.model_copy(
                    update={
                        "target_asset_id": operation_target["id"] if operation_target else None,
                        "character_name": (
                            operation_target["name"]
                            if operation_target
                            else operation.character_name
                        ),
                    }
                )
            )
        proposal = proposal.model_copy(update={"operations": normalized_operations})
        target: dict[str, Any] | None = None
        if proposal.target_asset_id:
            explicit_target = require_character_asset(proposal.target_asset_id)
            if explicit_target["campaign_id"] != campaign_id:
                raise HTTPException(422, "显式骰组的人物不属于当前存档")
            if explicit_target["id"] not in {item["id"] for item in assets}:
                raise HTTPException(422, "显式骰组的人物必须先在资产桌布中引用")
            target = explicit_target
        elif len(selected_characters) == 1:
            target = selected_characters[0]
        elif len(selected_characters) > 1 and proposal.character_name:
            matches = [
                item for item in selected_characters if item["name"] == proposal.character_name
            ]
            if len(matches) == 1:
                target = matches[0]
        elif proposal.character_name:
            matches = db.find_character_assets_by_name(campaign_id, proposal.character_name)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                raise HTTPException(422, "存在多个同名人物，请在资产桌布中选中投骰目标")
        proposal = proposal.model_copy(
            update={
                "target_asset_id": target["id"] if target else None,
                "character_name": target["name"] if target else proposal.character_name,
            }
        )
        turn = db.create_turn(
            campaign_id,
            payload.instruction,
            assets,
            proposal.model_dump(mode="json"),
            historical_references,
        )
        return {
            "turn": turn,
            "preview": format_proposal_preview(proposal),
            "proposal_source": proposal_source,
        }

    @app.post("/api/turns/{turn_id}/roll-next")
    async def roll_next(turn_id: str) -> dict[str, Any]:
        turn = db.get_turn(turn_id)
        proposal = RollProposal.model_validate(turn["proposal"])
        if proposal.sequence_mode != "stepwise" or proposal.repeat_count < 2:
            raise HTTPException(409, "该回合不是分步连续骰")
        if turn["status"] == "completed":
            raise HTTPException(409, "该回合已经完成")
        operation_count = len(proposal.operations)
        existing_results = turn["roll_result"]["result"]["results"] if turn["roll_result"] else []
        current = len(existing_results) // operation_count
        if current >= proposal.repeat_count:
            return {
                "turn": turn,
                "complete": True,
                "current": current,
                "total": proposal.repeat_count,
            }
        single = proposal.model_copy(update={"repeat_count": 1, "sequence_mode": "batch"})
        result = execute_proposal(single)
        iteration = current + 1
        for item in result["results"]:
            item["iteration"] = iteration
            item["repeat_count"] = proposal.repeat_count
        recorded = db.record_roll(turn["campaign_id"], None, result)
        if proposal.target_asset_id and proposal.writeback_to_character:
            target = require_character_asset(proposal.target_asset_id)
            recorded["character"] = db.apply_roll_to_asset(
                target["id"], {**result, "event_id": recorded["event"]["id"]}
            )
        if turn["roll_result"]:
            aggregate = turn["roll_result"]
            aggregate["roll_ids"].append(recorded["roll_id"])
            aggregate["events"].append(recorded["event"])
            aggregate["event"] = recorded["event"]
            aggregate["result"]["seed_hex"].append(result["seed_hex"])
            aggregate["result"]["results"].extend(result["results"])
        else:
            aggregate = {
                "roll_id": recorded["roll_id"],
                "roll_ids": [recorded["roll_id"]],
                "event": recorded["event"],
                "events": [recorded["event"]],
                "result": {
                    "algorithm": "stepwise-series-v1",
                    "seed_hex": [result["seed_hex"]],
                    "proposal": proposal.model_dump(mode="json"),
                    "results": result["results"],
                },
            }
        turn = db.save_turn_roll_progress(turn_id, aggregate)
        return {
            "turn": turn,
            "complete": iteration >= proposal.repeat_count,
            "current": iteration,
            "total": proposal.repeat_count,
            "roll_summary": format_roll_result(result),
            "roll_announcement": format_roll_announcement(proposal, result),
        }

    @app.post("/api/turns/{turn_id}/roll")
    async def roll_turn(turn_id: str) -> dict[str, Any]:
        """Reveal a batch roll immediately, before the slower story request."""
        turn = db.get_turn(turn_id)
        proposal = RollProposal.model_validate(turn["proposal"])
        if proposal.sequence_mode == "stepwise" and proposal.repeat_count > 1:
            raise HTTPException(409, "分步连续骰请使用逐次投骰")
        if turn["roll_result"] is None:
            result = execute_proposal(proposal)
            recorded = db.record_roll(turn["campaign_id"], None, result)
            if proposal.target_asset_id and proposal.writeback_to_character:
                target = require_character_asset(proposal.target_asset_id)
                if target["campaign_id"] != turn["campaign_id"]:
                    raise HTTPException(422, "投骰目标不属于当前存档")
                recorded["character"] = db.apply_roll_to_asset(
                    target["id"], {**result, "event_id": recorded["event"]["id"]}
                )
            elif proposal.create_character and proposal.character_name:
                recorded["character"] = db.apply_roll_to_character(
                    turn["campaign_id"], proposal.character_name, result
                )
            turn = db.save_turn_roll(turn_id, recorded)
        return {
            "turn": turn,
            "roll_summary": format_roll_result(turn["roll_result"]["result"]),
            "roll_announcement": format_roll_announcement(proposal, turn["roll_result"]["result"]),
        }

    @app.post("/api/turns/{turn_id}/execute")
    async def execute_turn(turn_id: str) -> dict[str, Any]:
        turn = db.get_turn(turn_id)
        if turn["status"] == "completed":
            draft = db.get_draft(turn["draft_id"])
            completed_proposal = RollProposal.model_validate(turn["proposal"])
            commit = db.get_commit(turn["commit_id"]) if turn.get("commit_id") else None
            return {
                "turn": turn,
                "draft": draft,
                "roll_summary": format_roll_result(turn["roll_result"]["result"]),
                "roll_announcement": format_roll_announcement(
                    completed_proposal, turn["roll_result"]["result"]
                ),
                "delivery": "discord_queued" if draft["status"] == "published" else "local_draft",
                "commit": commit,
            }
        try:
            proposal = RollProposal.model_validate(turn["proposal"])
            if proposal.sequence_mode == "stepwise":
                result_count = (
                    len(turn["roll_result"]["result"]["results"]) if turn["roll_result"] else 0
                )
                expected = proposal.repeat_count * len(proposal.operations)
                if result_count < expected:
                    raise HTTPException(409, "连续骰尚未完成，请继续投下一骰")
            if turn["roll_result"] is None:
                result = execute_proposal(proposal)
                recorded = db.record_roll(turn["campaign_id"], None, result)
                if proposal.target_asset_id and proposal.writeback_to_character:
                    target = require_character_asset(proposal.target_asset_id)
                    if target["campaign_id"] != turn["campaign_id"]:
                        raise ValueError("投骰目标不属于当前存档")
                    recorded["character"] = db.apply_roll_to_asset(
                        target["id"], {**result, "event_id": recorded["event"]["id"]}
                    )
                elif proposal.create_character and proposal.character_name:
                    recorded["character"] = db.apply_roll_to_character(
                        turn["campaign_id"], proposal.character_name, result
                    )
                turn = db.save_turn_roll(turn_id, recorded)
            recorded = turn["roll_result"]
            campaign = db.get_campaign(turn["campaign_id"])
            roll_announcement = format_roll_announcement(proposal, recorded["result"])
            character_ids = [
                item["id"] for item in turn["asset_snapshots"] if item["kind"] == "character"
            ]
            selected_ids = {item["id"] for item in turn["asset_snapshots"]}
            story_status_cards = [
                card
                for card in db.list_status_cards(turn["campaign_id"])
                if card["target_asset_id"] in selected_ids
            ]
            story_relations = [
                relation
                for relation in db.list_relations(turn["campaign_id"])
                if relation["source_asset_id"] in selected_ids
                and relation["target_asset_id"] in selected_ids
            ]
            recent_outcomes = [
                outcome
                for outcome in db.list_outcomes(turn["campaign_id"])
                if selected_ids.intersection(outcome["participant_asset_ids"])
            ][-10:]
            content, provider, model = await app.state.ai.write_story(
                campaign,
                db.list_events(turn["campaign_id"]),
                db.list_chat_messages(turn["campaign_id"]),
                turn["instruction"],
                assets=turn["asset_snapshots"],
                roll_result=recorded,
                memories=db.memories_for_assets(turn["campaign_id"], character_ids),
                roll_announcement=roll_announcement,
                world_context={
                    "status_cards": story_status_cards,
                    "relations": story_relations,
                    "recent_outcomes": recent_outcomes,
                },
                historical_references=turn["historical_references"],
            )
            draft = db.save_draft(
                turn["campaign_id"], turn["instruction"], content, provider, model
            )
            db.create_chat_message(
                turn["campaign_id"],
                "director_prompt",
                "director",
                "你（导演）",
                turn["instruction"],
                "local",
                "received",
                metadata={
                    "turn_id": turn_id,
                    "branch_id": turn["branch_id"],
                    "canonical": True,
                },
                message_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"anko-director:turn:{turn_id}:director-prompt",
                    )
                ),
            )
            db.create_chat_message(
                turn["campaign_id"],
                "story_preview",
                "anko-director",
                "Anko Bot",
                f"{roll_announcement}\n\n{content}",
                "local",
                "preview",
                metadata={
                    "draft_id": draft["id"],
                    "turn_id": turn_id,
                    "branch_id": turn["branch_id"],
                },
                message_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"anko-director:turn:{turn_id}:story-preview",
                    )
                ),
            )
            delivery = "local_draft"
            if campaign["discord_thread_id"]:
                rolls = db.unpublished_rolls(turn["campaign_id"])
                roll_evidence = format_roll_evidence(rolls)
                branch = db.get_branch(turn["branch_id"])
                branch_marker = ""
                if branch["name"] != "主线":
                    turn_number = db.next_turn_number(turn["branch_id"])
                    branch_marker = f"【{branch['name']} · 第 {turn_number} 回】\n\n"
                outbox_content = (
                    f"{branch_marker}{roll_evidence}\n\n{roll_announcement}\n\n---\n\n{content}"
                    if roll_evidence
                    else f"{branch_marker}{roll_announcement}\n\n{content}"
                )
                db.publish_draft(draft["id"], content, outbox_content)
                db.mark_story_preview_published(draft["id"])
                await app.state.discord.flush_outbox_once()
                draft = db.get_draft(draft["id"])
                delivery = "discord_queued"
            characters = [require_character_asset(asset_id) for asset_id in character_ids]

            async def process_world_delta() -> dict[str, Any]:
                db.expire_status_cards(turn["campaign_id"], turn_id)
                try:
                    analyzer = getattr(app.state.ai, "analyze_world_delta", None)
                    if analyzer is None:
                        delta = WorldDelta(
                            outcome={
                                "title": "本回合结果",
                                "summary": content[:1000],
                                "participant_asset_ids": list(selected_ids),
                                "previous_outcome_ids": [],
                            }
                        )
                    else:
                        delta = await analyzer(
                            content,
                            turn["asset_snapshots"],
                            story_status_cards,
                            story_relations,
                            recent_outcomes,
                        )
                    dumped = delta.model_dump(mode="json")
                    applied = db.apply_world_delta(turn_id, dumped, list(selected_ids))
                    return {"status": "complete", "delta": dumped, "applied": applied}
                except Exception as exc:
                    fallback = db.create_turn_outcome(
                        turn_id,
                        "本回合结果",
                        content[:1000],
                        list(selected_ids),
                        [],
                        "failed",
                    )
                    db.save_world_analysis(turn_id, "failed", error=str(exc)[:2000])
                    return {"status": "failed", "error": str(exc), "outcome": fallback}

            async def process_memories() -> dict[str, Any]:
                if not characters:
                    db.set_memory_status(turn_id, "skipped")
                    return {"status": "skipped", "memories": []}
                try:
                    extraction = await app.state.ai.extract_memories(content, characters)
                    memories = db.save_character_memories(
                        turn["campaign_id"],
                        turn_id,
                        None,
                        [item.model_dump(mode="json") for item in extraction.memories],
                        app.state.settings.provider.parameter_model,
                    )
                    db.set_memory_status(turn_id, "complete")
                    return {"status": "complete", "memories": memories}
                except Exception as exc:
                    db.set_memory_status(turn_id, "failed")
                    return {"status": "failed", "error": str(exc), "memories": []}

            world_processing, memory_processing = await asyncio.gather(
                process_world_delta(), process_memories()
            )
            completed = db.complete_turn(turn_id, recorded, draft["id"])
            outcome = world_processing.get("applied", {}).get("outcome") or world_processing.get(
                "outcome"
            )
            commit = db.create_world_commit(
                turn["campaign_id"],
                turn_id,
                "turn",
                outcome["title"] if outcome else "完成故事回合",
                world_processing.get("delta", {}),
            )
            completed = db.get_turn(turn_id)
            return {
                "turn": completed,
                "draft": draft,
                "roll_summary": format_roll_result(recorded["result"]),
                "roll_announcement": roll_announcement,
                "delivery": delivery,
                "world_processing": world_processing,
                "memory_processing": memory_processing,
                "commit": commit,
            }
        except (AIServiceError, ValidationError, ValueError) as exc:
            db.fail_turn(turn_id, str(exc))
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/turns/{turn_id}/world-delta")
    async def get_turn_world_delta(turn_id: str) -> dict[str, Any]:
        db.get_turn(turn_id)
        return db.get_world_analysis(turn_id)

    @app.post("/api/turns/{turn_id}/world-delta/retry")
    async def retry_turn_world_delta(turn_id: str) -> dict[str, Any]:
        turn = db.get_turn(turn_id)
        if turn["status"] != "completed":
            raise HTTPException(409, "只能重试已完成回合的世界变化分析")
        try:
            analysis = db.get_world_analysis(turn_id)
            if analysis["status"] == "complete":
                return analysis
        except KeyError:
            pass
        selected_ids = {item["id"] for item in turn["asset_snapshots"]}
        cards = [
            card
            for card in db.list_status_cards(turn["campaign_id"])
            if card["target_asset_id"] in selected_ids
        ]
        relations = [
            relation
            for relation in db.list_relations(turn["campaign_id"])
            if relation["source_asset_id"] in selected_ids
            and relation["target_asset_id"] in selected_ids
        ]
        outcomes = db.list_outcomes(turn["campaign_id"])[-10:]
        draft = db.get_draft(turn["draft_id"])
        try:
            delta = await app.state.ai.analyze_world_delta(
                draft["content"], turn["asset_snapshots"], cards, relations, outcomes
            )
            dumped = delta.model_dump(mode="json")
            applied = db.apply_world_delta(turn_id, dumped, list(selected_ids))
            commit = db.create_world_commit(
                turn["campaign_id"],
                None,
                "correction",
                f"重试世界分析：{dumped['outcome']['title']}",
                dumped,
            )
            return {"status": "complete", "delta": dumped, "applied": applied, "commit": commit}
        except (AIServiceError, ValidationError, ValueError) as exc:
            db.save_world_analysis(turn_id, "failed", error=str(exc))
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/turns/{turn_id}/memories/retry")
    async def retry_turn_memories(turn_id: str) -> dict[str, Any]:
        turn = db.get_turn(turn_id)
        if turn["status"] != "completed":
            raise HTTPException(409, "只能重试已完成回合的人物记忆")
        character_ids = list(
            dict.fromkeys(
                item["id"] for item in turn["asset_snapshots"] if item["kind"] == "character"
            )
        )
        if not character_ids:
            db.set_memory_status(turn_id, "skipped")
            return {"status": "skipped", "memories": []}
        draft = db.get_draft(turn["draft_id"])
        characters = [require_character_asset(asset_id) for asset_id in character_ids]
        try:
            extraction = await app.state.ai.extract_memories(draft["content"], characters)
            memories = db.save_character_memories(
                turn["campaign_id"],
                turn_id,
                None,
                [item.model_dump(mode="json") for item in extraction.memories],
                app.state.settings.provider.parameter_model,
            )
            db.set_memory_status(turn_id, "complete")
            return {"status": "complete", "memories": memories}
        except AIServiceError as exc:
            db.set_memory_status(turn_id, "failed")
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/campaigns/{campaign_id}/memories")
    async def list_memories(
        campaign_id: str, owner_asset_id: str | None = None
    ) -> list[dict[str, Any]]:
        db.get_campaign(campaign_id)
        if owner_asset_id:
            asset = require_character_asset(owner_asset_id)
            if asset["campaign_id"] != campaign_id:
                raise HTTPException(422, "人物不属于当前存档")
        return db.list_character_memories(campaign_id, owner_asset_id)

    @app.post("/api/campaigns/{campaign_id}/memories/generate")
    async def generate_memories(campaign_id: str, payload: MemoryGenerateRequest) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        turn = (
            db.get_turn(payload.turn_id)
            if payload.turn_id
            else db.latest_completed_turn(campaign_id)
        )
        if turn["campaign_id"] != campaign_id or turn["status"] != "completed":
            raise HTTPException(422, "只能从当前存档已完成的故事回合生成记忆")
        character_ids = list(
            dict.fromkeys(
                item["id"] for item in turn["asset_snapshots"] if item["kind"] == "character"
            )
        )
        if not character_ids:
            raise HTTPException(422, "这个故事回合没有引用人物资产")
        characters = [require_character_asset(asset_id) for asset_id in character_ids]
        draft = db.get_draft(turn["draft_id"])
        try:
            extraction = await app.state.ai.extract_memories(draft["content"], characters)
        except AIServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        memories = db.save_character_memories(
            campaign_id,
            turn["id"],
            None,
            [item.model_dump(mode="json") for item in extraction.memories],
            app.state.settings.provider.parameter_model,
        )
        return {"turn_id": turn["id"], "memories": memories}

    @app.post("/api/campaigns/{campaign_id}/stories")
    async def generate_story(campaign_id: str, payload: StoryRequest) -> dict[str, Any]:
        campaign = db.get_campaign(campaign_id)
        try:
            content, provider, model = await app.state.ai.write_story(
                campaign,
                db.list_events(campaign_id),
                db.list_discord_messages(campaign_id),
                payload.instruction,
            )
        except AIServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        return db.save_draft(campaign_id, payload.instruction, content, provider, model)

    @app.post("/api/drafts/{draft_id}/publish")
    async def publish_story(draft_id: str, payload: PublishRequest) -> dict[str, Any]:
        draft = db.get_draft(draft_id)
        rolls = db.unpublished_rolls(draft["campaign_id"])
        evidence = format_roll_evidence(rolls)
        outbox_content = f"{evidence}\n\n---\n\n{payload.content}" if evidence else payload.content
        published = db.publish_draft(draft_id, payload.content, outbox_content)
        db.mark_story_preview_published(draft_id)
        await app.state.discord.flush_outbox_once()
        return published

    @app.get("/api/campaigns/{campaign_id}/messages")
    async def list_messages(campaign_id: str) -> list[dict[str, Any]]:
        return db.list_chat_messages(campaign_id)

    @app.post("/api/campaigns/{campaign_id}/messages", status_code=201)
    async def send_message(campaign_id: str, payload: ChatMessageRequest) -> dict[str, Any]:
        campaign = db.get_campaign(campaign_id)
        if campaign["discord_thread_id"]:
            message = db.create_chat_message(
                campaign_id,
                "local",
                "director-local",
                "导演（本地）",
                payload.content,
                "outbound",
                "pending",
            )
            await app.state.discord.flush_outbox_once()
            return {"mode": "discord", "message": db.get_chat_message(message["id"])}
        user_message = db.create_chat_message(
            campaign_id,
            "local",
            "director-local",
            "导演",
            payload.content,
            "local",
            "received",
        )
        try:
            reply, provider, model = await app.state.ai.local_chat(
                campaign, db.list_chat_messages(campaign_id), payload.content
            )
        except AIServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        bot_message = db.create_chat_message(
            campaign_id,
            "bot",
            "anko-director",
            "Anko Bot",
            reply,
            "local",
            "received",
            metadata={"provider": provider, "model": model, "canonical": False},
        )
        return {"mode": "local", "message": user_message, "reply": bot_message}

    @app.post("/api/campaigns/{campaign_id}/discord-bind")
    async def bind_discord(campaign_id: str, payload: DiscordBindRequest) -> dict[str, Any]:
        db.get_campaign(campaign_id)
        try:
            thread_id = await app.state.discord.create_forum_post(
                campaign_id, payload.forum_channel_id, payload.post_name
            )
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"thread_id": str(thread_id)}

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return public_settings(app.state.settings, store)

    @app.websocket("/ws/campaigns/{campaign_id}")
    async def campaign_updates(websocket: WebSocket, campaign_id: str) -> None:
        try:
            db.get_campaign(campaign_id)
        except KeyError:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(
                    {
                        "events": db.list_events(campaign_id),
                        "discord_messages": db.list_discord_messages(campaign_id),
                        "characters": db.list_characters(campaign_id),
                        "assets": db.list_assets(campaign_id),
                        "status_cards": db.list_status_cards(campaign_id),
                        "relations": db.list_relations(campaign_id),
                        "outcomes": db.list_outcomes(campaign_id),
                        "timeline": db.get_timeline(campaign_id),
                        "chat_messages": db.list_chat_messages(campaign_id),
                    }
                )
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.put("/api/settings")
    async def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
        provider_data = payload.get("provider", {})
        try:
            new_settings = AppSettings(
                provider=ProviderSettings(
                    provider=str(provider_data.get("provider", "openai")).lower(),
                    parameter_model=str(provider_data.get("parameter_model", "")).strip(),
                    story_model=str(provider_data.get("story_model", "")).strip(),
                    base_url=provider_data.get("base_url") or None,
                ),
                host="127.0.0.1",
                port=int(payload.get("port", 8765)),
                open_browser=bool(payload.get("open_browser", True)),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if new_settings.provider.provider not in {"openai", "anthropic", "poe"}:
            raise HTTPException(422, "provider 必须是 openai、anthropic 或 poe")
        if not new_settings.provider.parameter_model:
            raise HTTPException(422, "传参模型不能为空，且应选择小模型")
        if not new_settings.provider.story_model:
            raise HTTPException(422, "叙事模型不能为空")
        store.save(new_settings)
        app.state.settings = new_settings
        app.state.ai = AIService(new_settings, store)
        return public_settings(new_settings, store)

    @app.put("/api/secrets/{name}")
    async def save_secret(name: str, payload: dict[str, str]) -> dict[str, Any]:
        allowed = {
            "openai_api_key",
            "anthropic_api_key",
            "poe_api_key",
            "discord_bot_token",
        }
        if name not in allowed:
            raise HTTPException(404, "未知凭据")
        persisted = store.set_secret(name, payload.get("value", "").strip())
        return {
            "saved": True,
            "persistent": persisted,
            "restart_required": name == "discord_bot_token",
        }

    return app


def format_roll_evidence(rolls: list[dict[str, Any]]) -> str:
    if not rolls:
        return ""
    lines = ["🎲 **本回合投骰证据**"]
    for roll in rolls:
        lines.append(f"\n事件 #{roll['sequence']} · 种子 `{roll['result']['seed_hex']}`")
        for item in roll["result"]["results"]:
            actor = f"{item['character_name']} · " if item.get("character_name") else ""
            if item["secret"]:
                lines.append(f"- {actor}{item['label']}：暗骰（结果由导演保管）")
            elif item["kind"] == "dice":
                lines.append(
                    f"- {actor}{item['label']} `{item['notation']}`："
                    f"{item['raw']} → **{item['total']}**"
                )
            else:
                selected = "、".join(draw["selected"]["label"] for draw in item["draws"])
                raw = ", ".join(str(draw["raw"]) for draw in item["draws"])
                lines.append(f"- {actor}{item['label']} `{item['die']}`：[{raw}] → **{selected}**")
    return "\n".join(lines)


def format_proposal_preview(proposal: RollProposal) -> dict[str, Any]:
    items = []
    for operation in proposal.operations:
        label = (
            f"{operation.character_name} · {operation.label}"
            if operation.character_name
            else operation.label
        )
        if operation.kind == "dice":
            dice = operation.dice
            notation = f"{dice.count}d{dice.sides}"
            if dice.keep:
                notation += f"k{'h' if dice.keep_mode == 'highest' else 'l'}{dice.keep}"
            if dice.modifier:
                notation += f"{dice.modifier:+d}"
            target = ""
            if operation.target is not None:
                symbol = "≤" if operation.comparison == "lte" else "≥"
                target = f" · 成功条件 {symbol} {operation.target}"
            items.append({"label": label, "description": notation + target})
        else:
            mode = "放回" if operation.replace else "不放回"
            items.append(
                {
                    "label": label,
                    "description": (
                        f"从 {len(operation.options)} 项中{mode}抽取 {operation.draw_count} 项"
                    ),
                }
            )
    return {
        "title": proposal.title,
        "explanation": proposal.explanation,
        "character_name": proposal.character_name,
        "target_asset_id": proposal.target_asset_id,
        "create_character": proposal.create_character,
        "writeback_to_character": proposal.writeback_to_character,
        "repeat_count": proposal.repeat_count,
        "sequence_mode": proposal.sequence_mode,
        "items": items,
        "warnings": proposal.warnings,
    }


def format_roll_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    summary = []
    for item in result["results"]:
        if item["secret"]:
            value = "暗骰"
        elif item["kind"] == "dice":
            value = str(item["total"])
        else:
            value = "、".join(draw["selected"]["label"] for draw in item["draws"])
        label = item["label"]
        if item.get("repeat_count", 1) > 1:
            label = f"第 {item['iteration']}/{item['repeat_count']} 次 · {label}"
        if item.get("success") is not None:
            value += " · 成功" if item["success"] else " · 失败"
        summary.append({"label": label, "value": value})
    return summary


def format_roll_announcement(proposal: RollProposal, result: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in result["results"]:
        actor = item.get("character_name") or proposal.character_name or "命运"
        prefix = ""
        if item.get("repeat_count", 1) > 1:
            prefix = f"第 {item['iteration']}/{item['repeat_count']} 次，"
        if item["secret"]:
            lines.append(f"🎲 {prefix}{actor}进行了{item['label']}暗骰！")
        elif item["kind"] == "dice":
            verdict = ""
            if item.get("success") is not None:
                verdict = "——成功！" if item["success"] else "——失败！"
            lines.append(f"🎲 {prefix}{actor}的{item['label']}掷出了 {item['total']} 点！{verdict}")
        else:
            selected = "、".join(draw["selected"]["label"] for draw in item["draws"])
            lines.append(f"🎲 {prefix}{actor}的{item['label']}抽中了「{selected}」！")
    return "\n".join(lines)
