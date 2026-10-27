# BoardTogether Home Assistant Integration (HACS)

A custom Home Assistant integration (`custom_components/boardtogether`) for [boardtogether.games](https://boardtogether.games) that:

1. **Subscribes to Web Push Notifications (Zero Open Ports):** Connects outbound to Mozilla's standard Web Push relay (`wss://push.services.mozilla.com/`), registers a Web Push subscription with your `boardtogether.games` account, and decrypts incoming notifications (`isYourTurn`, `gameStarted`, `wasInvited`, `playerJoined`, `playerDeclined`, `playerVoteRequested`) in real time.
2. **Sends Rich Turn Notification Emails:** Calls your Home Assistant email notification service (defaults to `notify.chris_karlof_email`) with a direct link to the game (`https://boardtogether.games/game/<id>`) and a digest of **all games currently waiting on you** and how long each has been waiting.
3. **Tracks All Games Waiting on You & Wait Times:** Polls `GET /api/v1/games/mine?scope=current` every 2 minutes (and immediately on every Web Push) to expose sensors for pending moves, wait durations, and a pre-rendered Lovelace Markdown table.

---

## Installation via HACS

1. Push this repository to GitHub (e.g., `https://github.com/ckarlof/email-notifications`).
2. In Home Assistant, open **HACS → Integrations → ⋮ (top right menu) → Custom repositories**.
3. Add your repository URL with category **Integration**, then click **Download**.
4. **Restart Home Assistant**.
5. Go to **Settings → Devices & Services → Add Integration** and search for **BoardTogether**.

---

## Configuration Options

During setup (or anytime via **Configure** on the integration entry), you can customize:
* **Username / Password:** Your `boardtogether.games` login credentials.
* **Notification Service:** Defaults to `notify.chris_karlof_email`.
* **Notification Frequency (`dedupe_mode`):**
  * `notification_id` *(default)*: Emails you on every push notification sent by BoardTogether (initial 60s turn notice + scheduled 1h/4h/8h/12h reminders when you haven't moved yet).
  * `turn`: Only emails you **once per turn**, suppressing follow-up reminders for the same turn.
* **Only send 'It's your turn' notifications:** When enabled, filters out `playerJoined`, `playerDeclined`, `wasInvited`, `gameStarted`, and `playerVoteRequested` pushes.
* **Include summary of all games waiting on you in each email:** Appends a list of all games currently awaiting your move (sorted by longest wait time) to the bottom of every notification email.

---

## Entities Created

| Entity | Description | Key Attributes |
| :--- | :--- | :--- |
| `sensor.boardtogether_games_waiting_on_me` | Count of games currently waiting on you (turn, vote, or invitation), sorted longest-waiting first | `waiting_games`, `longest_wait_duration`, `longest_wait_game`, `longest_wait_url`, `markdown_table`, `webpush_connected`, `webpush_registered` |
| `sensor.boardtogether_longest_wait` | Duration (`min`) of the oldest game currently waiting on you (`0` when caught up) | `game_name`, `game_title`, `waiting_reason`, `waiting_duration`, `waiting_since`, `last_action_by`, `url` |
| `sensor.boardtogether_active_games` | Total count of active started games you are playing in | `active_games` (`name`, `title`, `is_my_turn`, `active_players`, `last_action_at`, `last_action_ago`, `last_action_by`, `url`) |
| `sensor.boardtogether_last_notification` | Summary of the last Web Push notification received | `received_at`, `action`, `title`, `message`, `game_id`, `game_name`, `game_title`, `url` |

---

## Dashboard Card (Lovelace Markdown Card)

Add a **Markdown Card** to any Home Assistant dashboard with the following content to see a live table of all games waiting on you, how long each has been waiting, who moved last, and a clickable link to open each game:

```yaml
type: markdown
title: 🎲 Games Waiting on Me ({{ states('sensor.boardtogether_games_waiting_on_me') }})
content: >
  {{ state_attr('sensor.boardtogether_games_waiting_on_me', 'markdown_table') }}
```

---

## Actions (Services)

Available in **Developer Tools → Actions** or in automations:
* `boardtogether.send_test_notification`: Sends a sample turn notification email (including your live waiting-games list) via your configured notification service.
* `boardtogether.send_waiting_games_digest`: Immediately refreshes your active games and sends a digest email of all games currently waiting on you and how long each has been waiting.
* `boardtogether.refresh_games`: Forces an immediate refresh of active games from `boardtogether.games`.
