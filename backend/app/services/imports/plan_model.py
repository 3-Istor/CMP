"""
ImportPlan, vendored from 3-Istor/extern-project-analyzer cnp_analyzer/model.py
at 70ddf701a1f4b5da5a90a76907edf136fe4d40e1 (CNP-237). The worker API
validates plans with it; copy the file again, unchanged, when the analyzer
changes its schema_version.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1"

# DNS-1035; app names are capped at 40 characters like the CNP registry.
DNS_1035 = r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$"
AppName = Annotated[str, Field(pattern=r"^[a-z]([-a-z0-9]{0,38}[a-z0-9])?$")]
PortNumber = Annotated[int, Field(ge=1, le=65535)]
EvidenceKind = Literal[
    "compose_depends_on",
    "env_url",
    "nginx_proxy_pass",
    "rest_client",
    "k8s_env",
]
BackingKind = Literal[
    "mongodb",
    "redis",
    "neo4j",
    "elasticsearch",
    "postgres",
    "mysql",
    "rabbitmq",
    "kafka",
    "other",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Source(_Model):
    branch: str | None = None
    sha: str | None = None


class Stack(_Model):
    language: str
    framework: str | None = None
    version: str | None = None


class Build(_Model):
    strategy: Literal["dockerfile", "repaired_dockerfile", "railpack"]
    context: str
    dockerfile: str | None = None
    target: str | None = None
    stage: str | None = None
    generated_dockerfile: str | None = None
    generated_dockerignore: str | None = None
    railpack_env: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class Port(_Model):
    name: str
    port: PortNumber


class Probe(_Model):
    kind: Literal["tcp", "http"]
    path: str | None = None
    port: PortNumber


class EnvVar(_Model):
    name: str
    value: str | None = None
    from_link: str | None = None
    secret: bool = False


class Resources(_Model):
    requests_mem_mib: int
    limits_mem_mib: int
    cpu: str = "100m"


class Confidence(_Model):
    overall: Literal["high", "medium", "low"]
    reasons: list[str] = Field(default_factory=list)


class ServicePlan(_Model):
    name: AppName
    path: str
    stack: Stack
    build: Build
    ports: list[Port]
    probes: Probe | None
    env: list[EnvVar] = Field(default_factory=list)
    public: bool = False
    resources: Resources
    confidence: Confidence


class BackingPlan(_Model):
    name: AppName
    kind: BackingKind
    image: str
    supported: bool
    port: PortNumber | None = None
    resources: Resources
    credentials_found: list[str] = Field(default_factory=list)


class Evidence(_Model):
    file: str
    line: int
    kind: EvidenceKind


class Link(_Model):
    from_: AppName = Field(alias="from")
    to: AppName
    port: PortNumber | None
    alias: str
    evidence: list[Evidence]

    @field_validator("alias")
    @classmethod
    def _alias_is_dns_1035(cls, value: str) -> str:
        if not re.fullmatch(DNS_1035, value):
            raise ValueError("alias must be a DNS-1035 label")
        return value


class Item(_Model):
    name: str
    kind: str
    reason: str
    file: str | None = None
    line: int | None = None


class SecretFinding(_Model):
    file: str
    line: int
    kind: str
    redacted: str


class Capacity(_Model):
    requests_mem_mib: int
    limits_mem_mib: int


class ImportPlan(_Model):
    schema_version: Literal["1"] = SCHEMA_VERSION
    source: Source
    services: list[ServicePlan]
    backing: list[BackingPlan]
    links: list[Link]
    unsupported: list[Item]
    secrets_found: list[SecretFinding]
    warnings: list[str]
    capacity: Capacity

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)
