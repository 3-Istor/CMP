from typing import Literal

from pydantic import BaseModel, Field, field_validator

Locale = Literal["fr", "en"]

MAX_FAVORITES = 50


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
    locale: Locale | None = None
    cnp_admin: bool = False
    # Platform identities: infra, security-auditor, finops (Keycloak groups).
    platform_roles: list[str] = []


class PictureUploadResponse(BaseModel):
    """Response after uploading a new profile picture."""

    message: str
    picture_url: str


class GitHubInstallationRequest(BaseModel):
    """An installation of CNP-Portal the caller's GitHub account reaches."""

    installation_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class GitHubInstallationResponse(BaseModel):
    """The installation recorded once the user's token showed it."""

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
    # Random value the frontend bridge keeps in an httpOnly cookie.
    binding: str = Field(min_length=32, max_length=256, repr=False)


class GitHubOAuthCallbackRequest(BaseModel):
    binding: str = Field(min_length=32, max_length=256, repr=False)
    state: str = Field(min_length=1, max_length=128, repr=False)
    code: str | None = Field(
        default=None, min_length=1, max_length=512, repr=False
    )


class Favorites(BaseModel):
    """Projects (by name) and apps (by deployment id) a user starred."""

    projects: list[str] = Field(default_factory=list)
    apps: list[int] = Field(default_factory=list)

    @field_validator("projects", mode="before")
    @classmethod
    def _clean_projects(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if not all(isinstance(v, str) and v.strip() for v in value):
            raise ValueError("project names must be non-empty strings")
        return list(dict.fromkeys(v.strip() for v in value))

    @field_validator("apps", mode="before")
    @classmethod
    def _clean_apps(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if not all(
            isinstance(v, int) and not isinstance(v, bool) and v > 0
            for v in value
        ):
            raise ValueError("deployment ids must be positive integers")
        return list(dict.fromkeys(value))

    @field_validator("projects", "apps")
    @classmethod
    def _cap(cls, value: list) -> list:
        if len(value) > MAX_FAVORITES:
            raise ValueError(f"at most {MAX_FAVORITES} favorites per kind")
        return value


class UserPreferences(BaseModel):
    """UI preferences stored for the current user."""

    locale: Locale | None = None
    favorites: Favorites = Field(default_factory=Favorites)


class PreferencesUpdate(BaseModel):
    """Writable part of PUT /account/preferences."""

    locale: Locale | None = None
