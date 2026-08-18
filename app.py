import json
import os
import re
import sqlite3
import traceback
from datetime import datetime

import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
DUFFEL_API_KEY = os.environ.get("DUFFEL_API_KEY")
LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY")
LINEAR_TEAM_ID = None

app = App(token=SLACK_BOT_TOKEN)

DUFFEL_API_URL = "https://api.duffel.com/air/offer_requests?return_offers=true"
LINEAR_API_URL = "https://api.linear.app/graphql"
DB_CONN = sqlite3.connect("linear_store.db", check_same_thread=False)
DB_CONN.execute(
    """CREATE TABLE IF NOT EXISTS linear_issue_mappings (
         offer_id TEXT PRIMARY KEY,
         linear_issue_id TEXT,
         issue_title TEXT,
         created_at TEXT,
         slack_channel TEXT,
         slack_user TEXT
    )"""
)
# add the new columns to any pre-existing DB
for _col in ("slack_channel", "slack_user"):
    try:
        DB_CONN.execute(f"ALTER TABLE linear_issue_mappings ADD COLUMN {_col} TEXT")
    except sqlite3.OperationalError:
        pass
DB_CONN.commit()


def store_linear_issue_mapping(offer_id, linear_issue_id, issue_title, slack_channel=None, slack_user=None):
    DB_CONN.execute(
        "INSERT OR REPLACE INTO linear_issue_mappings "
        "(offer_id, linear_issue_id, issue_title, created_at, slack_channel, slack_user) "
        "VALUES (?, ?, ?, datetime('now'), ?, ?)",
        (offer_id, linear_issue_id, issue_title, slack_channel, slack_user),
    )
    DB_CONN.commit()


def linear_graphql(query, variables=None):
    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    response = requests.post(LINEAR_API_URL, headers=headers, json=payload, timeout=30)
    print("LINEAR status:", response.status_code)
    print("LINEAR body:", response.text)
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise Exception(f"Linear GraphQL errors: {data['errors']}")
    return data.get("data", {})


def get_linear_team_id():
    global LINEAR_TEAM_ID
    if LINEAR_TEAM_ID:
        return LINEAR_TEAM_ID
    data = linear_graphql("{ teams(first: 1) { nodes { id } } }")
    nodes = data.get("teams", {}).get("nodes", [])
    if not nodes:
        raise Exception("No Linear teams found")
    LINEAR_TEAM_ID = nodes[0].get("id")
    return LINEAR_TEAM_ID


def create_linear_issue(title, description, team_id):
    mutation = """
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue {
          id
          title
          url
        }
      }
    }
    """
    variables = {"input": {"teamId": team_id, "title": title, "description": description}}
    data = linear_graphql(mutation, variables)
    issue_create = data.get("issueCreate", {})
    if not issue_create.get("success") or not issue_create.get("issue"):
        raise Exception(f"Linear issueCreate failed: {issue_create}")
    return issue_create["issue"]

SEARCH_MODAL = {
    "type": "modal",
    "callback_id": "travel_modal",
    "title": {"type": "plain_text", "text": "Book a flight"},
    "submit": {"type": "plain_text", "text": "Search"},
    "close": {"type": "plain_text", "text": "Cancel"},
    "blocks": [
        {
            "type": "input",
            "block_id": "passenger_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "passenger_name",
                "placeholder": {"type": "plain_text", "text": "e.g. Jane Doe"},
            },
            "label": {"type": "plain_text", "text": "Passenger name (who is flying)", "emoji": True},
        },
        {
            "type": "input",
            "block_id": "from_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "from_location",
                "placeholder": {"type": "plain_text", "text": "City or airport, e.g. San Francisco or SFO"},
            },
            "label": {"type": "plain_text", "text": "From (airport or city)", "emoji": True},
        },
        {
            "type": "input",
            "block_id": "to_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "to_location",
                "placeholder": {"type": "plain_text", "text": "City or airport, e.g. San Francisco or SFO"},
            },
            "label": {"type": "plain_text", "text": "To (airport or city)", "emoji": True},
        },
        {
            "type": "input",
            "block_id": "date_block",
            "element": {
                "type": "datepicker",
                "action_id": "travel_date",
                "placeholder": {"type": "plain_text", "text": "Select a date"},
            },
            "label": {"type": "plain_text", "text": "Date", "emoji": True},
        },
        {
            "type": "input",
            "block_id": "time_block",
            "element": {
                "type": "static_select",
                "action_id": "time_preference",
                "placeholder": {"type": "plain_text", "text": "Choose a time"},
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Morning"},
                        "value": "Morning",
                    },
                    {
                        "text": {"type": "plain_text", "text": "Afternoon"},
                        "value": "Afternoon",
                    },
                    {
                        "text": {"type": "plain_text", "text": "Evening"},
                        "value": "Evening",
                    },
                    {
                        "text": {"type": "plain_text", "text": "Anytime"},
                        "value": "Anytime",
                    },
                ],
            },
            "label": {"type": "plain_text", "text": "Time preference", "emoji": True},
        },
    ],
}


