"""Unit and state-machine tests for the Autopush WebSocket subscriber."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from custom_components.boardtogether.autopush import AutopushState, AutopushSubscriber
from custom_components.boardtogether.webpush_crypto import (
    b64url_encode,
    encrypt_aes128gcm,
)


class FakeWSMessage:
    """Simulated aiohttp WSMessage."""

    def __init__(self, msg_type: aiohttp.WSMsgType, data: str = "") -> None:
        self.type = msg_type
        self.data = data


class FakeWebSocket:
    """In-memory fake WebSocket implementing the Autopush server protocol."""

    def __init__(self, state: AutopushState, sample_notification: dict[str, Any]) -> None:
        self._state = state
        self._sample_notification = sample_notification
        self._incoming: asyncio.Queue[FakeWSMessage] = asyncio.Queue()
        self.sent_messages: list[dict[str, Any]] = []
        self.closed = False

    async def __aenter__(self) -> FakeWebSocket:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent_messages.append(data)
        msg_type = data.get("messageType")
        if msg_type == "hello":
            await self._incoming.put(
                FakeWSMessage(
                    aiohttp.WSMsgType.TEXT,
                    json.dumps(
                        {
                            "messageType": "hello",
                            "status": 200,
                            "uaid": "uaid-test-123",
                            "use_webpush": True,
                        }
                    ),
                )
            )
        elif msg_type == "register":
            channel_id = data["channelID"]
            await self._incoming.put(
                FakeWSMessage(
                    aiohttp.WSMsgType.TEXT,
                    json.dumps(
                        {
                            "messageType": "register",
                            "status": 200,
                            "channelID": channel_id,
                            "pushEndpoint": f"https://updates.push.services.mozilla.com/wpush/v2/{channel_id}",
                        }
                    ),
                )
            )
            ciphertext = encrypt_aes128gcm(
                json.dumps(self._sample_notification).encode("utf-8"),
                self._state.keys.p256dh,
                self._state.keys.auth,
            )
            await self._incoming.put(
                FakeWSMessage(
                    aiohttp.WSMsgType.TEXT,
                    json.dumps(
                        {
                            "messageType": "notification",
                            "channelID": channel_id,
                            "version": "v-100",
                            "data": b64url_encode(ciphertext),
                            "headers": {"encoding": "aes128gcm"},
                        }
                    ),
                )
            )
        elif msg_type == "ack":
            await self._incoming.put(FakeWSMessage(aiohttp.WSMsgType.CLOSED))

    async def close(self) -> None:
        self.closed = True
        await self._incoming.put(FakeWSMessage(aiohttp.WSMsgType.CLOSED))

    def __aiter__(self) -> FakeWebSocket:
        return self

    async def __anext__(self) -> FakeWSMessage:
        msg = await self._incoming.get()
        if msg.type == aiohttp.WSMsgType.CLOSED:
            raise StopAsyncIteration
        return msg


class FakeSession:
    """In-memory fake ClientSession."""

    def __init__(self, ws: FakeWebSocket) -> None:
        self._ws = ws
        self.closed = False

    def ws_connect(self, *args: Any, **kwargs: Any) -> FakeWebSocket:
        return self._ws

    async def close(self) -> None:
        self.closed = True


def test_autopush_handshake_and_notification_flow() -> None:
    """Test hello -> register -> notification -> ack flow."""

    async def _run() -> None:
        state = AutopushState()
        registered_endpoints: list[tuple[str, str, str]] = []
        received_notifications: list[dict[str, Any]] = []
        saved_states: list[dict[str, Any]] = []

        sample_notification = {
            "id": "notif-live-1",
            "type": "user",
            "action": "isYourTurn",
            "data": {
                "user": {"id": "u-1"},
                "game": {"id": "g-1", "typeId": "indonesia", "name": "Test Match"},
            },
        }

        fake_ws = FakeWebSocket(state, sample_notification)
        fake_session = FakeSession(fake_ws)

        async def on_register(endpoint: str, p256dh: str, auth: str) -> None:
            registered_endpoints.append((endpoint, p256dh, auth))

        async def on_notification(payload: dict[str, Any]) -> None:
            received_notifications.append(payload)

        async def on_save(st: AutopushState) -> None:
            saved_states.append(st.to_dict())

        subscriber = AutopushSubscriber(
            state=state,
            on_register_endpoint=on_register,
            on_notification=on_notification,
            on_save_state=on_save,
            session=fake_session,  # type: ignore[arg-type]
        )

        await subscriber.async_connect_and_listen()

        assert len(registered_endpoints) == 1
        assert registered_endpoints[0][0].startswith(
            "https://updates.push.services.mozilla.com/wpush/v2/"
        )
        assert received_notifications == [sample_notification]
        ack_msgs = [m for m in fake_ws.sent_messages if m.get("messageType") == "ack"]
        assert len(ack_msgs) == 1
        assert ack_msgs[0]["updates"][0]["version"] == "v-100"
        assert state.uaid == "uaid-test-123"

    asyncio.run(_run())
