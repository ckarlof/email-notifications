"""Unit tests for notification formatting and waiting-games email digests."""

from __future__ import annotations

from datetime import datetime, timezone

from custom_components.boardtogether.api import analyze_games
from custom_components.boardtogether.formatter import (
    format_user_notification,
    format_waiting_games_digest_email,
)


def _sample_snapshot():
    now = datetime(2026, 10, 26, 15, 0, 0, tzinfo=timezone.utc)
    games = [
        {
            "id": "game-1",
            "name": "Evening Indonesia",
            "typeId": "indonesia",
            "status": "started",
            "hotseat": False,
            "deleted": False,
            "ownerId": "user-2",
            "players": [
                {"id": "p1", "userId": "user-1", "name": "Chris", "status": "joined"},
                {"id": "p2", "userId": "user-2", "name": "Alice", "status": "joined"},
            ],
            "activePlayerIds": ["p1"],
            "lastActionPlayerId": "p2",
            "lastActionAt": "2026-10-26T10:30:00Z",
        },
        {
            "id": "game-2",
            "name": "Railroad Showdown",
            "typeId": "1846",
            "status": "started",
            "hotseat": False,
            "deleted": False,
            "ownerId": "user-1",
            "players": [
                {"id": "p3", "userId": "user-1", "name": "Chris", "status": "joined"},
                {"id": "p4", "userId": "user-3", "name": "Bob", "status": "joined"},
            ],
            "activePlayerIds": ["p3"],
            "lastActionPlayerId": "p4",
            "lastActionAt": "2026-10-25T15:00:00Z",
        },
    ]
    return analyze_games(
        games=games,
        user_id="user-1",
        username="Chris",
        catalog_titles={"indonesia": "Indonesia", "1846": "1846"},
        now=now,
    )


def test_format_is_your_turn_default_no_digest() -> None:
    """Test formatting an isYourTurn notification with default include_waiting_digest=False."""
    snapshot = _sample_snapshot()
    payload = {
        "id": "notif-99",
        "type": "user",
        "action": "isYourTurn",
        "data": {
            "user": {"id": "user-1"},
            "game": {
                "id": "game-1",
                "typeId": "indonesia",
                "name": "Evening Indonesia",
            },
        },
    }
    formatted = format_user_notification(
        payload=payload,
        base_url="https://boardtogether.games",
        snapshot=snapshot,
    )
    assert formatted is not None
    assert (
        formatted.title
        == "[BoardTogether] It's your turn in Evening Indonesia (Indonesia) — after Alice, Oct 26 10:30 UTC"
    )
    assert "Waiting on you: 4h 30m" in formatted.message
    assert "Last move by: Alice" in formatted.message
    assert "Take your turn: https://boardtogether.games/game/game-1" in formatted.message
    assert "Games currently waiting on you" not in formatted.message
    assert formatted.turn_dedupe_key == "isYourTurn:game-1:2026-10-26T10:30:00+00:00"
    assert formatted.legacy_turn_dedupe_key == "isYourTurn:game-1:2026-10-26T10:30:00Z"


def test_format_is_your_turn_with_waiting_digest() -> None:
    """Test formatting an isYourTurn notification including the waiting games digest."""
    snapshot = _sample_snapshot()
    payload = {
        "id": "notif-99",
        "type": "user",
        "action": "isYourTurn",
        "data": {
            "user": {"id": "user-1"},
            "game": {
                "id": "game-1",
                "typeId": "indonesia",
                "name": "Evening Indonesia",
            },
        },
    }
    formatted = format_user_notification(
        payload=payload,
        base_url="https://boardtogether.games",
        snapshot=snapshot,
        include_waiting_digest=True,
    )
    assert formatted is not None
    assert (
        formatted.title
        == "[BoardTogether] It's your turn in Evening Indonesia (Indonesia) — after Alice, Oct 26 10:30 UTC"
    )
    assert "Waiting on you: 4h 30m" in formatted.message
    assert "Last move by: Alice" in formatted.message
    assert "Take your turn: https://boardtogether.games/game/game-1" in formatted.message
    assert "Games currently waiting on you (2):" in formatted.message
    assert "1. Railroad Showdown [1846] vs. Bob — Your Turn, waiting 1d" in formatted.message
    assert formatted.turn_dedupe_key == "isYourTurn:game-1:2026-10-26T10:30:00+00:00"


def test_format_all_other_notification_actions() -> None:
    """Test formatting gameStarted, wasInvited, playerJoined, playerDeclined, and playerVoteRequested."""
    cases = [
        (
            {
                "id": "n1",
                "type": "user",
                "action": "gameStarted",
                "data": {
                    "user": {"id": "u1"},
                    "game": {"id": "g1", "typeId": "1846", "name": "Midwest Rails"},
                },
            },
            "[BoardTogether] Game has started: Midwest Rails (1846)",
            "https://boardtogether.games/game/g1",
        ),
        (
            {
                "id": "n2",
                "type": "user",
                "action": "wasInvited",
                "data": {
                    "user": {"id": "u1"},
                    "owner": {"id": "u2", "username": "Alice"},
                    "game": {"id": "g2", "typeId": "indonesia", "name": "Spice Islands"},
                },
            },
            "[BoardTogether] Alice invited you to Spice Islands (Indonesia)",
            "https://boardtogether.games/dashboard",
        ),
        (
            {
                "id": "n3",
                "type": "user",
                "action": "playerJoined",
                "data": {
                    "user": {"id": "u1"},
                    "player": {"id": "p2", "name": "Bob"},
                    "game": {"id": "g3", "typeId": "oath", "name": "Chronicle 1"},
                },
            },
            "[BoardTogether] Bob joined Chronicle 1",
            "https://boardtogether.games/dashboard",
        ),
        (
            {
                "id": "n4",
                "type": "user",
                "action": "playerDeclined",
                "data": {
                    "user": {"id": "u1"},
                    "player": {"id": "p2", "name": "Bob"},
                    "game": {"id": "g3", "typeId": "oath", "name": "Chronicle 1"},
                },
            },
            "[BoardTogether] Bob declined to join Chronicle 1",
            "https://boardtogether.games/dashboard",
        ),
        (
            {
                "id": "n5",
                "type": "user",
                "action": "playerVoteRequested",
                "data": {
                    "user": {"id": "u1"},
                    "initiator": {"id": "p2", "name": "Alice"},
                    "kind": "undo",
                    "game": {"id": "g4", "typeId": "fresh-fish", "name": "Market Day"},
                },
            },
            "[BoardTogether] Vote requested in Market Day: Alice asks to go back",
            "https://boardtogether.games/game/g4",
        ),
    ]

    for payload, expected_title, expected_url in cases:
        res = format_user_notification(payload)
        assert res is not None
        assert res.title == expected_title
        assert res.url == expected_url


def test_format_waiting_games_digest_email() -> None:
    """Test formatting the standalone waiting games digest email."""
    snapshot = _sample_snapshot()
    title, message = format_waiting_games_digest_email(snapshot.waiting_games)
    assert title == "[BoardTogether] 2 games waiting on you (oldest: Railroad Showdown, 1d)"
    assert "1. Railroad Showdown [1846]" in message
    assert "2. Evening Indonesia [Indonesia]" in message
