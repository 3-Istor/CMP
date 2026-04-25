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
security = HTTPBearer(auto_error=True)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict:
    """
    Get current user from JWT token.

    In production, Envoy Gateway injects the JWT in Authorization header.
    """
    token = credentials.credentials
    try:
        # Decode without verification (Envoy already validated it)
        payload = jwt.decode(
            token, options={"verify_signature": False, "verify_aud": False}
        )
        return payload
    except jwt.DecodeError as e:
        raise HTTPException(
            status_code=401, detail=f"Invalid token: {str(e)}"
        ) from e


@router.get("/me", response_model=UserProfile)
async def get_user_profile(
    request: Request,
    token_payload: Annotated[dict, Depends(get_current_user)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> UserProfile:
    """
    Get current user profile with fresh data from Keycloak.

    This ensures we always return the latest profile picture and other attributes,
    even if the JWT contains stale claims.
    """
    # Extract groups from token (Keycloak uses 'groups' field)
    groups = token_payload.get("groups", [])

    # Fallback to roles if groups not available
    if not groups and "realm_access" in token_payload:
        groups = token_payload["realm_access"].get("roles", [])

    # Get picture from token as fallback
    picture = token_payload.get("picture")

    # Try to fetch fresh picture from Keycloak Admin API
    # Use preferred_username which is the actual username in Keycloak
    user_sub = token_payload.get("preferred_username") or token_payload.get(
        "sub", ""
    )
    user_uuid = None  # Will store the actual UUID
    print(f"DEBUG /me: Looking up user: {user_sub}")

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
        if token_response.ok:
            admin_token = token_response.json()["access_token"]

            # Search for user by username to get UUID
            search_url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users"
            search_response = requests.get(
                search_url,
                headers={"Authorization": f"Bearer {admin_token}"},
                params={"username": user_sub, "exact": "true"},
                timeout=10,
            )

            if search_response.ok:
                users = search_response.json()
                if users:
                    user_uuid = users[0]["id"]
                    print(f"DEBUG /me: Found user UUID: {user_uuid}")

                    # Get user details including attributes
                    user_url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users/{user_uuid}"
                    user_response = requests.get(
                        user_url,
                        headers={"Authorization": f"Bearer {admin_token}"},
                        timeout=10,
                    )

                    if user_response.ok:
                        user_data = user_response.json()
                        print(
                            f"DEBUG /me: User data attributes: {user_data.get('attributes', {})}"
                        )
                        # Extract picture from attributes
                        if (
                            "attributes" in user_data
                            and "picture" in user_data["attributes"]
                        ):
                            picture_list = user_data["attributes"]["picture"]
                            if picture_list and len(picture_list) > 0:
                                picture = picture_list[0]
                                print(
                                    f"DEBUG /me: Retrieved picture from Keycloak: {picture}"
                                )
                        else:
                            print(
                                f"DEBUG /me: No picture attribute found in user data"
                            )
                    else:
                        print(
                            f"DEBUG /me: Failed to get user details: {user_response.status_code}"
                        )
                else:
                    print(
                        f"DEBUG /me: No users found for username: {user_sub}"
                    )
            else:
                print(
                    f"DEBUG /me: Search failed: {search_response.status_code}"
                )
        else:
            print(
                f"DEBUG /me: Failed to get admin token: {token_response.status_code}"
            )
    except requests.RequestException as e:
        # Log error but don't fail the request - use JWT picture as fallback
        print(f"Warning /me: Failed to fetch user data from Keycloak: {e}")

    print(f"DEBUG /me: Returning picture: {picture}")

    return UserProfile(
        sub=user_uuid
        or token_payload.get(
            "sub", ""
        ),  # Use UUID if found, otherwise fallback to token sub
        email=token_payload.get("email", ""),
        given_name=token_payload.get("given_name"),
        family_name=token_payload.get("family_name"),
        name=token_payload.get("name"),
        picture=picture,
        groups=groups,
    )


@router.post("/picture", response_model=PictureUploadResponse)
async def upload_profile_picture(
    file: UploadFile = File(...),
    token_payload: dict = Depends(get_current_user),
) -> PictureUploadResponse:
    """Upload a new profile picture to S3 and update Keycloak."""
    # Debug: Log the token payload to see what's available
    print(f"DEBUG: Token payload keys: {token_payload.keys()}")
    print(f"DEBUG: Token payload: {token_payload}")

    # Validate MIME type — only real image types accepted
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    max_size_bytes = 5 * 1024 * 1024  # 5 MB

    if not file.content_type or file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid file type '{file.content_type}'. "
                "Allowed: JPG, PNG, GIF, WEBP."
            ),
        )

    # Get user sub from token - try multiple possible field names
    user_sub = (
        token_payload.get("sub")
        or token_payload.get("preferred_username")
        or token_payload.get("username")
        or token_payload.get("user_id")
    )

    if not user_sub:
        raise HTTPException(
            status_code=400,
            detail=(
                "User sub not found in token. Available fields: "
                f"{list(token_payload.keys())}"
            ),
        )

    # Read content and enforce size limit
    file_content = await file.read()
    if len(file_content) > max_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File too large ({len(file_content) // 1024} KB). "
                "Maximum allowed size is 5 MB."
            ),
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
        public_url = (
            f"{settings.AVATARS_PUBLIC_URL_BASE.rstrip('/')}/{filename}"
        )

    except ClientError as e:
        raise HTTPException(
            status_code=500, detail=f"S3 upload failed: {str(e)}"
        ) from e

    # Update Keycloak user profile
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

        # The 'sub' claim might be a username, not a UUID
        # First, try to find the user by username to get their UUID
        search_url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users"
        search_response = requests.get(
            search_url,
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"username": user_sub, "exact": "true"},
            timeout=10,
        )
        search_response.raise_for_status()
        users = search_response.json()

        if not users:
            # If not found by username, assume user_sub is already a UUID
            user_uuid = user_sub
        else:
            # Get the UUID from the first matching user
            user_uuid = users[0]["id"]

        print(f"DEBUG: Using user UUID: {user_uuid} for username: {user_sub}")

        # First, GET the current user data to avoid overwriting other fields
        user_url = (
            f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users/{user_uuid}"
        )
        get_response = requests.get(
            user_url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        get_response.raise_for_status()
        user_data = get_response.json()

        print(f"DEBUG: Current user data keys: {user_data.keys()}")
        print(f"DEBUG: Current attributes: {user_data.get('attributes', {})}")

        # Update only the picture attribute, preserving all other attributes
        if "attributes" not in user_data:
            user_data["attributes"] = {}
        user_data["attributes"]["picture"] = [public_url]

        # Remove read-only fields that can't be updated via PUT
        fields_to_remove = [
            "access",
            "createdTimestamp",
            "disableableCredentialTypes",
            "notBefore",
            "totp",
            "federatedIdentities",
            "federationLink",
            "serviceAccountClientId",
            "origin",
        ]
        for field in fields_to_remove:
            user_data.pop(field, None)

        print(
            f"DEBUG: Updating user with attributes: {user_data.get('attributes', {})}"
        )

        # Now PUT the complete user data back with the updated picture
        update_response = requests.put(
            user_url,
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
            json=user_data,
            timeout=10,
        )
        update_response.raise_for_status()

        print(
            f"DEBUG: Successfully updated Keycloak user {user_uuid} with picture: {public_url}"
        )

        # Verify the update by fetching the user again
        verify_response = requests.get(
            user_url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        if verify_response.ok:
            verify_data = verify_response.json()
            stored_picture = verify_data.get("attributes", {}).get(
                "picture", []
            )
            print(f"DEBUG: Verified picture in Keycloak: {stored_picture}")

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500, detail=f"Keycloak update failed: {str(e)}"
        ) from e

    return PictureUploadResponse(
        message="Profile picture updated successfully",
        picture_url=public_url,
    )
