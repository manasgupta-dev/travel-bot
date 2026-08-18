"""Linear -> Slack confirmation webhook.

Flow:
  1. You attach the ticket PDF to the Linear issue.
  2. You move the issue Backlog -> Done.
  3. Linear fires an "Issue update" webhook here.
  4. We confirm the state is Done, pull the PDF off the issue, and post it
     back to the Slack channel + user who ran /travel.

Reads the SAME sqlite DB that app.py writes to (linear_store.db), looked up
by the Linear issue id.

Run alongside app.py (separate process):
    python webhook.py            # listens on :3000/linear-webhook

New .env vars:
    LINEAR_WEBHOOK_SECRET     signing secret shown when you create the webhook
    DONE_STATE_NAME           optional, defaults to "Done"
"""

import hashlib
import hmac
import os
import re
import sqlite3
import tempfile

import requests
from dotenv import load_dotenv
from flask import Flask, request, abort
from slack_sdk import WebClient

load_dotenv()

LINEAR_API_KEY = os.environ["LINEAR_API_KEY"]
LINEAR_WEBHOOK_SECRET = os.environ["LINEAR_WEBHOOK_SECRET"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
DONE_STATE_NAME = os.getenv("DONE_STATE_NAME", "Done")

slack = WebClient(token=SLACK_BOT_TOKEN)
app = Flask(__name__)

LINEAR_GQL = "https://api.linear.app/graphql"
DB_CONN = sqlite3.connect("linear_store.db", check_same_thread=False)
_PDF_RE = re.compile(r"(https://uploads\.linear\.app/[^\s)\"']+)", re.IGNORECASE)


def _verify(raw_body, signature):
    expected = hmac.new(
        LINEAR_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _lookup_origin(linear_issue_id):
    row = DB_CONN.execute(
        "SELECT slack_channel, slack_user, issue_title "
        "FROM linear_issue_mappings WHERE linear_issue_id = ?",
        (linear_issue_id,),
    ).fetchone()
    if not row:
        return None
    return {"channel": row[0], "user": row[1], "title": row[2]}


def _fetch_pdf_url(issue_id):
    query = """
    query($id: String!) {
      issue(id: $id) {
        description
        attachments { nodes { url title } }
      }
    }"""
    r = requests.post(
        LINEAR_GQL,
        json={"query": query, "variables": {"id": issue_id}},
        headers={"Authorization": LINEAR_API_KEY},
        timeout=15,
    )
    r.raise_for_status()
    issue = r.json()["data"]["issue"]
    for node in issue.get("attachments", {}).get("nodes", []):
        url = node.get("url", "")
        if url.lower().endswith(".pdf") or "uploads.linear.app" in url:
            return url
    for match in _PDF_RE.findall(issue.get("description") or ""):
        if match.lower().endswith(".pdf") or "uploads.linear.app" in match:
            return match
    return None


def _download(url):
    r = requests.get(url, headers={"Authorization": LINEAR_API_KEY}, timeout=30)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path


@app.post("/linear-webhook")
def linear_webhook():
    raw = request.get_data()
    if not _verify(raw, request.headers.get("Linear-Signature")):
        abort(401)

    payload = request.get_json(silent=True) or {}
    if payload.get("type") != "Issue" or payload.get("action") != "update":
        return "", 200

    data = payload.get("data", {})
    state_name = (data.get("state") or {}).get("name")
    updated_from = payload.get("updatedFrom") or {}
    changed_state = "stateId" in updated_from or "state" in updated_from
    if state_name != DONE_STATE_NAME or not changed_state:
        return "", 200

    issue_id = data.get("id")
    origin = _lookup_origin(issue_id)
    if not origin:
        return "", 200  # not one of our /travel issues

    title = origin["title"] or data.get("title", "your flight")
    pdf_url = _fetch_pdf_url(issue_id)

    if not pdf_url:
        slack.chat_postMessage(
            channel=origin["channel"],
            text=f":white_check_mark: <@{origin['user']}> *{title}* is confirmed, "
                 f"but I couldn't find a PDF attached to the issue.",
        )
        return "", 200

    path = _download(pdf_url)
    try:
        slack.files_upload_v2(
            channel=origin["channel"],
            file=path,
            filename="ticket.pdf",
            initial_comment=f":white_check_mark: <@{origin['user']}> your booking is "
                            f"confirmed — *{title}*. Ticket attached below.",
        )
    finally:
        os.remove(path)
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("WEBHOOK_PORT", "3000")))