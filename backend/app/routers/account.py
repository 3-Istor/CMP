import time
from typing import Annotated

import boto3
import jwt
import requests
from botocore.exceptions import ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.schemas.account import PictureUploadResponse, UserProfile

router = APIRouter(prefix="/account", tags=["Account"])
security = HTTPBearer(auto_error=False)  # Don't auto-error for local dev


# Mock user for local development
MOCK_USER = {
    "sub": "brian.perret",
    "email": "brian.perret@epita.fr",
    "given_name": "Brian",
    "family_name": "Perret",
    "name": "Brian Perret",
    "picture": None,
    "realm_access": {"roles": ["infra", "member"]},
}


async def get_current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(security)
    ] = None,
) -> dict:
    """
    Get current user from JWT token or return mock user in DEBUG mode.

    In production, Envoy Gateway injects the JWT in Authorization header.
    In local dev (DEBUG=True), if no JWT is present, return mock user.
    """
    # Try to get JWT token
    if credentials:
        token = credentials.credentials
        try:
            # Decode without verification (Envoy already validated it)
            payload = jwt.decode(
                token, options={"verify_signature": False, "verify_aud": False}
            )
            return payload
        except jwt.DecodeError as e:
            if settings.DEBUG:
                # In debug mode, fall back to mock user on decode error
                return MOCK_USER
            raise HTTPException(
                status_code=401, detail=f"Invalid token: {str(e)}"
            ) from e

    # No credentials provided
    if settings.DEBUG:
        # Local dev mode: return mock user
        return MOCK_USER

    # Production mode: require authentication
    raise HTTPException(
        status_code=401,
        detail="Authentication required. No JWT token provided.",
    )


@router.get("/me", response_model=UserProfile)
async def get_user_profile(
    token_payload: Annotated[dict, Depends(get_current_user)]
) -> UserProfile:
    """Get current user profile from JWT token or mock user in DEBUG mode."""
    # Extract roles from realm_access or resource_access
    roles = []
    if "realm_access" in token_payload:
        roles = token_payload["realm_access"].get("roles", [])

    return UserProfile(
        sub=token_payload.get("sub", ""),
        email=token_payload.get("email", ""),
        given_name=token_payload.get("given_name"),
        family_name=token_payload.get("family_name"),
        name=token_payload.get("name"),
        picture=token_payload.get("picture"),
        roles=roles,
    )


@router.post("/picture", response_model=PictureUploadResponse)
async def upload_profile_picture(
    file: UploadFile = File(...),
    token_payload: dict = Depends(get_current_user),
) -> PictureUploadResponse:
    """Upload a new profile picture to S3 and update Keycloak."""
    # Validate MIME type — only real image types accepted
    ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

    if not file.content_type or file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{file.content_type}'. Allowed: JPG, PNG, GIF, WEBP.",
        )

    # Get user sub from token
    user_sub = token_payload.get("sub")
    if not user_sub:
        raise HTTPException(
            status_code=400, detail="User sub not found in token"
        )

    # Read content and enforce size limit
    file_content = await file.read()
    if len(file_content) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(file_content) // 1024} KB). Maximum allowed size is 5 MB.",
        )

    # Generate filename: <sub_id>.jpg (overwrite previous avatar)
    filename = f"{user_sub}.jpg"

    # Upload to S3
    try:
        s3_client = boto3.client(
            "s3",
            endpoint_url=settings.AVATARS_S3_ENDPOINT,
            aws_access_key_id=settings.AVATARS_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AVATARS_S3_SECRET_ACCESS_KEY,
            region_name=settings.AVATARS_S3_REGION,
        )

        bucket_name = settings.AVATARS_S3_BUCKET
        # file_content already read above for size validation

        s3_client.put_object(
            Bucket=bucket_name,
            Key=filename,
            Body=file_content,
            ContentType=file.content_type,
        )

        # Generate public URL using configured base URL
        public_url = f"{settings.AVATARS_PUBLIC_URL_BASE.rstrip('/')}/{filename}"

    except ClientError as e:
        raise HTTPException(
            status_code=500, detail=f"S3 upload failed: {str(e)}"
        ) from e

    # Update Keycloak user profile (skip in DEBUG mode with mock user)
    if not settings.DEBUG:
        try:
            # Get admin token
            token_url = f"{settings.KEYCLOAK_URL}/realms/3istor/protocol/openid-connect/token"
            token_response = requests.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.KEYCLOAK_CLIENT_ID,
                    "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
                },
                timeout=10,
            )
            token_response.raise_for_status()
            admin_token = token_response.json()["access_token"]

            # Update user attributes
            user_url = (
                f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users/{user_sub}"
            )
            update_response = requests.put(
                user_url,
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json",
                },
                json={"attributes": {"picture": [public_url]}},
                timeout=10,
            )
            update_response.raise_for_status()

        except requests.RequestException as e:
            raise HTTPException(
                status_code=500, detail=f"Keycloak update failed: {str(e)}"
            ) from e

    if settings.DEBUG and user_sub == MOCK_USER["sub"]:
        MOCK_USER["picture"] = public_url

    return PictureUploadResponse(
        message="Profile picture updated successfully",
        picture_url=public_url,
    )
