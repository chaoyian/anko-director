from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any

from .schemas import DiceOperation, DiceSpec, KeepMode, RollProposal, TableOperation

DICE_RE = re.compile(
    r"^\s*\(?(?P<count>\d*)d(?P<sides>\d+)"
    r"(?:(?P<keep_mode>kh|kl)(?P<keep>\d+))?"
    r"(?P<modifier>[+-]\d+)?\)?"
    r"(?:(?:\*|x)(?P<multiplier>\d+))?\s*$",
    re.IGNORECASE,
)


class DiceNotationError(ValueError):
    pass


def parse_dice_notation(notation: str) -> DiceSpec:
    match = DICE_RE.fullmatch(notation)
    if not match:
        raise DiceNotationError(f"无法识别骰式：{notation}")
    keep_token = match.group("keep_mode")
    return DiceSpec(
        count=int(match.group("count") or 1),
        sides=int(match.group("sides")),
        modifier=int(match.group("modifier") or 0),
        multiplier=int(match.group("multiplier") or 1),
        keep=int(match.group("keep")) if match.group("keep") else None,
        keep_mode=(
            KeepMode.HIGHEST
            if keep_token and keep_token.lower() == "kh"
            else KeepMode.LOWEST
            if keep_token
            else None
        ),
    )


@dataclass(slots=True)
class DeterministicRandom:
    """Cross-platform deterministic random stream using SHA-256 and rejection sampling."""

    seed: bytes
    counter: int = 0

    @classmethod
    def fresh(cls) -> DeterministicRandom:
        return cls(secrets.token_bytes(32))

    @classmethod
    def from_hex(cls, seed_hex: str, counter: int = 0) -> DeterministicRandom:
        try:
            seed = bytes.fromhex(seed_hex)
        except ValueError as exc:
            raise ValueError("种子必须是十六进制字符串") from exc
        if len(seed) < 16:
            raise ValueError("种子至少需要 128 位")
        return cls(seed=seed, counter=counter)

    @property
    def seed_hex(self) -> str:
        return self.seed.hex()

    def _block(self) -> bytes:
        payload = self.seed + self.counter.to_bytes(16, "big")
        self.counter += 1
        return hashlib.sha256(payload).digest()

    def randbelow(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper 必须为正数")
        limit = (1 << 256) - ((1 << 256) % upper)
        while True:
            value = int.from_bytes(self._block(), "big")
            if value < limit:
                return value % upper


def _roll_dice(operation: DiceOperation, rng: DeterministicRandom) -> dict[str, Any]:
    spec = operation.dice
    raw = [rng.randbelow(spec.sides) + 1 for _ in range(spec.count)]
    if spec.keep is None:
        kept_indices = list(range(spec.count))
    else:
        reverse = spec.keep_mode == KeepMode.HIGHEST
        ranked = sorted(range(spec.count), key=lambda index: (raw[index], -index), reverse=reverse)
        kept_indices = sorted(ranked[: spec.keep])
    kept = [raw[index] for index in kept_indices]
    subtotal = sum(kept) + spec.modifier
    total = subtotal * spec.multiplier
    success = None
    if operation.target is not None:
        success = (
            total <= operation.target
            if operation.comparison == "lte"
            else total >= operation.target
        )
    return {
        "kind": "dice",
        "id": operation.id,
        "label": operation.label,
        "notation": format_dice_notation(spec),
        "raw": raw,
        "kept_indices": kept_indices,
        "kept": kept,
        "modifier": spec.modifier,
        "multiplier": spec.multiplier,
        "subtotal": subtotal,
        "total": total,
        "target": operation.target,
        "comparison": operation.comparison,
        "success": success,
        "secret": operation.secret,
    }


def _draw_table(operation: TableOperation, rng: DeterministicRandom) -> dict[str, Any]:
    pool = [option.model_copy() for option in operation.options]
    draws: list[dict[str, Any]] = []
    for _ in range(operation.draw_count):
        total_weight = sum(option.weight for option in pool)
        raw = rng.randbelow(total_weight) + 1
        cursor = 0
        selected_index = 0
        intervals: list[dict[str, Any]] = []
        for index, option in enumerate(pool):
            start = cursor + 1
            cursor += option.weight
            intervals.append(
                {"id": option.id, "label": option.label, "start": start, "end": cursor}
            )
            if start <= raw <= cursor:
                selected_index = index
        selected = pool[selected_index]
        draws.append(
            {
                "raw": raw,
                "range": total_weight,
                "selected": selected.model_dump(),
                "intervals": intervals,
            }
        )
        if not operation.replace:
            pool.pop(selected_index)
    return {
        "kind": "table",
        "id": operation.id,
        "label": operation.label,
        "die": f"d{operation.die_sides or sum(o.weight for o in operation.options)}",
        "draw_count": operation.draw_count,
        "replace": operation.replace,
        "draws": draws,
        "secret": operation.secret,
    }


def execute_proposal(proposal: RollProposal, seed_hex: str | None = None) -> dict[str, Any]:
    rng = DeterministicRandom.from_hex(seed_hex) if seed_hex else DeterministicRandom.fresh()
    start_counter = rng.counter
    results: list[dict[str, Any]] = []
    for iteration in range(1, proposal.repeat_count + 1):
        for operation in proposal.operations:
            if isinstance(operation, DiceOperation):
                item = _roll_dice(operation, rng)
            elif isinstance(operation, TableOperation):
                item = _draw_table(operation, rng)
            else:  # pragma: no cover - Pydantic prevents this
                raise TypeError(f"未知投骰操作：{type(operation)!r}")
            item["character_name"] = operation.character_name
            item["target_asset_id"] = operation.target_asset_id
            item["iteration"] = iteration
            item["repeat_count"] = proposal.repeat_count
            results.append(item)
    return {
        "algorithm": "sha256-counter-v1",
        "seed_hex": rng.seed_hex,
        "start_counter": start_counter,
        "end_counter": rng.counter,
        "proposal": proposal.model_dump(mode="json"),
        "results": results,
    }


def format_dice_notation(spec: DiceSpec) -> str:
    text = f"{spec.count}d{spec.sides}"
    if spec.keep is not None:
        text += "kh" if spec.keep_mode == KeepMode.HIGHEST else "kl"
        text += str(spec.keep)
    if spec.modifier > 0:
        text += f"+{spec.modifier}"
    elif spec.modifier < 0:
        text += str(spec.modifier)
    if spec.multiplier != 1:
        if spec.modifier:
            text = f"({text})"
        text += f"*{spec.multiplier}"
    return text


def verify_roll(result: dict[str, Any]) -> bool:
    proposal = RollProposal.model_validate(result["proposal"])
    reproduced = execute_proposal(proposal, seed_hex=result["seed_hex"])
    actual = reproduced["results"]
    expected = result["results"]
    if len(actual) != len(expected):
        return False
    for actual_item, expected_item in zip(actual, expected, strict=True):
        comparable = dict(actual_item)
        # 0.5.x 的已保存骰果还没有这两个审计字段；乘数默认为 1 时仍可复核。
        for key in ("multiplier", "subtotal"):
            if key not in expected_item:
                comparable.pop(key, None)
        if comparable != expected_item:
            return False
    return True
