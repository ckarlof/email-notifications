"""Format BoardTogether notifications and waiting-games summaries for email and Lovelace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .api import GamesSnapshot, WaitingGame
from .const import (
    ACTION_GAME_STARTED,
    ACTION_IS_YOUR_TURN,
    ACTION_PLAYER_DECLINED,
    ACTION_PLAYER_JOINED,
    ACTION_PLAYER_VOTE_REQUESTED,
    ACTION_WAS_INVITED,
    DEFAULT_BASE_URL,
)


@dataclass(frozen=True)
class FormattedNotification:
    """Formatted email and event payload for a BoardTogether notification."""

    notification_id: str
    action: str
    title: str
    summary: str
    message: str
    game_id: str
    game_name: str
    game_type_id: str
    game_title: str
    url: str
    turn_dedupe_key: str

    def to_event_dict(self) -> dict[str, Any]:
        """Serialize for the Home Assistant event bus and sensor state attributes."""
        return {
            "notification_id": self.notification_id,
            "action": self.action,
            "title": self.title,
            "summary": self.summary,
            "message": self.message,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "game_type_id": self.game_type_id,
            "game_title": self.game_title,
            "url": self.url,
            "turn_dedupe_key": self.turn_dedupe_key,
        }


def build_waiting_games_markdown_table(
    waiting_games: list[WaitingGame],
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    """Render a Markdown table of all games waiting on the user for a Lovelace Markdown card."""
    if not waiting_games:
        clean_base = base_url.rstrip("/")
        return f"No games are currently waiting on you. [Open Dashboard]({clean_base}/dashboard)"

    lines = [
        "| Game | Title | Action | Waiting | Last Move |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for wg in waiting_games:
        last_by = wg.last_action_by or "-"
        safe_name = wg.name.replace("|", "\\|")
        safe_title = wg.title.replace("|", "\\|")
        lines.append(
            f"| [{safe_name}]({wg.url}) | {safe_title} | {wg.waiting_reason} | **{wg.waiting_duration}** | {last_by} |"
        )
    return "\n".join(lines)


def build_waiting_games_text_section(
    waiting_games: list[WaitingGame],
) -> str:
    """Build a plain-text summary section of all games currently waiting on the user."""
    if not waiting_games:
        return "All caught up! No games are currently waiting on you."

    lines = [
        f"Games currently waiting on you ({len(waiting_games)}):",
    ]
    for idx, wg in enumerate(waiting_games, start=1):
        last_move_part = f" (last move by {wg.last_action_by})" if wg.last_action_by else ""
        opponents_part = f" vs. {', '.join(wg.opponents)}" if wg.opponents else ""
        lines.append(
            f"  {idx}. {wg.name} [{wg.title}]{opponents_part} — {wg.waiting_reason}, waiting {wg.waiting_duration}{last_move_part}\n"
            f"     {wg.url}"
        )
    return "\n".join(lines)


def format_waiting_games_digest_email(
    waiting_games: list[WaitingGame],
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[str, str]:
    """Build (title, message) for an on-demand digest email of all waiting games."""
    clean_base = base_url.rstrip("/")
    count = len(waiting_games)
    if count == 0:
        title = "[BoardTogether] 0 games waiting on you"
        message = (
            "You're all caught up on BoardTogether!\n\n"
            f"Dashboard: {clean_base}/dashboard"
        )
        return title, message

    longest = waiting_games[0]
    plural = "game" if count == 1 else "games"
    title = (
        f"[BoardTogether] {count} {plural} waiting on you "
        f"(oldest: {longest.name}, {longest.waiting_duration})"
    )
    message = (
        f"{build_waiting_games_text_section(waiting_games)}\n\n"
        f"Dashboard: {clean_base}/dashboard"
    )
    return title, message


def format_user_notification(
    payload: dict[str, Any],
    base_url: str = DEFAULT_BASE_URL,
    snapshot: GamesSnapshot | None = None,
    include_waiting_digest: bool = True,
) -> FormattedNotification | None:
    """Format a BoardTogether UserNotification JSON envelope into email title and message."""
    if not isinstance(payload, dict) or payload.get("type") != "user":
        return None

    action = str(payload.get("action") or "")
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    game = data.get("game")
    if not isinstance(game, dict):
        return None

    notification_id = str(payload.get("id") or "")
    game_id = str(game.get("id") or "")
    game_name = str(game.get("name") or "Untitled Game")
    game_type_id = str(game.get("typeId") or "")

    clean_base = base_url.rstrip("/")
    game_url = f"{clean_base}/game/{game_id}" if game_id else f"{clean_base}/dashboard"
    dashboard_url = f"{clean_base}/dashboard"

    catalog_titles = snapshot.catalog_titles if snapshot else {}
    game_title = (
        catalog_titles.get(game_type_id)
        or game_type_id.replace("-", " ").title()
        or "Board Game"
    )

    waiting_entry: WaitingGame | None = None
    raw_game: dict[str, Any] | None = None
    if snapshot and game_id:
        waiting_entry = next(
            (wg for wg in snapshot.waiting_games if wg.game_id == game_id),
            None,
        )
        raw_game = snapshot.raw_games_by_id.get(game_id)

    title: str
    summary: str
    target_url: str
    action_label: str

    if action == ACTION_IS_YOUR_TURN:
        title = f"[BoardTogether] It's your turn in {game_name} ({game_title})"
        summary = f"It's your turn in game {game_name}"
        target_url = game_url
        action_label = "Take your turn"
    elif action == ACTION_GAME_STARTED:
        title = f"[BoardTogether] Game has started: {game_name} ({game_title})"
        summary = f"Your game {game_name} has begun!"
        target_url = game_url
        action_label = "Open game"
    elif action == ACTION_WAS_INVITED:
        owner = data.get("owner") or {}
        owner_name = str(owner.get("username") or "Someone")
        title = f"[BoardTogether] {owner_name} invited you to {game_name} ({game_title})"
        summary = f"{owner_name} invited you to join their game {game_name}"
        target_url = dashboard_url
        action_label = "View invitation"
    elif action == ACTION_PLAYER_JOINED:
        player = data.get("player") or {}
        player_name = str(player.get("name") or "A player")
        title = f"[BoardTogether] {player_name} joined {game_name}"
        summary = f"{player_name} joined your game {game_name}"
        target_url = dashboard_url
        action_label = "Open dashboard"
    elif action == ACTION_PLAYER_DECLINED:
        player = data.get("player") or {}
        player_name = str(player.get("name") or "A player")
        title = f"[BoardTogether] {player_name} declined to join {game_name}"
        summary = f"{player_name} has declined to join your game {game_name}"
        target_url = dashboard_url
        action_label = "Open dashboard"
    elif action == ACTION_PLAYER_VOTE_REQUESTED:
        initiator = data.get("initiator") or {}
        initiator_name = str(initiator.get("name") or "A player")
        kind = str(data.get("kind") or "")
        if kind == "call":
            heading = f"{initiator_name} asks to end the game"
        else:
            heading = f"{initiator_name} asks to go back"
        title = f"[BoardTogether] Vote requested in {game_name}: {heading}"
        summary = f"{heading} — Vote in game {game_name}"
        target_url = game_url
        action_label = "Vote in game"
    else:
        return None

    # Build turn deduplication key from game_id + lastActionAt (or notification_id fallback)
    last_action_at = ""
    if raw_game and raw_game.get("lastActionAt"):
        last_action_at = str(raw_game["lastActionAt"])
    elif waiting_entry and waiting_entry.waiting_since:
        last_action_at = waiting_entry.waiting_since
    turn_dedupe_key = (
        f"{action}:{game_id}:{last_action_at}"
        if last_action_at
        else f"{action}:{game_id}:{notification_id}"
    )

    body_lines = [
        summary,
        "",
        f"• Game: {game_name}",
        f"• Title: {game_title}",
    ]
    if waiting_entry:
        body_lines.append(f"• Waiting on you: {waiting_entry.waiting_duration}")
        if waiting_entry.last_action_by:
            body_lines.append(f"• Last move by: {waiting_entry.last_action_by}")
        if waiting_entry.opponents:
            body_lines.append(f"• Opponents: {', '.join(waiting_entry.opponents)}")

    body_lines.extend(
        [
            "",
            f"{action_label}: {target_url}",
        ]
    )

    if include_waiting_digest and snapshot and snapshot.waiting_games:
        body_lines.extend(
            [
                "",
                "─" * 44,
                build_waiting_games_text_section(snapshot.waiting_games),
            ]
        )

    return FormattedNotification(
        notification_id=notification_id,
        action=action,
        title=title,
        summary=summary,
        message="\n".join(body_lines),
        game_id=game_id,
        game_name=game_name,
        game_type_id=game_type_id,
        game_title=game_title,
        url=target_url,
        turn_dedupe_key=turn_dedupe_key,
    )
