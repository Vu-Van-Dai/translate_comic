"""Pinning forms of address across chapters.

Vietnamese has no neutral second person, so every line commits to a
relationship. Letting that drift between chapters is the most noticeable
failure a series can have, which is why a pinned pair is not overwritten.
"""

from __future__ import annotations

import json

import pytest

from comic.handoff.schemas import AddressObservation, TranslateResponse
from comic.memory.profile import Address, Character, SeriesMemory


@pytest.fixture
def memory(home):
    store = SeriesMemory("test-series")
    store.ensure_initialised()
    return store


def test_address_pairs_parse_from_a_response():
    response = TranslateResponse.model_validate(
        {
            "items": [],
            "address_pairs": [
                {
                    "speaker": "Ran",
                    "addressee": "Shinichi",
                    "self_term": "tớ",
                    "other_term": "cậu",
                }
            ],
        }
    )
    pair = response.address_pairs[0]
    assert (pair.self_term, pair.other_term) == ("tớ", "cậu")
    assert pair.formality == "neutral"


def test_a_pair_is_directional():
    """Ran -> Shinichi and Shinichi -> Ran are separate entries."""
    ran = Character(name="Ran")
    ran.addresses["Shinichi"] = Address(self_term="tớ", other_term="cậu")
    assert "Ran" not in ran.addresses


def test_pinned_addresses_survive_a_round_trip(memory):
    ran = Character(name="Ran")
    ran.addresses["Shinichi"] = Address(
        self_term="tớ", other_term="cậu", formality="casual"
    )
    memory.save_characters({"Ran": ran})

    loaded = memory.load_characters()["Ran"].addresses["Shinichi"]
    assert (loaded.self_term, loaded.other_term) == ("tớ", "cậu")
    assert loaded.formality == "casual"


def test_the_pronoun_table_renders_pinned_pairs(memory):
    from comic.context.builder import _pronoun_table

    ran = Character(name="Ran")
    ran.addresses["Shinichi"] = Address(self_term="tớ", other_term="cậu")
    table = _pronoun_table({"Ran": ran})
    assert "| Ran | Shinichi | tớ | cậu |" in table


def test_an_empty_table_tells_the_agent_which_field_to_use(memory):
    from comic.context.builder import _pronoun_table

    assert "address_pairs" in _pronoun_table({})


def test_diacritics_survive_the_json_file(memory):
    ran = Character(name="Ran")
    ran.addresses["Shinichi"] = Address(self_term="tớ", other_term="cậu")
    memory.save_characters({"Ran": ran})
    raw = memory.paths.characters_file.read_text(encoding="utf-8")
    assert "cậu" in raw
    assert json.loads(raw)["Ran"]["addresses"]["Shinichi"]["other_term"] == "cậu"


def _pair(speaker, addressee, self_term, other_term):
    return AddressObservation(
        speaker=speaker,
        addressee=addressee,
        self_term=self_term,
        other_term=other_term,
    )


def test_merge_adds_a_new_pair():
    from comic.translation.gate import merge_addresses

    characters: dict = {}
    added = merge_addresses(characters, [_pair("Ran", "Shinichi", "tớ", "cậu")])
    assert added == 1
    assert characters["Ran"].addresses["Shinichi"].other_term == "cậu"


def test_merge_creates_the_speaker_if_unknown():
    from comic.translation.gate import merge_addresses

    characters: dict = {}
    merge_addresses(characters, [_pair("Megure", "Shinichi", "tôi", "cậu")])
    assert characters["Megure"].name == "Megure"


def test_an_established_pair_is_not_overwritten():
    """A later chapter must not quietly change an existing relationship."""
    from comic.translation.gate import merge_addresses

    characters = {"Ran": Character(name="Ran")}
    characters["Ran"].addresses["Shinichi"] = Address(
        self_term="tớ", other_term="cậu"
    )
    added = merge_addresses(characters, [_pair("Ran", "Shinichi", "em", "anh")])
    assert added == 0
    assert characters["Ran"].addresses["Shinichi"].self_term == "tớ"


def test_both_directions_are_pinned_independently():
    from comic.translation.gate import merge_addresses

    characters: dict = {}
    added = merge_addresses(
        characters,
        [
            _pair("Ran", "Shinichi", "tớ", "cậu"),
            _pair("Shinichi", "Ran", "tớ", "cậu"),
        ],
    )
    assert added == 2
    assert set(characters) == {"Ran", "Shinichi"}


def test_merging_nothing_changes_nothing():
    from comic.translation.gate import merge_addresses

    characters: dict = {}
    assert merge_addresses(characters, []) == 0
    assert characters == {}
