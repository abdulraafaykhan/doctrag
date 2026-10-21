"""Password hashing, access tokens and verified identity."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import users_col

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class CurrentUser(BaseModel):
    user_id: str
    username: str
    customer_id: str


# Precompute once so the first unknown username does not pay for an extra hash.
_DUMMY_HASH = password_hash.hash("fictional-dummy-password-never-used-for-login")


def dummy_hash() -> str:
    return _DUMMY_HASH


def hash_password(password: str) -> str:
    return password_hash.hash(password)


async def verify_password(password: str, encoded: str) -> bool:
    try:
        return await run_in_threadpool(password_hash.verify, password, encoded)
    except Exception:
        return False


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "iat": now,
                       "exp": now + timedelta(minutes=settings.access_token_minutes)},
                      settings.jwt_secret.get_secret_value(), algorithm="HS256")


def unauthorized() -> HTTPException:
    return HTTPException(401, "Could not validate credentials", headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> CurrentUser:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret.get_secret_value(), algorithms=["HS256"],
                             options={"require": ["sub", "exp", "iat"]})
        user_id = payload["sub"]
        if not isinstance(user_id, str) or not user_id:
            raise unauthorized()
    except jwt.InvalidTokenError:
        raise unauthorized() from None
    user = await users_col().find_one({"_id": user_id})
    if not user or user.get("status") != "active":
        raise unauthorized()
    return CurrentUser(user_id=user["_id"], username=user["username"], customer_id=user["customer_id"])
