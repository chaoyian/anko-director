from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from anko_director.dice import execute_proposal, parse_dice_notation, verify_roll
from anko_director.schemas import (
    DiceOperation,
    DiceSpec,
    KeepMode,
    RollProposal,
    TableOperation,
    WeightedOption,
)


def proposal_for(spec: DiceSpec) -> RollProposal:
    return RollProposal(
        title="测试投骰", operations=[DiceOperation(id="test", label="测试", dice=spec)]
    )


@pytest.mark.parametrize(
    ("notation", "expected"),
    [
        ("d100", DiceSpec(count=1, sides=100)),
        ("3d6+2", DiceSpec(count=3, sides=6, modifier=2)),
        (
            "2d20kh1",
            DiceSpec(count=2, sides=20, keep=1, keep_mode=KeepMode.HIGHEST),
        ),
        ("4d6kl2-1", DiceSpec(count=4, sides=6, modifier=-1, keep=2, keep_mode="lowest")),
    ],
)
def test_parse_dice_notation(notation: str, expected: DiceSpec) -> None:
    assert parse_dice_notation(notation) == expected


def test_roll_is_reproducible_and_verifiable() -> None:
    proposal = proposal_for(parse_dice_notation("4d6kh3+2"))
    first = execute_proposal(proposal)
    second = execute_proposal(proposal, first["seed_hex"])
    assert first["results"] == second["results"]
    assert verify_roll(first)


def test_pre_multiplier_roll_records_remain_verifiable() -> None:
    result = execute_proposal(proposal_for(parse_dice_notation("1d100")), "66" * 32)
    for item in result["results"]:
        item.pop("multiplier")
        item.pop("subtotal")
    assert verify_roll(result)


@given(
    sides=st.integers(min_value=2, max_value=1000),
    count=st.integers(min_value=1, max_value=30),
)
def test_dice_never_leave_bounds(sides: int, count: int) -> None:
    result = execute_proposal(proposal_for(DiceSpec(count=count, sides=sides)))
    raw = result["results"][0]["raw"]
    assert len(raw) == count
    assert all(1 <= value <= sides for value in raw)


def test_weighted_draw_without_replacement() -> None:
    operation = TableOperation(
        id="traits",
        label="特殊能力",
        options=[
            WeightedOption(id="a", label="天才操盘手", weight=3),
            WeightedOption(id="b", label="高情商沟通家", weight=2),
            WeightedOption(id="c", label="运气爆棚", weight=1),
        ],
        draw_count=3,
        replace=False,
        die_sides=6,
    )
    result = execute_proposal(RollProposal(title="抽能力", operations=[operation]))
    selected = [draw["selected"]["id"] for draw in result["results"][0]["draws"]]
    assert len(set(selected)) == 3


def test_explicit_die_requires_exact_weight_sum() -> None:
    with pytest.raises(ValidationError, match="权重之和"):
        TableOperation(
            id="branch",
            label="分支",
            options=[
                WeightedOption(id="a", label="A", weight=2),
                WeightedOption(id="b", label="B", weight=1),
            ],
            die_sides=4,
        )
