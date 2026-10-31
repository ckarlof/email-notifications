"""Config flow for the BoardTogether integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .api import (
    BoardTogetherAuthError,
    BoardTogetherClient,
    BoardTogetherConnectionError,
    BoardTogetherError,
)
from .const import (
    CONF_BASE_URL,
    CONF_DEDUPE_MODE,
    CONF_INCLUDE_WAITING_DIGEST,
    CONF_NOTIFY_SERVICE,
    CONF_ONLY_TURN_NOTIFICATIONS,
    DEDUPE_MODE_NOTIFICATION_ID,
    DEDUPE_MODE_TURN,
    DEFAULT_BASE_URL,
    DEFAULT_DEDUPE_MODE,
    DEFAULT_INCLUDE_WAITING_DIGEST,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_ONLY_TURN_NOTIFICATIONS,
    DOMAIN,
    LEGACY_NOTIFY_SERVICE_MAP,
)

_LOGGER = logging.getLogger(__name__)


def _get_notify_service_options(hass: HomeAssistant) -> list[str]:
    """Return a list of available notify.<entity_or_service> targets in Home Assistant."""
    entity_ids = sorted(hass.states.async_entity_ids("notify"))
    services = hass.services.async_services().get("notify", {})
    service_options = [
        f"notify.{svc}"
        for svc in sorted(services.keys())
        if svc not in ("send_message", "notify")
        and f"notify.{svc}" not in LEGACY_NOTIFY_SERVICE_MAP
    ]
    options = list(dict.fromkeys([*entity_ids, *service_options]))
    if DEFAULT_NOTIFY_SERVICE not in options:
        options.insert(0, DEFAULT_NOTIFY_SERVICE)
    return options


class BoardTogetherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BoardTogether."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/")

            client = BoardTogetherClient(
                username=username,
                password=password,
                base_url=base_url,
            )
            try:
                user = await client.async_login()
            except BoardTogetherAuthError:
                errors["base"] = "invalid_auth"
            except BoardTogetherConnectionError:
                errors["base"] = "cannot_connect"
            except BoardTogetherError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during BoardTogether login")
                errors["base"] = "unknown"
            finally:
                await client.async_close()

            if not errors:
                user_id = str(user.get("id") or username)
                actual_username = str(user.get("username") or username)
                await self.async_set_unique_id(f"{base_url}:{user_id}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"BoardTogether ({actual_username})",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_BASE_URL: base_url,
                        CONF_NOTIFY_SERVICE: user_input.get(
                            CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE
                        ),
                        CONF_DEDUPE_MODE: user_input.get(
                            CONF_DEDUPE_MODE, DEFAULT_DEDUPE_MODE
                        ),
                        CONF_ONLY_TURN_NOTIFICATIONS: user_input.get(
                            CONF_ONLY_TURN_NOTIFICATIONS,
                            DEFAULT_ONLY_TURN_NOTIFICATIONS,
                        ),
                        CONF_INCLUDE_WAITING_DIGEST: user_input.get(
                            CONF_INCLUDE_WAITING_DIGEST,
                            DEFAULT_INCLUDE_WAITING_DIGEST,
                        ),
                    },
                )

        notify_options = _get_notify_service_options(self.hass)
        default_notify = (
            DEFAULT_NOTIFY_SERVICE
            if DEFAULT_NOTIFY_SERVICE in notify_options
            else notify_options[0]
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_NOTIFY_SERVICE, default=default_notify): str,
                vol.Optional(
                    CONF_DEDUPE_MODE, default=DEFAULT_DEDUPE_MODE
                ): vol.In(
                    {
                        DEDUPE_MODE_TURN: "Once per turn (recommended)",
                        DEDUPE_MODE_NOTIFICATION_ID: "Every notification & reminder (1m, +1h, +4h, +8h, +12h)",
                    }
                ),
                vol.Optional(
                    CONF_ONLY_TURN_NOTIFICATIONS,
                    default=DEFAULT_ONLY_TURN_NOTIFICATIONS,
                ): bool,
                vol.Optional(
                    CONF_INCLUDE_WAITING_DIGEST,
                    default=DEFAULT_INCLUDE_WAITING_DIGEST,
                ): bool,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return BoardTogetherOptionsFlowHandler(config_entry)


class BoardTogetherOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle BoardTogether options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    default=current.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE),
                ): str,
                vol.Optional(
                    CONF_DEDUPE_MODE,
                    default=current.get(CONF_DEDUPE_MODE, DEFAULT_DEDUPE_MODE),
                ): vol.In(
                    {
                        DEDUPE_MODE_TURN: "Once per turn (recommended)",
                        DEDUPE_MODE_NOTIFICATION_ID: "Every notification & reminder (1m, +1h, +4h, +8h, +12h)",
                    }
                ),
                vol.Optional(
                    CONF_ONLY_TURN_NOTIFICATIONS,
                    default=current.get(
                        CONF_ONLY_TURN_NOTIFICATIONS,
                        DEFAULT_ONLY_TURN_NOTIFICATIONS,
                    ),
                ): bool,
                vol.Optional(
                    CONF_INCLUDE_WAITING_DIGEST,
                    default=current.get(
                        CONF_INCLUDE_WAITING_DIGEST,
                        DEFAULT_INCLUDE_WAITING_DIGEST,
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
