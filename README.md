# Travel Bot for Slack

A Slack bot that takes a founder's travel booking from request to ticket without anyone leaving Slack or copying details between tools.

**Demo (30 sec):** [watch here](https://drive.google.com/drive/folders/16nL2EADtmZKRlL_tjHUdUiU_5HYugOAK?usp=sharing)

## The problem
Booking travel meant moving between Slack, airline sites and Linear: someone asks for a flight, someone searches, someone retypes the itinerary into a task, and later someone sends the ticket back. Each hand-off was manual and easy to get wrong.

## How it works
1. **Request:** type `/travel` in Slack to open a form (passenger, from, to, date, time preference). City names like "Hyderabad" are resolved to airport codes automatically.
2. **Search:** the bot queries the Duffel flights API and posts the options back to Slack, each with a **Select** button.
3. **Track:** choosing a flight creates a Linear issue with the passenger, airline, flight number, times, price and a booking link.
4. **Close the loop:** once the ticket PDF is attached to that Linear issue and it is moved to **Done**, a signed Linear webhook sends the PDF back to the Slack channel and person who asked.

## Stack
- Python, [Slack Bolt](https://slack.dev/bolt-python) in Socket Mode
- Duffel API (flight search), Linear GraphQL API (issues and webhooks)
- Flask for the webhook, with HMAC signature verification
- SQLite to map each Linear issue back to the Slack request

## Run it
```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your own keys
python app.py          # Slack bot
python webhook.py      # Linear -> Slack webhook, listens on :3000/linear-webhook
```

Environment variables (`.env`, never committed):

| Variable | What it is |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token from your Slack app |
| `SLACK_APP_TOKEN` | App-level token with `connections:write` (Socket Mode) |
| `DUFFEL_API_KEY` | Duffel API key (test keys return sandbox flights) |
| `LINEAR_API_KEY` | Linear personal API key |
| `LINEAR_WEBHOOK_SECRET` | Signing secret from the Linear webhook |
| `DONE_STATE_NAME` | Optional; Linear state that triggers delivery (default `Done`) |
| `WEBHOOK_PORT` | Optional; webhook port (default `3000`) |

The webhook needs a public URL; a Cloudflare Tunnel works for local testing.

## Status
A working prototype on Duffel's test environment. It links out to the airline to book rather than issuing tickets itself.

Built by [Manas Gupta](https://www.linkedin.com/in/manas-garudapalli-93512a216/) with AI tools.
