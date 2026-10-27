"""Mozilla Autopush WebSocket client for receiving BoardTogether Web Push notifications."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any
import uuid

import aiohttp

from .const import DEFAULT_AUTOPUSH_URL, DEFAULT_VAPID_PUBLIC_KEY
from .webpush_crypto import WebPushClientKeys, decrypt_webpush_payload

_LOGGER = logging.getLogger(__name__)

MAX_DEDUPE_HISTORY = 200


class AutopushState:
    """In-memory and serializable state for Mozilla Autopush and deduplication."""

    def __init__(
        self,
        uaid: str | None = None,
        channel_id: str | None = None,
        endpoint: str | None = None,
        vapid_key: str | None = None,
        keys: WebPushClientKeys | None = None,
        seen_notification_ids: list[str] | None = None,
        seen_turn_keys: list[str] | None = None,
        last_notification: dict[str, Any] | None = None,
    ) -> None:
        """Initialize Autopush state."""
        self.uaid = uaid
        self.channel_id = channel_id or str(uuid.uuid4())
        self.endpoint = endpoint
        self.vapid_key = vapid_key
        self.keys = keys or WebPushClientKeys.generate()
        self.seen_notification_ids = list(seen_notification_ids or [])
        self.seen_turn_keys = list(seen_turn_keys or [])
        self.last_notification = last_notification

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AutopushState:
        """Restore state from a dictionary."""
        if not data:
            return cls()
        keys_raw = data.get("keys")
        keys = (
            WebPushClientKeys.from_dict(keys_raw)
            if isinstance(keys_raw, dict)
            and all(k in keys_raw for k in ("private_key_b64", "p256dh", "auth"))
            else WebPushClientKeys.generate()
        )
        return cls(
            uaid=data.get("uaid"),
            channel_id=data.get("channel_id") or str(uuid.uuid4()),
            endpoint=data.get("endpoint"),
            vapid_key=data.get("vapid_key"),
            keys=keys,
            seen_notification_ids=data.get("seen_notification_ids") or [],
            seen_turn_keys=data.get("seen_turn_keys") or [],
            last_notification=data.get("last_notification"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to dictionary for persistence."""
        return {
            "uaid": self.uaid,
            "channel_id": self.channel_id,
            "endpoint": self.endpoint,
            "vapid_key": self.vapid_key,
            "keys": self.keys.to_dict(),
            "seen_notification_ids": self.seen_notification_ids[-MAX_DEDUPE_HISTORY:],
            "seen_turn_keys": self.seen_turn_keys[-MAX_DEDUPE_HISTORY:],
            "last_notification": self.last_notification,
        }

    def mark_notification_seen(self, notification_id: str, turn_key: str) -> None:
        """Record notification_id and turn_key in bounded deduplication lists."""
        if notification_id and notification_id not in self.seen_notification_ids:
            self.seen_notification_ids.append(notification_id)
            if len(self.seen_notification_ids) > MAX_DEDUPE_HISTORY:
                self.seen_notification_ids = self.seen_notification_ids[-MAX_DEDUPE_HISTORY:]

        if turn_key and turn_key not in self.seen_turn_keys:
            self.seen_turn_keys.append(turn_key)
            if len(self.seen_turn_keys) > MAX_DEDUPE_HISTORY:
                self.seen_turn_keys = self.seen_turn_keys[-MAX_DEDUPE_HISTORY:]


