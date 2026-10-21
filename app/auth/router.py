"""OAuth2 form login for Swagger and verified-user details."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.auth.security import CurrentUser, create_access_token, dummy_hash, get_current_user, verify_password
from app.auth.throttle import login_throttle
from app.db import users_col
from app.services.audit import audit_event

router = APIRouter(prefix="/auth", tags=["auth"])
LOGIN_ERROR = "Incorrect username or password"


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserResponse(BaseModel):
    id: str
    username: str


@router.post("/login", response_model=TokenResponse)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> TokenResponse:
    username = form.username.lower()
    async with login_throttle.attempt(username) as allowed:
        if not allowed:
            await audit_event("login_fail", outcome="throttled")
            raise HTTPException(429, LOGIN_ERROR, headers={"Retry-After": "900"})
        user = await users_col().find_one({"username": username})
        encoded = user["password_hash"] if user else dummy_hash()
        valid = await verify_password(form.password, encoded)
        if not user or not valid or user.get("status") != "active":
            login_throttle.fail(username)
            await audit_event("login_fail", outcome="denied")
            raise HTTPException(401, LOGIN_ERROR, headers={"WWW-Authenticate": "Bearer"})
        login_throttle.succeed(username)
        token = create_access_token(user["_id"])
        await audit_event("login_ok", user_id=user["_id"])
        return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> UserResponse:
    return UserResponse(id=user.user_id, username=user.username)
