"""How the write-argument resolver renders values bound in the block and local helpers."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
    describe_write_arguments,
    has_write_call,
)

_CREATE_THEN_ADD = (
    "playlist = await spotify_create_playlist_playlists_post(title='Random Songs', is_public=False)\n"
    "await spotify_add_song_to_playlist_post(playlist_id=playlist.get('playlist_id'), song_id=42)"
)


def _rows(code: str, call: str) -> list[str]:
    return [row for row in describe_write_arguments(code).splitlines() if row.startswith(f"{call}(")]


# gpt-oss smoke on #782: 6 of 10 revises read an id from the block's own create
# call as ungrounded or as a second create, because the id rendered as
# (await spotify_create_playlist_playlists_post(...)).get('playlist_id').


@pytest.mark.unit
def test_an_id_from_a_create_in_the_block_is_marked_not_inlined():
    (playlist_row,) = [
        r for r in _rows(_CREATE_THEN_ADD, "spotify_add_song_to_playlist_post") if "playlist_id=" in r
    ]
    assert "spotify_create_playlist_playlists_post(" not in playlist_row
    assert "-> playlist.get('playlist_id')" in playlist_row
    assert (
        "[playlist: from the result of spotify_create_playlist_playlists_post earlier in this block]"
        in playlist_row
    )


@pytest.mark.unit
def test_the_create_call_keeps_its_own_rows():
    rows = _rows(_CREATE_THEN_ADD, "spotify_create_playlist_playlists_post")
    assert "spotify_create_playlist_playlists_post(title=) -> 'Random Songs'" in rows
    assert "spotify_create_playlist_playlists_post(is_public=) -> False" in rows


@pytest.mark.unit
@pytest.mark.parametrize(
    "binding",
    [
        "pid = (await spotify_create_playlist_playlists_post(title='X'))['playlist_id']",
        "pid = (await spotify_create_playlist_playlists_post(title='X')).get('playlist_id')",
    ],
)
def test_a_field_of_a_call_result_is_marked_too(binding):
    (row,) = [r for r in _rows(f"{binding}\nawait add_song_post(playlist_id=pid)", "add_song_post")]
    assert row.startswith("add_song_post(playlist_id=) -> pid  [pid: from the result of")


@pytest.mark.unit
def test_a_value_computed_from_a_call_result_stays_inlined():
    """The divisor is what rule 3 checks; a marker would hide it."""
    code = "share = (await venmo_total_get())['sum'] / 3\nawait venmo_pay_post(amount=share)"
    (row,) = _rows(code, "venmo_pay_post")
    assert row == "venmo_pay_post(amount=) -> (await venmo_total_get())['sum'] / 3"


@pytest.mark.unit
def test_a_called_name_is_not_listed_as_a_variable_from_earlier_blocks():
    out = describe_write_arguments(
        "await add_to_cart_post(product_id=sorted(await search_get(q='notebook'), key=lambda h: h['price'])[0]['id'])"
    )
    assert "From earlier blocks" not in out


@pytest.mark.unit
def test_a_real_variable_from_an_earlier_block_is_still_listed():
    out = describe_write_arguments("await add_to_cart_post(product_id=best_match['id'])")
    assert "From earlier blocks (check these against Variables): best_match" in out


# A helper that only computes was listed as a write ("valid(arg0=) -> c") and
# judged as one; a helper that wraps a write must stay visible.


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        "cards = await show_cards_get()\ndef valid(c):\n    return c['year'] >= 2026\nprint([c for c in cards if valid(c)])",
        "cards = await show_cards_get()\nvalid = lambda c: c['year'] >= 2026\nprint([c for c in cards if valid(c)])",
        "def fact(n):\n    return n * fact(n - 1) if n else 1\nprint(fact(5))",
    ],
    ids=["def", "lambda", "recursive"],
)
def test_a_helper_that_only_computes_is_not_a_write(code):
    assert not has_write_call(code)
    assert describe_write_arguments(code) == "(no write calls)"


@pytest.mark.unit
def test_a_helper_that_wraps_a_write_is_shown_with_the_calls_it_makes():
    code = (
        "async def place(payload):\n"
        "    return await amazon_place_order_orders_post(**payload)\n"
        "await place({'payment_card_id': 999, 'address_id': 5})"
    )
    assert has_write_call(code)
    out = describe_write_arguments(code)
    assert (
        "place(arg0=) -> {'payment_card_id': 999, 'address_id': 5}"
        "  [helper defined in this block; it calls amazon_place_order_orders_post]"
    ) in out
    assert "amazon_place_order_orders_post(**kwargs=) -> payload  [inside helper place]" in out


@pytest.mark.unit
def test_a_write_two_helpers_deep_still_counts():
    code = (
        "def outer(p):\n    return inner(p)\n"
        "def inner(p):\n    return venmo_pay_post(**p)\n"
        "outer({'amount': 46.67})"
    )
    assert has_write_call(code)
    assert "[helper defined in this block; it calls venmo_pay_post]" in describe_write_arguments(code)


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        "def check(c):\n    return True\ndef check(c):\n    return c.save()\ncheck(x)",
        "def check(c):\n    return True\ncheck = other_tool\ncheck(x)",
        "@retry\ndef check(c):\n    return True\ncheck(x)",
        "def run(check):\n    return check(1)\ndef check(c):\n    return True\ncheck(x)",
    ],
    ids=["redefined", "rebound", "decorated", "shadowed-by-parameter"],
)
def test_a_helper_name_that_is_not_one_plain_definition_stays_a_write(code):
    assert has_write_call(code)
    assert "check(arg0=) -> x" in describe_write_arguments(code)
