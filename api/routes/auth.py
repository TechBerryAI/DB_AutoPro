"""Authentication API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth.jwt_handler import authenticate_user, create_tokens
from api.dependencies import get_current_user
from backend.models.schemas import TokenResponse, UserLogin, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin) -> TokenResponse:
    """Authenticate user and return JWT tokens."""
    user = authenticate_user(credentials.username, credentials.password)
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect username or password",
        )
    return create_tokens(user["username"], user["role"])


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> UserResponse:
    """Get current authenticated user info."""
    return UserResponse(
        username=current_user["username"],
        role=current_user["role"],
    )
