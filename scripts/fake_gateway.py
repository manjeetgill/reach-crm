"""Local synthetic communication engine for the end-to-end smoke test."""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

events = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/events":
            self.send_error(404)
            return
        self.respond({"count": len(events), "idempotency_keys": [event["idempotency_key"] for event in events]})

    def do_POST(self):
        if self.path != "/messages" or self.headers.get("Authorization") != "Bearer e2e-gateway-token":
            self.send_error(401)
            return
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if not all(payload.get(key) for key in ("organization_id", "contact_id", "conversation_id", "content", "idempotency_key")):
            self.send_error(422)
            return
        if not any(item["idempotency_key"] == payload["idempotency_key"] for item in events):
            events.append(payload)
        self.respond({"status": "sent", "external_id": "fake-" + payload["idempotency_key"][:30]})

    def respond(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
