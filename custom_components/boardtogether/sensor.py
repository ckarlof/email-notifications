"""Sensor platform for BoardTogether."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BoardTogetherCoordinator
from .const import DOMAIN
from .formatter import build_waiting_games_markdown_table


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BoardTogether sensor entities from a config entry."""
    coordinator: BoardTogetherCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BoardTogetherWaitingGamesSensor(coordinator, entry),
            BoardTogetherLongestWaitSensor(coordinator, entry),
            BoardTogetherActiveGamesSensor(coordinator, entry),
            BoardTogetherLastNotificationSensor(coordinator, entry),
        ]
    )


class BoardTogetherBaseSensor(
    CoordinatorEntity[BoardTogetherCoordinator], SensorEntity
):
    """Base sensor for BoardTogether entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BoardTogetherCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize the base sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info grouping all sensors under the BoardTogether account."""
        username = self.coordinator.client.username
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"BoardTogether ({username})",
            manufacturer="BoardTogether",
            model="Web Push & Game Tracker",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{self.coordinator.client.base_url}/dashboard",
        )


class BoardTogetherWaitingGamesSensor(BoardTogetherBaseSensor):
    """Sensor showing count and details of all games currently waiting on the user."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "games"

    def __init__(
        self, coordinator: BoardTogetherCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the waiting games sensor."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            key="games_waiting_on_me",
            name="Games waiting on me",
            icon="mdi:chess-knight",
        )

    @property
    def native_value(self) -> int:
        """Return the number of games currently waiting on the user."""
        if not self.coordinator.data:
            return 0
        return len(self.coordinator.data.waiting_games)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed list of waiting games, durations, and a Lovelace Markdown table."""
        waiting = (
            self.coordinator.data.waiting_games if self.coordinator.data else []
        )
        longest = waiting[0] if waiting else None
        endpoint = self.coordinator.autopush_state.endpoint
        return {
            "waiting_games": [wg.to_dict() for wg in waiting],
            "longest_wait_duration": longest.waiting_duration if longest else "0m",
            "longest_wait_seconds": longest.waiting_seconds if longest else 0,
            "longest_wait_game": longest.name if longest else None,
            "longest_wait_title": longest.title if longest else None,
            "longest_wait_url": longest.url if longest else None,
            "markdown_table": build_waiting_games_markdown_table(
                waiting, self.coordinator.client.base_url
            ),
            "webpush_connected": bool(
                self.coordinator.subscriber and self.coordinator.subscriber.connected
            ),
            "webpush_registered": bool(endpoint),
        }


class BoardTogetherLongestWaitSensor(BoardTogetherBaseSensor):
    """Sensor showing how long (in minutes) the oldest pending game has been waiting on the user."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BoardTogetherCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the longest wait sensor."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            key="longest_wait",
            name="Longest waiting move",
            icon="mdi:timer-sand",
        )

    @property
    def native_value(self) -> int:
        """Return the wait duration in minutes of the oldest waiting game."""
        if not self.coordinator.data or not self.coordinator.data.waiting_games:
            return 0
        longest = self.coordinator.data.waiting_games[0]
        return longest.waiting_seconds // 60

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return metadata for the longest-waiting game."""
        if not self.coordinator.data or not self.coordinator.data.waiting_games:
            return {
                "game_name": None,
                "game_title": None,
                "waiting_reason": None,
                "waiting_duration": "0m",
                "waiting_seconds": 0,
                "waiting_since": None,
                "last_action_by": None,
                "url": None,
            }
        longest = self.coordinator.data.waiting_games[0]
        return {
            "game_name": longest.name,
            "game_title": longest.title,
            "waiting_reason": longest.waiting_reason,
            "waiting_duration": longest.waiting_duration,
            "waiting_seconds": longest.waiting_seconds,
            "waiting_since": longest.waiting_since,
            "last_action_by": longest.last_action_by,
            "url": longest.url,
        }


class BoardTogetherActiveGamesSensor(BoardTogetherBaseSensor):
    """Sensor showing all active started games the user is participating in."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "games"

    def __init__(
        self, coordinator: BoardTogetherCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the active games sensor."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            key="active_games",
            name="Active games",
            icon="mdi:dice-multiple",
        )

    @property
    def native_value(self) -> int:
        """Return total number of started games the user is in."""
        if not self.coordinator.data:
            return 0
        return len(self.coordinator.data.active_games)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return list of active games and whose turn it is."""
        active = self.coordinator.data.active_games if self.coordinator.data else []
        return {
            "active_games": [ag.to_dict() for ag in active],
        }


class BoardTogetherLastNotificationSensor(BoardTogetherBaseSensor):
    """Sensor showing the most recent Web Push notification received from BoardTogether."""

    def __init__(
        self, coordinator: BoardTogetherCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the last notification sensor."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            key="last_notification",
            name="Last notification",
            icon="mdi:bell-ring",
        )

    @property
    def native_value(self) -> str:
        """Return summary text of the last notification."""
        last = self.coordinator.autopush_state.last_notification
        if not last:
            return "None"
        return str(last.get("summary") or last.get("title") or "Notification")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return full details of the last notification."""
        last = self.coordinator.autopush_state.last_notification
        if not last:
            return {}
        return dict(last)
