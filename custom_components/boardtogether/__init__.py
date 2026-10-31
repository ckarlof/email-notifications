"""The BoardTogether integration for Home Assistant."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BoardTogetherAuthError,
    BoardTogetherClient,
    BoardTogetherError,
    GamesSnapshot,
)
from .autopush import AutopushState, AutopushSubscriber
from .const import (
    ACTION_IS_YOUR_TURN,
    CONF_BASE_URL,
    CONF_DEDUPE_MODE,
    CONF_INCLUDE_WAITING_DIGEST,
    CONF_NOTIFY_SERVICE,
    CONF_ONLY_TURN_NOTIFICATIONS,
    DEDUPE_MODE_TURN,
    DEFAULT_BASE_URL,
    DEFAULT_DEDUPE_MODE,
    DEFAULT_INCLUDE_WAITING_DIGEST,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_ONLY_TURN_NOTIFICATIONS,
    DOMAIN,
    EVENT_NOTIFICATION,
    LEGACY_NOTIFY_SERVICE_MAP,
    SERVICE_REFRESH_GAMES,
    SERVICE_SEND_TEST_NOTIFICATION,
    SERVICE_SEND_WAITING_GAMES_DIGEST,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)
from .formatter import (
    format_user_notification,
    format_waiting_games_digest_email,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


class BoardTogetherCoordinator(DataUpdateCoordinator[GamesSnapshot]):
    """Coordinator managing active games polling, Autopush Web Push, and email delivery."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: BoardTogetherClient,
        store: Store[dict[str, Any]],
        autopush_state: AutopushState,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.client = client
        self.store = store
        self.autopush_state = autopush_state
        self.subscriber: AutopushSubscriber | None = None

    def _get_option(self, key: str, default: Any) -> Any:
        """Return an option from entry.options with fallback to entry.data."""
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    @property
    def notify_service(self) -> str:
        """Return configured notify target string (e.g. 'notify.chris_karlof_email_chris_karlof_gmail_com')."""
        raw = str(self._get_option(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE)).strip()
        return LEGACY_NOTIFY_SERVICE_MAP.get(raw, raw)

    @property
    def dedupe_mode(self) -> str:
        """Return configured deduplication mode."""
        return str(self._get_option(CONF_DEDUPE_MODE, DEFAULT_DEDUPE_MODE))

    @property
    def only_turn_notifications(self) -> bool:
        """Return True if only isYourTurn notifications should be emailed."""
        return bool(
            self._get_option(
                CONF_ONLY_TURN_NOTIFICATIONS, DEFAULT_ONLY_TURN_NOTIFICATIONS
            )
        )

    @property
    def include_waiting_digest(self) -> bool:
        """Return True if emails should include the 'All Games Waiting on You' section."""
        return bool(
            self._get_option(
                CONF_INCLUDE_WAITING_DIGEST, DEFAULT_INCLUDE_WAITING_DIGEST
            )
        )

    async def _async_update_data(self) -> GamesSnapshot:
        """Fetch latest active games snapshot from BoardTogether."""
        try:
            return await self.client.async_get_games_snapshot()
        except BoardTogetherAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BoardTogetherError as err:
            raise UpdateFailed(str(err)) from err

    async def async_start_autopush(self) -> None:
        """Fetch VAPID key and start the background Autopush WebSocket subscriber."""
        vapid_key = await self.client.async_fetch_vapid_public_key()
        self.subscriber = AutopushSubscriber(
            state=self.autopush_state,
            on_register_endpoint=self.client.async_subscribe_webpush,
            on_notification=self.async_handle_push_notification,
            on_save_state=self.async_save_state,
            vapid_public_key=vapid_key,
        )
        self.subscriber.start()

    async def async_stop(self) -> None:
        """Stop background tasks and close API session."""
        if self.subscriber is not None:
            await self.subscriber.async_stop()
        await self.client.async_close()

    async def async_save_state(self, state: AutopushState) -> None:
        """Persist Autopush and deduplication state to Home Assistant .storage."""
        await self.store.async_save(state.to_dict())

    async def async_seed_current_waiting_turns(self) -> None:
        """Seed seen_turn_keys with currently active waiting turns on startup if not yet recorded."""
        if not self.data:
            return
        changed = False
        for wg in self.data.waiting_games:
            if wg.waiting_reason == "Your Turn" and wg.waiting_since:
                turn_key = f"{ACTION_IS_YOUR_TURN}:{wg.game_id}:{wg.waiting_since}"
                if turn_key not in self.autopush_state.seen_turn_keys:
                    self.autopush_state.mark_notification_seen(None, turn_key)
                    changed = True
        if changed:
            await self.async_save_state(self.autopush_state)

    async def async_handle_push_notification(
        self, payload: dict[str, Any], force_send: bool = False
    ) -> None:
        """Process a decrypted Web Push notification payload from BoardTogether."""
        _LOGGER.debug("Received BoardTogether push payload: %s", payload)

        action = str(payload.get("action") or "")
        if not force_send and self.only_turn_notifications and action != ACTION_IS_YOUR_TURN:
            _LOGGER.debug(
                "Skipping notification action '%s' because only_turn_notifications is enabled",
                action,
            )
            return

        # Fetch a fresh un-debounced games snapshot so we verify real-time turn state
        snapshot = self.data
        try:
            snapshot = await self.client.async_get_games_snapshot()
            self.async_set_updated_data(snapshot)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Could not fetch live games snapshot on push notification (%s); falling back to cached snapshot",
                err,
            )

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        game_obj = data.get("game") if isinstance(data.get("game"), dict) else {}
        game_id = str(game_obj.get("id") or "")

        # Strict false-positive guard: for isYourTurn, confirm the game is ACTUALLY waiting on the user right now
        if not force_send and action == ACTION_IS_YOUR_TURN and snapshot is not None and game_id:
            active_turn_ids = {
                wg.game_id
                for wg in snapshot.waiting_games
                if wg.waiting_reason == "Your Turn"
            }
            if game_id not in active_turn_ids:
                _LOGGER.info(
                    "Dropping stale isYourTurn notification for game %s (%s): game is no longer waiting on user",
                    game_obj.get("name") or game_id,
                    game_id,
                )
                return

        formatted = format_user_notification(
            payload=payload,
            base_url=self.client.base_url,
            snapshot=snapshot,
            include_waiting_digest=self.include_waiting_digest,
        )
        if formatted is None:
            _LOGGER.debug("Ignored unrecognized notification payload: %s", payload)
            return

        if not force_send:
            if (
                formatted.notification_id
                and formatted.notification_id in self.autopush_state.seen_notification_ids
            ):
                _LOGGER.debug(
                    "Skipping duplicate notification_id %s", formatted.notification_id
                )
                return
            if self.dedupe_mode == DEDUPE_MODE_TURN:
                seen_turns = set(self.autopush_state.seen_turn_keys)
                if (
                    formatted.turn_dedupe_key in seen_turns
                    or (
                        formatted.legacy_turn_dedupe_key
                        and formatted.legacy_turn_dedupe_key in seen_turns
                    )
                ):
                    _LOGGER.info(
                        "Skipping reminder for already-notified turn %s (notification_id=%s)",
                        formatted.turn_dedupe_key,
                        formatted.notification_id,
                    )
                    self.autopush_state.mark_notification_seen(
                        formatted.notification_id, formatted.turn_dedupe_key
                    )
                    await self.async_save_state(self.autopush_state)
                    return

        now_iso = datetime.now(timezone.utc).isoformat()
        event_data = {
            **formatted.to_event_dict(),
            "received_at": now_iso,
        }
        self.autopush_state.last_notification = event_data
        self.autopush_state.mark_notification_seen(
            formatted.notification_id, formatted.turn_dedupe_key
        )
        await self.async_save_state(self.autopush_state)

        # Notify entities that last_notification updated
        self.async_update_listeners()

        # Fire event on Home Assistant event bus
        self.hass.bus.async_fire(EVENT_NOTIFICATION, event_data)

        # Call configured notify service (e.g., notify.chris_karlof_email)
        await self.async_send_notify(title=formatted.title, message=formatted.message)

    async def async_send_notify(self, title: str, message: str) -> None:
        """Send an email/notification using notify.send_message (entity target) or a legacy notify service."""
        service_full = self.notify_service
        if not service_full:
            return
        if "." in service_full:
            domain, service_name = service_full.split(".", 1)
        else:
            domain, service_name = "notify", service_full

        entity_candidate = f"{domain}.{service_name}"
        if domain == "notify" and (
            self.hass.states.get(entity_candidate) is not None
            or not self.hass.services.has_service(domain, service_name)
        ):
            await self.hass.services.async_call(
                "notify",
                "send_message",
                {"title": title, "message": message},
                target={"entity_id": entity_candidate},
                blocking=True,
            )
            return

        await self.hass.services.async_call(
            domain,
            service_name,
            {"title": title, "message": message},
            blocking=True,
        )

    async def async_send_waiting_games_digest(self) -> None:
        """Refresh games and send an on-demand digest email of all games waiting on the user."""
        await self.async_request_refresh()
        waiting_games = self.data.waiting_games if self.data else []
        title, message = format_waiting_games_digest_email(
            waiting_games=waiting_games,
            base_url=self.client.base_url,
        )
        await self.async_send_notify(title=title, message=message)

    async def async_send_test_notification(self) -> None:
        """Send a test notification through the full formatting and email pipeline."""
        await self.async_request_refresh()
        waiting_games = self.data.waiting_games if self.data else []
        if waiting_games:
            sample = waiting_games[0]
            game_id = sample.game_id
            game_name = sample.name
            game_type_id = sample.type_id
        else:
            game_id = "test-game-id"
            game_name = "Sample Tabletop Match"
            game_type_id = "indonesia"

        sample_payload = {
            "id": f"test-{int(datetime.now(timezone.utc).timestamp())}",
            "type": "user",
            "action": ACTION_IS_YOUR_TURN,
            "data": {
                "user": {"id": self.client.user_id or "self"},
                "game": {
                    "id": game_id,
                    "typeId": game_type_id,
                    "name": game_name,
                },
            },
        }
        await self.async_handle_push_notification(sample_payload, force_send=True)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to the latest version."""
    _LOGGER.info(
        "Migrating BoardTogether config entry %s from version %s",
        config_entry.entry_id,
        config_entry.version,
    )
    new_data = dict(config_entry.data)
    new_options = dict(config_entry.options)

    if config_entry.version < 2:
        new_data[CONF_DEDUPE_MODE] = DEDUPE_MODE_TURN
        new_data[CONF_INCLUDE_WAITING_DIGEST] = False
        if CONF_DEDUPE_MODE in new_options:
            new_options[CONF_DEDUPE_MODE] = DEDUPE_MODE_TURN
        if CONF_INCLUDE_WAITING_DIGEST in new_options:
            new_options[CONF_INCLUDE_WAITING_DIGEST] = False

    if config_entry.version < 3:
        raw_data_svc = str(new_data.get(CONF_NOTIFY_SERVICE, "")).strip()
        if raw_data_svc in LEGACY_NOTIFY_SERVICE_MAP:
            new_data[CONF_NOTIFY_SERVICE] = LEGACY_NOTIFY_SERVICE_MAP[raw_data_svc]
        raw_opt_svc = str(new_options.get(CONF_NOTIFY_SERVICE, "")).strip()
        if raw_opt_svc in LEGACY_NOTIFY_SERVICE_MAP:
            new_options[CONF_NOTIFY_SERVICE] = LEGACY_NOTIFY_SERVICE_MAP[raw_opt_svc]

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=new_options,
            version=3,
        )
        _LOGGER.info(
            "Migrated BoardTogether config entry %s to version 3 (notify_service=%s)",
            config_entry.entry_id,
            new_options.get(CONF_NOTIFY_SERVICE) or new_data.get(CONF_NOTIFY_SERVICE),
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BoardTogether from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

    client = BoardTogetherClient(
        username=username,
        password=password,
        base_url=base_url,
    )
    try:
        await client.async_login()
    except BoardTogetherAuthError as err:
        await client.async_close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except BoardTogetherError as err:
        await client.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}{entry.entry_id}"
    )
    stored_raw = await store.async_load()
    autopush_state = AutopushState.from_dict(stored_raw)

    coordinator = BoardTogetherCoordinator(
        hass=hass,
        entry=entry,
        client=client,
        store=store,
        autopush_state=autopush_state,
    )
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_seed_current_waiting_turns()
    await coordinator.async_start_autopush()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_TEST_NOTIFICATION):

        async def handle_send_test(call: ServiceCall) -> None:
            for coordinator in hass.data.get(DOMAIN, {}).values():
                if isinstance(coordinator, BoardTogetherCoordinator):
                    await coordinator.async_send_test_notification()

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_TEST_NOTIFICATION,
            handle_send_test,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_WAITING_GAMES_DIGEST):

        async def handle_send_digest(call: ServiceCall) -> None:
            for coordinator in hass.data.get(DOMAIN, {}).values():
                if isinstance(coordinator, BoardTogetherCoordinator):
                    await coordinator.async_send_waiting_games_digest()

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_WAITING_GAMES_DIGEST,
            handle_send_digest,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_GAMES):

        async def handle_refresh(call: ServiceCall) -> None:
            for coordinator in hass.data.get(DOMAIN, {}).values():
                if isinstance(coordinator, BoardTogetherCoordinator):
                    await coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_GAMES,
            handle_refresh,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: BoardTogetherCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.async_stop()
    return unload_ok
