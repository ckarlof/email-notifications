"""Constants for the BoardTogether integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "boardtogether"
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}."

DEFAULT_BASE_URL = "https://boardtogether.games"
DEFAULT_AUTOPUSH_URL = "wss://push.services.mozilla.com/"
DEFAULT_VAPID_PUBLIC_KEY = (
    "BAyfrK5NRNfun8M7Zug28V5nk6Y7a_LB8ha1EkRW-b6MU_p9tF5_rV0j1MYa-omqo1SSOdtpGfvUbtt24wDYwFA"
)
DEFAULT_NOTIFY_SERVICE = "notify.chris_karlof_email"

CONF_BASE_URL = "base_url"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_DEDUPE_MODE = "dedupe_mode"
CONF_ONLY_TURN_NOTIFICATIONS = "only_turn_notifications"
CONF_INCLUDE_WAITING_DIGEST = "include_waiting_digest"

DEDUPE_MODE_NOTIFICATION_ID = "notification_id"
DEDUPE_MODE_TURN = "turn"

DEFAULT_DEDUPE_MODE = DEDUPE_MODE_NOTIFICATION_ID
DEFAULT_ONLY_TURN_NOTIFICATIONS = False
DEFAULT_INCLUDE_WAITING_DIGEST = True

UPDATE_INTERVAL = timedelta(minutes=2)

EVENT_NOTIFICATION = f"{DOMAIN}_notification"

SERVICE_SEND_TEST_NOTIFICATION = "send_test_notification"
SERVICE_SEND_WAITING_GAMES_DIGEST = "send_waiting_games_digest"
SERVICE_REFRESH_GAMES = "refresh_games"

# UserNotificationAction values from @tabletop/common
ACTION_PLAYER_JOINED = "playerJoined"
ACTION_PLAYER_DECLINED = "playerDeclined"
ACTION_WAS_INVITED = "wasInvited"
ACTION_GAME_STARTED = "gameStarted"
ACTION_IS_YOUR_TURN = "isYourTurn"
ACTION_PLAYER_VOTE_REQUESTED = "playerVoteRequested"
