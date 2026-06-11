from pydantic import BaseModel


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

    installation_id: str


class GitHubInstallationResponse(BaseModel):
    """Response after saving GitHub installation ID."""

    message: str
    installation_id: str
