"""Unit tests for analyzing waiting games, wait durations, and Lovelace Markdown tables."""

from __future__ import annotations

from datetime import datetime, timezone

from custom_components.boardtogether.api import analyze_games, format_duration
from custom_components.boardtogether.formatter import build_waiting_games_markdown_table


def test_format_duration() -> None:
    """Test human-readable duration formatting."""
    assert format_duration(0) == "<1m"
    assert format_duration(45) == "<1m"
    assert format_duration(15 * 60) == "15m"
    assert format_duration(3 * 3600 + 20 * 60) == "3h 20m"
    assert format_duration(2 * 86400 + 4 * 3600 + 10 * 60) == "2d 4h"
    assert format_duration(1 * 86400) == "1d"


def test_analyze_games_sorting_and_reasons() -> None:
    """Test identifying games waiting on the user and sorting longest-waiting first."""
    now = datetime(2026, 10, 26, 15, 0, 0, tzinfo=timezone.utc)
    user_id = "user-me"

    games = [
        # 1. Started game where it's my turn for 3 hours
        {
            "id": "g-turn-recent",
            "name": "Quick 1846",
            "typeId": "1846",
            "status": "started",
            "hotseat": False,
            "deleted": False,
            "ownerId": "user-other",
            "players": [
                {"id": "p1", "userId": "user-me", "name": "Chris", "status": "joined"},
                {"id": "p2", "userId": "user-other", "name": "Alice", "status": "joined"},
            ],
            "activePlayerIds": ["p1"],
            "lastActionPlayerId": "p2",
            "lastActionAt": "2026-10-26T12:00:00Z",
        },
        # 2. Started game where it's my turn for 1 day 2 hours (should sort first!)
        {
            "id": "g-turn-oldest",
            "name": "Weekend Indonesia",
            "typeId": "indonesia",
            "status": "started",
            "hotseat": False,
            "deleted": False,
            "ownerId": "user-me",
            "players": [
                {"id": "p10", "userId": "user-me", "name": "Chris", "status": "joined"},
                {"id": "p20", "userId": "user-bob", "name": "Bob", "status": "joined"},
            ],
            "activePlayerIds": ["p10"],
            "lastActionPlayerId": "p20",
            "lastActionAt": "2026-10-25T13:00:00Z",
        },
        # 3. Started game where it's opponent's turn (should NOT be in waiting_games, but in active_games)
        {
            "id": "g-opponent-turn",
            "name": "Fresh Fish Table",
            "typeId": "fresh-fish",
            "status": "started",
            "hotseat": False,
            "deleted": False,
            "ownerId": "user-me",
            "players": [
                {"id": "p30", "userId": "user-me", "name": "Chris", "status": "joined"},
                {"id": "p40", "userId": "user-carol", "name": "Carol", "status": "joined"},
            ],
            "activePlayerIds": ["p40"],
            "lastActionPlayerId": "p30",
            "lastActionAt": "2026-10-26T14:30:00Z",
        },
        # 4. Game invitation waiting on me for 30 minutes
        {
            "id": "g-invite",
            "name": "New Reef Game",
            "typeId": "reef-encounter",
            "status": "waitingForPlayers",
            "hotseat": False,
            "deleted": False,
            "ownerId": "user-dave",
            "players": [
                {"id": "p50", "userId": "user-me", "name": "Chris", "status": "invited"},
                {"id": "p60", "userId": "user-dave", "name": "Dave", "status": "joined"},
            ],
            "createdAt": "2026-10-26T14:30:00Z",
        },
    ]

    catalog = {
        "indonesia": "Indonesia",
        "1846": "1846: The Race for the Midwest",
        "fresh-fish": "Fresh Fish",
        "reef-encounter": "Reef Encounter",
    }

    snapshot = analyze_games(
        games=games,
        user_id=user_id,
        username="Chris",
        catalog_titles=catalog,
        base_url="https://boardtogether.games",
        now=now,
    )

    assert len(snapshot.waiting_games) == 3
    assert [wg.game_id for wg in snapshot.waiting_games] == [
        "g-turn-oldest",
        "g-turn-recent",
        "g-invite",
    ]

    oldest = snapshot.waiting_games[0]
    assert oldest.name == "Weekend Indonesia"
    assert oldest.title == "Indonesia"
    assert oldest.waiting_reason == "Your Turn"
    assert oldest.waiting_duration == "1d 2h"
    assert oldest.last_action_by == "Bob"
    assert oldest.opponents == ["Bob"]
    assert oldest.url == "https://boardtogether.games/game/g-turn-oldest"

    assert len(snapshot.active_games) == 3
    md = build_waiting_games_markdown_table(snapshot.waiting_games)
    assert "[Weekend Indonesia](https://boardtogether.games/game/g-turn-oldest)" in md
    assert "**1d 2h**" in md
    assert "1846: The Race for the Midwest" in md
