"""Channel-neutral outbound integration contract.

Engine contract: POST {base_url}/messages with JSON payload containing
organization_id, contact_id, conversation_id, content, and idempotency_key.
Return JSON with status (queued/sent/delivered) and optional external_id.
"""
from __future__ import annotations

import os
from typing import Protocol

import httpx
from fastapi import HTTPException


class CommunicationAdapter(Protocol):
    def send_text(self, *, organization_id: str, contact_id: str, conversation_id: str, content: str, idempotency_key: str) -> dict: ...


class WebAdapter:
    def send_text(self, **kwargs) -> dict:
        return {"status": "sent"}


class HttpEngineAdapter:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def send_text(self, **kwargs) -> dict:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{self.base_url}/messages",
                    json=kwargs,
                    headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": kwargs["idempotency_key"]},
                )
                response.raise_for_status()
                result = response.json()
                if result.get("status") not in {"queued", "sent", "delivered"}:
                    raise ValueError("Invalid engine response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Communication engine failed") from exc


class CommunicationGateway:
    def adapter(self, channel: str) -> CommunicationAdapter:
        if channel == "web":
            return WebAdapter()
        env_name = {"whatsapp": "WHATSAPP_ENGINE_URL", "voice": "VOICE_ENGINE_URL"}.get(channel)
        url = os.getenv(env_name, "") if env_name else ""
        token = os.getenv("ENGINE_API_TOKEN", "")
        if not url or not token:
            raise HTTPException(503, "External engine adapter is not configured")
        return HttpEngineAdapter(url, token)

    def send_text(self, channel: str, **kwargs) -> dict:
        return self.adapter(channel).send_text(**kwargs)


gateway = CommunicationGateway()