class AutopushSubscriber:
    """Persistent WebSocket client for Mozilla Autopush (wss://push.services.mozilla.com/)."""

    def __init__(
        self,
        state: AutopushState,
        on_register_endpoint: Callable[[str, str, str], Awaitable[None]],
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
        on_save_state: Callable[[AutopushState], Awaitable[None]],
        vapid_public_key: str = DEFAULT_VAPID_PUBLIC_KEY,
        autopush_url: str = DEFAULT_AUTOPUSH_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the Autopush subscriber."""
        self.state = state
        self._on_register_endpoint = on_register_endpoint
        self._on_notification = on_notification
        self._on_save_state = on_save_state
        self._vapid_public_key = vapid_public_key
        self._autopush_url = autopush_url
        self._external_session = session is not None
        self._session = session
        self._stop_event = asyncio.Event()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task[None] | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """Return True if the WebSocket is currently connected."""
        return self._connected

    def update_vapid_key(self, vapid_public_key: str) -> None:
        """Update VAPID public key."""
        self._vapid_public_key = vapid_public_key

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure an aiohttp ClientSession exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._external_session = False
        return self._session

    def start(self) -> asyncio.Task[None]:
        """Start the background Autopush listener task."""
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self.async_run_forever(),
                name="boardtogether_autopush_listener",
            )
        return self._task

    async def async_stop(self) -> None:
        """Stop the Autopush listener task and close the WebSocket."""
        self._stop_event.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if not self._external_session and self._session and not self._session.closed:
            await self._session.close()
        self._connected = False

    async def async_run_forever(self) -> None:
        """Run the Autopush WebSocket connection loop with exponential backoff."""
        backoff = 2.0
        while not self._stop_event.is_set():
            try:
                await self.async_connect_and_listen()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self._connected = False
                if self._stop_event.is_set():
                    break
                _LOGGER.warning(
                    "BoardTogether Web Push connection interrupted (%s); reconnecting in %.0fs",
                    err,
                    backoff,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def async_connect_and_listen(self) -> None:
        """Connect once to Autopush, complete handshake, and process frames until closed."""
        session = await self._ensure_session()
        async with session.ws_connect(
            self._autopush_url,
            heartbeat=45.0,
        ) as ws:
            self._ws = ws
            has_existing_channel = bool(
                self.state.uaid
                and self.state.endpoint
                and self.state.vapid_key == self._vapid_public_key
            )
            hello_msg: dict[str, Any] = {
                "messageType": "hello",
                "use_webpush": True,
                "uaid": self.state.uaid or "",
                "channelIDs": [self.state.channel_id] if has_existing_channel else [],
            }
            await ws.send_json(hello_msg)

            async for msg in ws:
                if self._stop_event.is_set():
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(ws, data, has_existing_channel)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

        self._connected = False

    async def _handle_message(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        data: dict[str, Any],
        had_existing_channel: bool,
    ) -> None:
        """Handle a single incoming Autopush WebSocket message."""
        msg_type = data.get("messageType")

        if msg_type == "hello":
            status = data.get("status", 200)
            if status != 200:
                raise RuntimeError(f"Autopush hello failed with status {status}: {data}")

            returned_uaid = data.get("uaid")
            uaid_changed = returned_uaid != self.state.uaid
            self.state.uaid = returned_uaid
            self._connected = True

            if uaid_changed or not had_existing_channel or not self.state.endpoint:
                # Register a channel with the VAPID public key
                if uaid_changed and had_existing_channel:
                    self.state.channel_id = str(uuid.uuid4())
                register_msg = {
                    "messageType": "register",
                    "channelID": self.state.channel_id,
                    "key": self._vapid_public_key,
                }
                await ws.send_json(register_msg)
            else:
                # Ensure BoardTogether still has our active subscription registered
                await self._on_register_endpoint(
                    self.state.endpoint,
                    self.state.keys.p256dh,
                    self.state.keys.auth,
                )
                await self._on_save_state(self.state)

        elif msg_type == "register":
            status = data.get("status", 200)
            if status != 200:
                raise RuntimeError(f"Autopush register failed with status {status}: {data}")

            endpoint = data.get("pushEndpoint")
            if not endpoint:
                raise RuntimeError(f"Autopush register response missing pushEndpoint: {data}")

            self.state.endpoint = str(endpoint)
            self.state.vapid_key = self._vapid_public_key
            await self._on_register_endpoint(
                self.state.endpoint,
                self.state.keys.p256dh,
                self.state.keys.auth,
            )
            await self._on_save_state(self.state)
            _LOGGER.info(
                "Registered BoardTogether Web Push subscription via Autopush (%s)",
                self.state.endpoint[:60],
            )

        elif msg_type == "notification":
            channel_id = data.get("channelID")
            version = data.get("version")
            raw_b64 = data.get("data")
            headers = data.get("headers")

            if raw_b64:
                try:
                    decrypted = decrypt_webpush_payload(raw_b64, self.state.keys, headers)
                    payload = json.loads(decrypted.decode("utf-8"))
                    await self._on_notification(payload)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Failed to process BoardTogether Web Push payload: %s", err)

            if channel_id and version:
                ack_msg = {
                    "messageType": "ack",
                    "updates": [{"channelID": channel_id, "version": version}],
                }
                await ws.send_json(ack_msg)
