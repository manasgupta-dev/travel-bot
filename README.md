# Travel Bot Phase 1

A minimal Slack bot using Python, slack-bolt, and Socket Mode. Type a flight request in Slack, get live flight options, and pick one to auto-create a task in Linear with the itinerary and a booking link.

## Setup

1. Install dependencies:
```bash
   pip install -r requirements.txt
```
2. Add your tokens to `.env`:
```text
   SLACK_BOT_TOKEN=
   SLACK_APP_TOKEN=
   LINEAR_API_KEY=
   LINEAR_TEAM_ID=
```
   - `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` — from your Slack app (App-Level Token needs `connections:write` for Socket Mode).
   - `LINEAR_API_KEY` — a personal API key from Linear (Settings → API → Personal API keys).
   - `LINEAR_TEAM_ID` — the team where new travel tasks are created.

## Run

```bash
python app.py
```

Use the `/travel` command in Slack to test.
