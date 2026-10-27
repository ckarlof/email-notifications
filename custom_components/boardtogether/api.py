"""BoardTogether API client and game-state analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Any

import aiohttp

from .const import DEFAULT_BASE_URL, DEFAULT_VAPID_PUBLIC_KEY

_LOGGER = logging.getLogger(__name__)

VAPID_KEY_REGEX = re.compile(r'"(B[A-Za-z0-9_\-]{86})"')


class BoardTogetherError(Exception):
    """Base exception for BoardTogether API errors."""


class BoardTogetherAuthError(BoardTogetherError):
    """Authentication failed."""


class BoardTogetherConnectionError(BoardTogetherError):
    """Connection or network error."""


@dataclass(frozen=True)
class WaitingGame:
    """Represents a game currently waiting on the user."""

    game_id: str
    name: str
    type_id: str
    title: str
    waiting_reason: str
    waiting_since: str | None
    waiting_seconds: int
    waiting_duration: str
    last_action_by: str | None
    opponents: list[str]
    url: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a Home Assistant state attribute dict."""
        return {
            "game_id": self.game_id,
            "name": self.name,
            "type_id": self.type_id,
            "title": self.title,
            "waiting_reason": self.waiting_reason,
            "waiting_since": self.waiting_since,
            "waiting_seconds": self.waiting_seconds,
            "waiting_duration": self.waiting_duration,
            "last_action_by": self.last_action_by,
            "opponents": self.opponents,
            "url": self.url,
        }


@dataclass(frozen=True)
class ActiveGameSummary:
    """Represents any active game the user is participating in."""

    game_id: str
    name: str
    type_id: str
    title: str
    status: str
    is_my_turn: bool
    active_players: list[str]
    last_action_at: str | None
    last_action_ago: str | None
    last_action_by: str | None
    url: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a Home Assistant state attribute dict."""
        return {
            "game_id": self.game_id,
            "name": self.name,
            "type_id": self.type_id,
            "title": self.title,
            "status": self.status,
            "is_my_turn": self.is_my_turn,
            "active_players": self.active_players,
            "last_action_at": self.last_action_at,
            "last_action_ago": self.last_action_ago,
            "last_action_by": self.last_action_by,
            "url": self.url,
        }


@dataclass(frozen=True)
class GamesSnapshot:
    """Analyzed snapshot of the user's games."""

    user_id: str
    username: str
    waiting_games: list[WaitingGame]
    active_games: list[ActiveGameSummary]
    raw_games_by_id: dict[str, dict[str, Any]]
    catalog_titles: dict[str, str]
    fetched_at: datetime


def format_duration(seconds: int) -> str:
    """Format elapsed seconds into a concise human-readable duration (e.g., '2d 4h', '3h 15m', '14m')."""
    if seconds < 60:
        return "<1m"
    total_minutes = seconds // 60
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    if days > 0:
        if hours > 0:
            return f"{days}d {hours}h"
        return f"{days}d"
    if hours > 0:
        if minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    return f"{minutes}m"


