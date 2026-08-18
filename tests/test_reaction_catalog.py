import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.plugins._reaction_catalog import (  # noqa: E402
    ENABLED_RANDOM_REACTIONS,
    TYPE_1_REACTIONS,
    TYPE_2_REACTIONS,
)


def test_verified_reactions_are_split_by_protocol_type() -> None:
    assert len(TYPE_1_REACTIONS) == 357
    assert len(TYPE_2_REACTIONS) == 171
    assert {reaction.reaction_type for reaction in TYPE_1_REACTIONS} == {1}
    assert {reaction.reaction_type for reaction in TYPE_2_REACTIONS} == {2}

    type_1_ids = {reaction.reaction_id for reaction in TYPE_1_REACTIONS}
    type_2_ids = {reaction.reaction_id for reaction in TYPE_2_REACTIONS}
    assert len(type_1_ids) == len(TYPE_1_REACTIONS)
    assert len(type_2_ids) == len(TYPE_2_REACTIONS)
    assert type_1_ids.isdisjoint(type_2_ids)


def test_type_1_distinguishes_message_faces_from_reaction_only_ids() -> None:
    message_faces = {
        reaction.reaction_id
        for reaction in TYPE_1_REACTIONS
        if reaction.message_face_id is not None
    }
    reaction_only = {
        reaction.reaction_id
        for reaction in TYPE_1_REACTIONS
        if reaction.message_face_id is None
    }

    assert len(message_faces) == 282
    assert len(reaction_only) == 75
    assert "76" in message_faces
    assert "193" in reaction_only


def test_unicode_reactions_keep_a_display_character() -> None:
    blessing = next(
        reaction
        for reaction in TYPE_2_REACTIONS
        if reaction.reaction_id == "12951"
    )

    assert blessing.message_face_id is None
    assert blessing.display_text == "㊗"


def test_only_type_1_is_enabled_for_random_reactions() -> None:
    assert ENABLED_RANDOM_REACTIONS is TYPE_1_REACTIONS