def search_duffel_offers(origin, destination, departure_date):
    headers = {
        "Authorization": f"Bearer {DUFFEL_API_KEY}",
        "Duffel-Version": "v2",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "data": {
            "slices": [
                {
                    "origin": origin,
                    "destination": destination,
                    "departure_date": departure_date,
                }
            ],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy",
        }
    }
    response = requests.post(DUFFEL_API_URL, headers=headers, json=payload, timeout=30)
    print("DUFFEL status:", response.status_code)
    print("DUFFEL body:", response.text)
    open("duffel_debug.txt", "w").write(
        f"STATUS: {response.status_code}\n\nBODY:\n{response.text}"
    )
    response.raise_for_status()

    body = response.json()
    data = body.get("data", {})
    print("DATA keys:", list(data.keys()))
    print("RAW offer count:", len(data.get("offers", [])))
    offers = data.get("offers", [])
    return offers


def get_safe(value, default=""):
    return value if value is not None else default


def get_duffel_headers(content_type=True):
    headers = {
        "Authorization": f"Bearer {DUFFEL_API_KEY}",
        "Duffel-Version": "v2",
        "Accept": "application/json",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def normalize_iata_input(text):
    if not text:
        return None
    normalized = text.strip()
    if re.fullmatch(r"[A-Za-z]{3}", normalized):
        return normalized.upper()
    return None


def resolve_location_to_iata(location_text):
    code = normalize_iata_input(location_text)
    if code:
        return code

    url = "https://api.duffel.com/places/suggestions"
    params = {"query": location_text}
    response = requests.get(url, headers=get_duffel_headers(content_type=False), params=params, timeout=30)
    print("DUFFEL suggestions status:", response.status_code)
    print("DUFFEL suggestions body:", response.text)
    response.raise_for_status()
    body = response.json()
    for place in body.get("data", []):
        iata_code = place.get("iata_code")
        if iata_code:
            return iata_code
    return None


def format_location_display(original_text, resolved_code):
    if not resolved_code:
        return original_text or ""
    if not original_text:
        return resolved_code
    original = original_text.strip()
    if re.fullmatch(r"[A-Za-z]{3}", original) and original.upper() == resolved_code.upper():
        return resolved_code
    if original.upper() == resolved_code.upper():
        return resolved_code
    return f"{original} ({resolved_code})"


def build_offer_blocks(offer, passenger_name=""):
    owner = offer.get("owner", {})
    airline_name = get_safe(owner.get("name"), owner.get("iata_code", "United Airlines"))
    offer_id = offer.get("id", "")
    total_amount = get_safe(offer.get("total_amount"), "")
    total_currency = get_safe(offer.get("total_currency"), "")
    price_text = f"{total_amount} {total_currency}" if total_amount and total_currency else "Price unavailable"

    slices = offer.get("slices", [])
    slice_data = slices[0] if slices else {}
    origin_code = slice_data.get("origin", {}).get("iata_code") or slice_data.get("origin", {}).get("name", "")
    destination_code = slice_data.get("destination", {}).get("iata_code") or slice_data.get("destination", {}).get("name", "")
    departure_date = slice_data.get("departure_date", "")
    flight_label = airline_name
    departure_text = ""
    arrival_text = ""
    stops_text = ""

    if slices:
        first_slice = slices[0]
        segments = first_slice.get("segments", [])
        if segments:
            first_segment = segments[0]
            last_segment = segments[-1]
            operating = first_segment.get("operating_carrier", {})
            operating_code = operating.get("iata_code", "")
            operating_flight_number = first_segment.get("operating_carrier_flight_number", "")
            flight_number = (
                f"{operating_code} {operating_flight_number}".strip()
                or first_segment.get("marketing_flight_number")
                or first_segment.get("flight_number", "")
            )
            flight_label = f"{airline_name} {flight_number}".strip()
            departure_text = first_segment.get("departing_at", "")
            arrival_text = last_segment.get("arriving_at", "")
            stops = max(0, len(segments) - 1)
            if stops == 0:
                stops_text = "Non-stop"
            elif stops == 1:
                stops_text = "1 stop"
            else:
                stops_text = f"{stops} stops"
            try:
                dep = datetime.fromisoformat(departure_text.replace("Z", "+00:00"))
                arr = datetime.fromisoformat(arrival_text.replace("Z", "+00:00"))
                departure_text = dep.strftime("%-I:%M %p")
                arrival_text = arr.strftime("%-I:%M %p")
            except Exception:
                departure_text = departure_text.replace("T", " ")
                arrival_text = arrival_text.replace("T", " ")
    else:
        flight_number = ""

    flight_summary = f"{flight_label}"
    if departure_text and arrival_text:
        flight_summary += f" — {departure_text} → {arrival_text}"

    button_value = json.dumps({
        "offer_id": offer_id,
        "flight": flight_summary,
        "price": price_text,
        "airline": airline_name,
        "flight_number": flight_number,
        "origin": origin_code,
        "destination": destination_code,
        "date": departure_date,
        "departure": departure_text,
        "arrival": arrival_text,
        "stops": stops_text,
        "passenger_name": passenger_name,
    })

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{flight_summary}*\n{stops_text}\n*Price:* {price_text}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Select"},
                    "action_id": "select_offer",
                    "value": button_value,
                }
            ],
        },
        {"type": "divider"},
    ]