def parse_iso_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def analyze_games(
    games: list[dict[str, Any]],
    user_id: str,
    username: str = "",
    catalog_titles: dict[str, str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    now: datetime | None = None,
) -> GamesSnapshot:
    """Analyze raw Game objects from /api/v1/games/mine?scope=current."""
    current_time = now or datetime.now(timezone.utc)
    titles = catalog_titles or {}
    clean_base = base_url.rstrip("/")

    waiting_games: list[WaitingGame] = []
    active_games: list[ActiveGameSummary] = []
    raw_games_by_id: dict[str, dict[str, Any]] = {}

    for game in games:
        game_id = str(game.get("id", ""))
        if not game_id or game.get("deleted"):
            continue
        if game.get("category") == "exploration" or game.get("hotseat"):
            continue

        raw_games_by_id[game_id] = game
        name = str(game.get("name", "Untitled Game"))
        type_id = str(game.get("typeId", ""))
        title = titles.get(type_id) or type_id.replace("-", " ").title() or "Game"
        status = str(game.get("status", ""))
        players = game.get("players") or []
        active_player_ids = set(game.get("activePlayerIds") or [])

        my_player = next(
            (p for p in players if p.get("userId") == user_id),
            None,
        )
        players_by_id = {str(p.get("id")): p for p in players if p.get("id")}

        last_action_player_id = game.get("lastActionPlayerId")
        last_action_by = None
        if last_action_player_id and str(last_action_player_id) in players_by_id:
            last_action_by = players_by_id[str(last_action_player_id)].get("name")

        opponents = [
            str(p.get("name"))
            for p in players
            if p.get("userId") != user_id and p.get("name")
        ]

        # Determine timestamp since the game has been in its current action state
        ts_str = (
            game.get("lastActionAt")
            or game.get("startedAt")
            or game.get("updatedAt")
            or game.get("createdAt")
        )
        ts_dt = parse_iso_timestamp(ts_str)
        waiting_seconds = (
            max(0, int((current_time - ts_dt).total_seconds()))
            if ts_dt is not None
            else 0
        )
        waiting_duration = format_duration(waiting_seconds)

        game_url = f"{clean_base}/game/{game_id}"
        dashboard_url = f"{clean_base}/dashboard"

        is_my_turn = bool(
            status == "started"
            and my_player is not None
            and str(my_player.get("id")) in active_player_ids
        )

        # Check if an active player vote (call / undo) is waiting on this user
        vote = game.get("vote")
        needs_my_vote = False
        if status == "started" and my_player is not None and isinstance(vote, dict):
            voted_player_ids = set(vote.get("votes", {}).keys()) if isinstance(vote.get("votes"), dict) else set()
            votes_list = vote.get("votes") if isinstance(vote.get("votes"), list) else None
            if votes_list is not None:
                voted_player_ids = {
                    str(v.get("playerId")) for v in votes_list if isinstance(v, dict)
                }
            my_pid = str(my_player.get("id"))
            if my_pid not in voted_player_ids and vote.get("initiatorId") != my_pid:
                needs_my_vote = True

        is_invited = bool(
            status == "waitingForPlayers"
            and my_player is not None
            and my_player.get("status") == "invited"
        )

        is_ready_to_start = bool(
            status == "waitingToStart" and game.get("ownerId") == user_id
        )

        waiting_reason: str | None = None
        target_url = game_url
        if is_my_turn:
            waiting_reason = "Your Turn"
        elif needs_my_vote:
            waiting_reason = "Vote Requested"
        elif is_invited:
            waiting_reason = "Invitation"
            target_url = dashboard_url
        elif is_ready_to_start:
            waiting_reason = "Ready to Start"
            target_url = dashboard_url

        if waiting_reason is not None:
            waiting_games.append(
                WaitingGame(
                    game_id=game_id,
                    name=name,
                    type_id=type_id,
                    title=title,
                    waiting_reason=waiting_reason,
                    waiting_since=ts_dt.isoformat() if ts_dt else None,
                    waiting_seconds=waiting_seconds,
                    waiting_duration=waiting_duration,
                    last_action_by=last_action_by,
                    opponents=opponents,
                    url=target_url,
                )
            )

        if status == "started" and my_player is not None:
            active_player_names = [
                str(players_by_id[pid].get("name", pid))
                for pid in active_player_ids
                if pid in players_by_id
            ]
            active_games.append(
                ActiveGameSummary(
                    game_id=game_id,
                    name=name,
                    type_id=type_id,
                    title=title,
                    status=status,
                    is_my_turn=is_my_turn,
                    active_players=active_player_names,
                    last_action_at=ts_dt.isoformat() if ts_dt else None,
                    last_action_ago=waiting_duration if ts_dt else None,
                    last_action_by=last_action_by,
                    url=game_url,
                )
            )

    # Sort waiting games from longest waiting to newest
    waiting_games.sort(key=lambda g: (-g.waiting_seconds, g.name.lower()))
    # Sort active games with user's turn first, then longest since last action
    active_games.sort(
        key=lambda g: (not g.is_my_turn, g.name.lower())
    )

    return GamesSnapshot(
        user_id=user_id,
        username=username,
        waiting_games=waiting_games,
        active_games=active_games,
        raw_games_by_id=raw_games_by_id,
        catalog_titles=titles,
        fetched_at=current_time,
    )


class BoardTogetherClient:
    """Async HTTP client for boardtogether.games."""

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the BoardTogether API client."""
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._external_session = session is not None
        self._session = session
        self._user_id: str | None = None
        self._actual_username: str = username
        self._catalog_titles: dict[str, str] = {}
        self._session_cookie: str | None = None

    @property
    def base_url(self) -> str:
        """Return base URL."""
        return self._base_url

    @property
    def user_id(self) -> str | None:
        """Return authenticated user ID."""
        return self._user_id

    @property
    def username(self) -> str:
        """Return authenticated username."""
        return self._actual_username

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure an aiohttp ClientSession exists."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._external_session = False
        return self._session

    async def async_close(self) -> None:
        """Close the internal session if owned by this client."""
        if not self._external_session and self._session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        """Build request headers including stored session cookie if needed."""
        headers = {"Accept": "application/json"}
        if self._session_cookie:
            headers["Cookie"] = self._session_cookie
        return headers

    def _capture_cookies(self, response: aiohttp.ClientResponse) -> None:
        """Extract Set-Cookie header so auth works even with shared HA sessions."""
        set_cookies = response.headers.getall("Set-Cookie", [])
        if not set_cookies:
            return
        cookie_pairs: list[str] = []
        for header in set_cookies:
            first_part = header.split(";", 1)[0].strip()
            if "=" in first_part:
                cookie_pairs.append(first_part)
        if cookie_pairs:
            self._session_cookie = "; ".join(cookie_pairs)

    async def async_login(self) -> dict[str, Any]:
        """Authenticate with POST /api/v1/auth/login and store session cookie."""
        session = await self._ensure_session()
        url = f"{self._base_url}/api/v1/auth/login"
        try:
            async with session.post(
                url,
                json={"username": self._username, "password": self._password},
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status == 401:
                    raise BoardTogetherAuthError("Invalid username or password")
                if resp.status >= 400:
                    text = await resp.text()
                    raise BoardTogetherError(
                        f"Login failed with HTTP {resp.status}: {text[:200]}"
                    )
                self._capture_cookies(resp)
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise BoardTogetherConnectionError(
                f"Cannot connect to {self._base_url}: {err}"
            ) from err

        user = (data.get("payload") or {}).get("user") or {}
        if not user.get("id"):
            raise BoardTogetherAuthError("Login response did not include user ID")

        self._user_id = str(user["id"])
        self._actual_username = str(user.get("username") or self._username)
        return user

    async def _request_authed(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated API request, logging in on 401."""
        if not self._user_id or not self._session_cookie:
            await self.async_login()

        session = await self._ensure_session()
        url = f"{self._base_url}{path}"

        for attempt in range(2):
            try:
                async with session.request(
                    method,
                    url,
                    json=json_body,
                    headers=self._headers(),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        _LOGGER.debug("Session expired, re-authenticating with BoardTogether")
                        await self.async_login()
                        continue
                    if resp.status == 401:
                        raise BoardTogetherAuthError("Unauthorized after re-login")
                    if resp.status >= 400:
                        text = await resp.text()
                        raise BoardTogetherError(
                            f"HTTP {resp.status} for {path}: {text[:200]}"
                        )
                    self._capture_cookies(resp)
                    return await resp.json()
            except aiohttp.ClientError as err:
                raise BoardTogetherConnectionError(
                    f"Request failed for {path}: {err}"
                ) from err

        raise BoardTogetherError(f"Failed request to {path}")

    async def async_subscribe_webpush(
        self,
        endpoint: str,
        p256dh: str,
        auth: str,
    ) -> None:
        """Register Web Push subscription with POST /api/v1/notification/webpush/subscribe."""
        payload = {
            "subscription": {
                "endpoint": endpoint,
                "expirationTime": None,
                "keys": {
                    "p256dh": p256dh,
                    "auth": auth,
                },
            }
        }
        await self._request_authed(
            "POST",
            "/api/v1/notification/webpush/subscribe",
            json_body=payload,
        )

    async def async_get_catalog(self) -> dict[str, str]:
        """Fetch /api/v1/catalog to map game typeId to human-readable title."""
        if self._catalog_titles:
            return self._catalog_titles

        session = await self._ensure_session()
        url = f"{self._base_url}/api/v1/catalog"
        try:
            async with session.get(url, headers={"Accept": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    payload = data.get("payload")
                    if isinstance(payload, list):
                        for entry in payload:
                            if isinstance(entry, dict) and entry.get("id") and entry.get("name"):
                                self._catalog_titles[str(entry["id"])] = str(entry["name"])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not fetch game catalog (non-fatal): %s", err)

        return self._catalog_titles

    async def async_get_games_snapshot(self) -> GamesSnapshot:
        """Fetch current active games and analyze waiting games and durations."""
        catalog = await self.async_get_catalog()
        data = await self._request_authed("GET", "/api/v1/games/mine?scope=current")
        games = (data.get("payload") or {}).get("games") or []
        return analyze_games(
            games=games,
            user_id=self._user_id or "",
            username=self._actual_username,
            catalog_titles=catalog,
            base_url=self._base_url,
        )

    async def async_fetch_vapid_public_key(self) -> str:
        """Fetch the current VAPID public key from /service-worker.js with fallback."""
        session = await self._ensure_session()
        url = f"{self._base_url}/service-worker.js"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    js_text = await resp.text()
                    match = VAPID_KEY_REGEX.search(js_text)
                    if match:
                        return match.group(1)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Falling back to default VAPID key: %s", err)
        return DEFAULT_VAPID_PUBLIC_KEY
