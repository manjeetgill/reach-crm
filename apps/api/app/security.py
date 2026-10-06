from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

passwords = PasswordHash.recommended()
bearer = HTTPBearer()
SECRET = os.getenv("JWT_SECRET", "dev-only-insecure-secret-change-before-deployment")

def token_for(user: User) -> str:
    return jwt.encode({"sub": user.id, "org": user.organization_id, "kind": "staff", "exp": datetime.now(timezone.utc) + timedelta(hours=8)}, SECRET, algorithm="HS256")

def web_token_for(organization_id: str, contact_id: str, conversation_id: str) -> str:
    return jwt.encode({"sub": contact_id, "org": organization_id, "conversation": conversation_id, "kind": "web_chat", "exp": datetime.now(timezone.utc) + timedelta(hours=24)}, SECRET, algorithm="HS256")

def web_chat_claims(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    try:
        claims = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
        if claims.get("kind") == "web_chat" and all(claims.get(key) for key in ("sub", "org", "conversation")):
            return claims
    except jwt.PyJWTError:
        pass
    raise HTTPException(401, "Invalid web chat token")

def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)) -> User:
    try:
        claims = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
        if claims.get("kind") != "staff":
            raise HTTPException(401, "Invalid credentials")
        user = db.scalar(select(User).where(User.id == claims["sub"], User.organization_id == claims["org"], User.status == "active"))
        if user:
            return user
    except (jwt.PyJWTError, KeyError):
        pass
    raise HTTPException(401, "Invalid credentials")

def require(*roles: str):
    def guard(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return guard