@app.command("/travel")
def handle_travel_command(ack, body, client):
    ack()
    modal = {**SEARCH_MODAL, "private_metadata": body["channel_id"]}
    client.views_open(
        trigger_id=body["trigger_id"],
        view=modal,
    )


@app.view("travel_modal")
def handle_travel_modal_submission(ack, body, client):
    ack()

    values = body["view"]["state"]["values"]
    passenger_name = values["passenger_block"]["passenger_name"]["value"]
    from_location = values["from_block"]["from_location"]["value"]
    to_location = values["to_block"]["to_location"]["value"]
    travel_date = values["date_block"]["travel_date"]["selected_date"]
    time_preference = values["time_block"]["time_preference"]["selected_option"]["value"]
    channel_id = body["view"]["private_metadata"]

    resolved_origin = resolve_location_to_iata(from_location)
    resolved_destination = resolve_location_to_iata(to_location)

    if not resolved_origin:
        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"Couldn't find an airport for '{from_location}' — try a nearby major city or the airport code."
            ),
        )
        return

    if not resolved_destination:
        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"Couldn't find an airport for '{to_location}' — try a nearby major city or the airport code."
            ),
        )
        return

    from_display = format_location_display(from_location, resolved_origin)
    to_display = format_location_display(to_location, resolved_destination)
    search_text = (
        f"Got it — searching flights for {passenger_name} from {from_display} to {to_display} "
        f"on {travel_date}, {time_preference}."
    )
    client.chat_postMessage(channel=channel_id, text=search_text)

    try:
        offers = search_duffel_offers(resolved_origin, resolved_destination, travel_date)
        print("OFFER owners:", [o.get("owner", {}).get("iata_code") for o in offers])
        united_offers = [
            offer for offer in offers if offer.get("owner", {}).get("iata_code") == "UA"
        ]

        if united_offers:
            offer_list = united_offers
            header_text = "Here are the cheapest United offers:"
        else:
            offer_list = offers
            header_text = "No United flights in test data — showing available options."

        offer_list.sort(key=lambda offer: float(offer.get("total_amount") or 0))
        selected_offers = offer_list[:5]
        blocks = []
        for offer in selected_offers:
            blocks.extend(build_offer_blocks(offer, passenger_name))

        client.chat_postMessage(
            channel=channel_id,
            text=header_text,
            blocks=blocks,
        )
    except requests.RequestException as e:
        print("SEARCH ERROR:", repr(e))
        traceback.print_exc()
        client.chat_postMessage(
            channel=channel_id,
            text="Could not search flights right now — please try again later.",
        )


@app.action("select_offer")
def handle_select_offer(ack, body, client):
    ack()

    action = body.get("actions", [])[0]
    channel_id = body.get("channel", {}).get("id") or body.get("container", {}).get("channel_id")
    try:
        payload = json.loads(action.get("value", "{}"))
        offer_id = payload.get("offer_id", "")
        airline = payload.get("airline", "")
        flight_number = payload.get("flight_number", "")
        origin = payload.get("origin", "")
        destination = payload.get("destination", "")
        date = payload.get("date", "")
        departure = payload.get("departure", "")
        arrival = payload.get("arrival", "")
        stops = payload.get("stops", "")
        price = payload.get("price", "price unavailable")
        passenger_name = payload.get("passenger_name", "")
        issue_title = f"✈️ {passenger_name}: {origin} → {destination} · {date}"
        issue_description = (
            f"*Passenger:* {passenger_name}\n"
            f"*Airline:* {airline}\n"
            f"*Flight:* {flight_number}\n"
            f"*Departure:* {departure}\n"
            f"*Arrival:* {arrival}\n"
            f"*Stops:* {stops}\n"
            f"*Price:* {price}\n\n"
            "Confirmation #: (pending)"
        )

        team_id = get_linear_team_id()
        issue = create_linear_issue(issue_title, issue_description, team_id)
        created_issue_id = issue.get("id")
        created_issue_title = issue.get("title", issue_title)
        selecting_user = body.get("user", {}).get("id")
        store_linear_issue_mapping(
            offer_id, created_issue_id, created_issue_title,
            slack_channel=channel_id, slack_user=selecting_user,
        )

        united_link = f"https://www.united.com/en/us/book-flight/united-one-way?f={origin}&t={destination}&d={date}"
        if channel_id:
            client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"✅ Added to Linear: {created_issue_title}\n"
                    f"Book on United: {united_link}"
                ),
            )
    except Exception as e:
        print("LINEAR ERROR:", repr(e))
        traceback.print_exc()
        if channel_id:
            client.chat_postMessage(
                channel=channel_id,
                text="Couldn't create the Linear task — check the logs.",
            )


if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()