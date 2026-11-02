from typing import Literal

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """User profile data from JWT token."""

    sub: str
    email: str
    given_name: str | None = None
    family_name: str | None = None
    name: str | None = None
    picture: str | None = None
    groups: list[str] = []
    github_installation_id: str | None = None


class PictureUploadResponse(BaseModel):
    """Response after uploading a new profile picture."""

    message: str
    picture_url: str


class GitHubInstallationRequest(BaseModel):
    """Request to save GitHub App installation ID."""

    installation_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class GitHubInstallationResponse(BaseModel):
    """Response after saving GitHub installation ID."""

    message: str
    installation_id: str
    github_login: str
    account: str


class GitHubInstallationOption(BaseModel):
    id: str
    account: str
    account_type: str


class GitHubConnectionStatus(BaseModel):
    github_installation_id: str | None
    identity_linked: bool
    github_login: str | None
    oauth_configured: bool
    installations: list[GitHubInstallationOption]
    installation_url: str | None


class GitHubOAuthStartRequest(BaseModel):
    mode: Literal["authorize", "install"] = "authorize"
    binding: str = Field(min_length=32, max_length=256, repr=False)


class GitHubOAuthCallbackRequest(GitHubOAuthStartRequest):
    state: str = Field(min_length=1, max_length=128, repr=False)
    code: str | None = Field(
        default=None, min_length=1, max_length=512, repr=False
    )
