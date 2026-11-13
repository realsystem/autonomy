#!/usr/bin/env python3
"""Lab only: make a fresh, real Home Assistant usable from scripts. Creates the first user through
the onboarding API, exchanges the code for an access token, and asks for a long-lived token over
the WebSocket API. Writes the long-lived token to OUT. Standard library plus autonomy_core.

  setup.py <ha-base-url> <out-file>
"""

import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from autonomy_core.wsclient import WebSocket

CLIENT = "http://lab.invalid/"


def call(url, data=None, form=False, token=None):
    body = None
    headers = {}
    if data is not None:
        body = (
            urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        )
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(
        urllib.request.Request(url, body, headers), timeout=30
    ) as r:
        return json.loads(r.read().decode() or "{}")


def main(base, out):
    for _ in range(60):  # the HTTP server is up before onboarding is ready
        try:
            steps = call(base + "/api/onboarding")
            break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(2)
    else:
        sys.exit("setup: Home Assistant did not answer /api/onboarding")
    if all(s.get("done") for s in steps):
        sys.exit(
            "setup: Home Assistant is already onboarded but the lab has no token: ops/lab/lab.sh reset"
        )
    code = call(
        base + "/api/onboarding/users",
        {
            "client_id": CLIENT,
            "name": "Lab",
            "username": "lab",
            "password": secrets.token_urlsafe(24),
            "language": "en",
        },
    )["auth_code"]
    access = call(
        base + "/auth/token",
        {"grant_type": "authorization_code", "code": code, "client_id": CLIENT},
        form=True,
    )["access_token"]
    ws = WebSocket(base.replace("http", "ws", 1) + "/api/websocket")
    json.loads(ws.recv_text(10))  # auth_required
    ws.send_text(json.dumps({"type": "auth", "access_token": access}))
    if json.loads(ws.recv_text(10)).get("type") != "auth_ok":
        sys.exit("setup: Home Assistant refused the new token")
    ws.send_text(
        json.dumps(
            {
                "id": 1,
                "type": "auth/long_lived_access_token",
                "client_name": "autonomy-lab",
                "lifespan": 365,
            }
        )
    )
    reply = json.loads(ws.recv_text(10))
    ws.close()
    if not reply.get("success"):
        sys.exit(f"setup: no long-lived token: {reply}")
    with open(out, "w", encoding="utf-8") as f:
        f.write(reply["result"])
    print("setup: token written")


if __name__ == "__main__":
    main(sys.argv[1].rstrip("/"), sys.argv[2])
