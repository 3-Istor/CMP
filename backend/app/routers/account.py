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
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user_github import UserGitHubInstallation
from app.schemas.account import (
    GitHubInstallationRequest,
    GitHubInstallationResponse,
    PictureUploadResponse,
    UserProfile,
)

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
            token, options={"verify_signature": False, "verify_aud": False, "verify_exp": False}
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
    db: Session = Depends(get_db),
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
    github_installation_id = None

    # Initialize fallback variables for name from Keycloak Admin API
    keycloak_first_name = None
    keycloak_last_name = None

    # Initialize variables
    user_sub = token_payload.get("preferred_username") or token_payload.get("sub", "")
    user_uuid = None
    keycloak_first_name = None
    keycloak_last_name = None
    github_installation_id = None

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

            search_url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users"
            search_response = requests.get(
                search_url,
                headers={"Authorization": f"Bearer {admin_token}"},
                params={"username": user_sub, "exact": "true"},
                timeout=10,
            )

            if search_response.ok and search_response.json():
                user_uuid = search_response.json()[0]["id"]

                user_url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users/{user_uuid}"
                user_response = requests.get(
                    user_url,
                    headers={"Authorization": f"Bearer {admin_token}"},
                    timeout=10,
                )

                if user_response.ok:
                    user_data = user_response.json()
                    keycloak_first_name = user_data.get("firstName")
                    keycloak_last_name = user_data.get("lastName")

                    attributes = user_data.get("attributes", {})
                    if "picture" in attributes and attributes["picture"]:
                        picture = attributes["picture"][0]

    except Exception as e:
        print(f"Warning /me: Failed to fetch user data: {e}")

    # Récupérer l'ID d'installation GitHub depuis la base de données
    if user_sub:
        github_record = db.query(UserGitHubInstallation).filter(
            UserGitHubInstallation.user_sub == user_sub
        ).first()
        if github_record:
            github_installation_id = github_record.installation_id

    # Construire le nom complet en évitant les "null null"
    computed_name = token_payload.get("name")
    if not computed_name and (keycloak_first_name or keycloak_last_name):
        computed_name = f"{keycloak_first_name or ''} {keycloak_last_name or ''}".strip()

    return UserProfile(
        sub=user_uuid or token_payload.get("sub", ""),
        email=token_payload.get("email", ""),
        given_name=token_payload.get("given_name") or keycloak_first_name,
        family_name=token_payload.get("family_name") or keycloak_last_name,
        name=computed_name,
        picture=picture,
        groups=groups,
        github_installation_id=github_installation_id,
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

    # Validate MIME type - only real image types accepted
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


@router.post("/github-installation", response_model=GitHubInstallationResponse)
async def save_github_installation(
    request_data: GitHubInstallationRequest,
    token_payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GitHubInstallationResponse:
    """
    Save GitHub App installation ID to database.

    This endpoint is called either:
    1. Automatically when GitHub redirects back after app installation
    2. Manually when user enters existing installation ID
    """
    installation_id = request_data.installation_id.strip()

    if not installation_id:
        raise HTTPException(
            status_code=400,
            detail="Installation ID cannot be empty"
        )

    # Get user identifier from token (use preferred_username for consistency)
    user_sub = (
        token_payload.get("preferred_username")
        or token_payload.get("sub")
        or token_payload.get("username")
    )

    if not user_sub:
        raise HTTPException(
            status_code=400,
            detail=f"User identifier not found in token. Available fields: {list(token_payload.keys())}"
        )

    try:
        print(f"DEBUG /github-installation: Saving installation_id {installation_id} for user {user_sub}")

        # Check if record exists
        existing = db.query(UserGitHubInstallation).filter(
            UserGitHubInstallation.user_sub == user_sub
        ).first()

        if existing:
            # Update existing record
            print(f"DEBUG /github-installation: Updating existing record (old ID: {existing.installation_id})")
            existing.installation_id = installation_id
        else:
            # Create new record
            print(f"DEBUG /github-installation: Creating new record")
            new_record = UserGitHubInstallation(
                user_sub=user_sub,
                installation_id=installation_id
            )
            db.add(new_record)

        db.commit()

        # Verify the save
        verify_record = db.query(UserGitHubInstallation).filter(
            UserGitHubInstallation.user_sub == user_sub
        ).first()

        if verify_record and verify_record.installation_id == installation_id:
            print(f"DEBUG /github-installation: Successfully saved and verified installation_id")
            return GitHubInstallationResponse(
                message="GitHub installation ID saved successfully",
                installation_id=installation_id,
            )
        else:
            print(f"ERROR /github-installation: Verification failed after save")
            raise HTTPException(
                status_code=500,
                detail="Failed to verify saved installation ID"
            )

    except Exception as e:
        db.rollback()
        print(f"ERROR /github-installation: Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save GitHub installation ID: {str(e)}"
        ) from e
