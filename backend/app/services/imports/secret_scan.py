"""
Credential detection of the analyzer, vendored from
3-Istor/extern-project-analyzer cnp_analyzer/secrets.py at 70ddf70.

The CMP applies it again to the plan it receives: the workflow runs code
of the imported repository, so its output is not trusted to be clean.
Keep in sync with the analyzer.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

MASK = "****"

_URL_START = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]{0,30}://")
_URL_STOP = frozenset(" \t\r\n\"'<>`")
_MAX_URL = 2048
_SECRET_LAST = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "pass",
        "secret",
        "token",
        "apikey",
        "credentials",
        "credential",
    }
)
_SECRET_PAIR = frozenset(
    {
        "api_key",
        "secret_key",
        "access_key",
        "private_key",
        "client_secret",
        "auth_token",
        "access_token",
        "refresh_token",
        "secret_access_key",
        "signing_key",
        "encryption_key",
        "master_key",
        "jwt_secret",
    }
)
# Any of these anywhere in a key keeps its value out of the plan.
_SECRET_SEGMENTS = frozenset(
    {
        "password",
        "passwd",
        "passwords",
        "pwd",
        "pass",
        "passphrase",
        "secret",
        "secrets",
        "token",
        "tokens",
        "key",
        "keys",
        "apikey",
        "credential",
        "credentials",
        "creds",
        "auth",
        "authorization",
        "private",
        "conn",
        "connection",
        "connstr",
        "dsn",
        "salt",
        "signature",
        "cookie",
        "session",
        "jwt",
        "bearer",
        "cert",
        "certificate",
        "pem",
        "otp",
    }
)
_TOKEN_PATTERNS = [
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
    ),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,90}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,60}\b")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,99}\b")),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY"
        ),
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*"
        ),
    ),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*")),
]
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{20,}")
_B64_RUN = re.compile(r"[A-Za-z0-9+/_\-]{40,}={0,2}")
_NOT_A_VALUE = frozenset({"", "true", "false", "null", "none", "yes", "no"})

_NUMBER = re.compile(r"-?\d{1,20}(?:\.\d{1,20}){0,3}")
_BOOLEAN = frozenset({"true", "false", "yes", "no", "on", "off"})
_WORD = re.compile(r"[A-Za-z]{1,32}(?:[_\-][A-Za-z]{1,32}){0,4}")
_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)*(?::\d{1,5})?"
)
_DOTTED_HOST = re.compile(
    r"[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?::\d{1,5})?|[a-z][a-z0-9\-]*:\d{1,5}"
)
_URL = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.\-]{0,30}:){1,3}//"
    r"(?:[A-Za-z0-9.\-]{1,253}|[+*])(?::\d{1,5})?"
    r"(?:[/?#][A-Za-z0-9._~/?#\[\]!$&()*+,;=%:\-]*)?"
)
_PATH_PART = (
    r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"
    r"|(?:/|\./|\.\./|~/)[A-Za-z0-9._\-/]*"
    r"|[A-Za-z0-9._\-/]*/[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,8}"
    r"|[A-Z][a-z]+(?:/[A-Z][A-Za-z_]+)+)"
)
_PATH = re.compile(rf"{_PATH_PART}(?::{_PATH_PART}){{0,20}}")
_VAR_REF = re.compile(r"\$\(?\{?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    redacted: str


def _segments(key: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1_\2", key)
    return [s for s in re.split(r"[^a-z0-9]+", spaced.lower()) if s]


def is_secret_key(key: str) -> bool:
    segs = _segments(key)
    if not segs or segs[-1] == "file":
        return False
    if segs[-1] in _SECRET_LAST:
        return True
    if len(segs) >= 2 and f"{segs[-2]}_{segs[-1]}" in _SECRET_PAIR:
        return True
    return (
        len(segs) >= 3 and f"{segs[-3]}_{segs[-2]}_{segs[-1]}" in _SECRET_PAIR
    )


def has_secret_segment(key: str) -> bool:
    """Broader than is_secret_key: used to keep values out of the plan, not to report findings."""
    return any(seg in _SECRET_SEGMENTS for seg in _segments(key))


def _classes(run: str) -> int:
    return sum(
        1
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]")
        if re.search(pattern, run)
    )


def _random_spans(value: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in _HEX_RUN.finditer(value)]
    spans += [
        m.span()
        for m in _ALNUM_RUN.finditer(value)
        if _classes(m.group(0)) >= 3
        or (_classes(m.group(0)) == 2 and re.search(r"[0-9]", m.group(0)))
    ]
    spans += [
        m.span() for m in _B64_RUN.finditer(value) if _classes(m.group(0)) >= 3
    ]
    return spans


def looks_random(value: str) -> bool:
    """High-entropy runs: hex digests, mixed-case/digit tokens, base64 blobs."""
    return bool(_random_spans(value))


def is_placeholder(value: str) -> bool:
    v = value.strip().strip("\"'")
    if v.lower() in _NOT_A_VALUE:
        return True
    if any(
        pattern.search(v) for _kind, pattern in _TOKEN_PATTERNS
    ) or looks_random(v):
        return False
    if v.startswith(("${", "$(", "{{", "<", "%(")) or (
        v.startswith("$") and re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", v)
    ):
        return True
    if re.fullmatch(
        r"(?i)(change[-_]?(me|this|it)|replace[-_]?me|todo|dummy|example|placeholder|your[-_].*)",
        v,
    ):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", v):
        return True
    return bool(re.fullmatch(r"\*+|x+|\.\.\.", v, re.IGNORECASE))


def _userinfo_spans(value: str) -> list[tuple[int, int]]:
    """(start, end) of the text between '://' and the last '@' before the next blank or quote."""
    spans = []
    for match in _URL_START.finditer(value):
        start = match.end()
        end = start
        limit = min(len(value), start + _MAX_URL)
        while end < limit and value[end] not in _URL_STOP:
            end += 1
        at = value.rfind("@", start, end)
        if at >= 0:
            spans.append((start, at))
    return spans


def redact_url_credentials(value: str) -> str:
    out = []
    last = 0
    for start, at in _userinfo_spans(value):
        if start < last:
            continue
        out.append(value[last:start])
        out.append(MASK)
        last = at
    out.append(value[last:])
    return "".join(out)


def strip_userinfo(value: str) -> str:
    out = []
    last = 0
    for start, at in _userinfo_spans(value):
        if start < last:
            continue
        out.append(value[last:start])
        last = at + 1
    out.append(value[last:])
    return "".join(out)


def url_has_credentials(value: str) -> bool:
    for start, at in _userinfo_spans(value):
        userinfo = value[start:at]
        user, sep, password = userinfo.partition(":")
        if sep and password and not is_placeholder(password):
            return True
        if not sep and (
            looks_random(user)
            or any(p.search(user) for _k, p in _TOKEN_PATTERNS)
        ):
            return True
    return False


def scan_value(key: str, value: str, file: str, line: int) -> list[Finding]:
    findings: list[Finding] = []
    if url_has_credentials(value):
        findings.append(
            Finding(
                file,
                line,
                "url_password",
                f"{key}={redact_url_credentials(value)}"[:200],
            )
        )
    if is_secret_key(key) and not is_placeholder(value):
        findings.append(
            Finding(
                file,
                line,
                (
                    "hardcoded_password"
                    if "pass" in key.lower() or "pwd" in key.lower()
                    else "hardcoded_secret"
                ),
                f"{key}={MASK}",
            )
        )
    elif (
        key.upper().endswith("NEO4J_AUTH")
        and "/" in value
        and not is_placeholder(value.split("/", 1)[1])
    ):
        user = value.split("/", 1)[0]
        findings.append(
            Finding(file, line, "neo4j_auth", f"{key}={user}/{MASK}")
        )
    return findings


def is_secret_value(key: str, value: str | None) -> bool:
    if value is None:
        return False
    if url_has_credentials(value):
        return True
    if is_secret_key(key) and not is_placeholder(value):
        return True
    return key.upper().endswith("NEO4J_AUTH") and "/" in value


def _clearly_not_secret(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    if len(v) > 512:
        return False
    if v.lower() in _BOOLEAN or _NUMBER.fullmatch(v) or _WORD.fullmatch(v):
        return True
    if all(
        _HOST.fullmatch(part)
        and (_DOTTED_HOST.fullmatch(part) or _WORD.fullmatch(part))
        for part in v.split(",")
    ):
        return True
    if _URL.fullmatch(v):
        query = re.split(r"[?#]", v, maxsplit=1)
        return len(query) == 1 or not has_secret_segment(query[1])
    return bool(_PATH.fullmatch(v))


def is_emittable_value(
    key: str, value: str | None, source_vars: Iterable[str] = ()
) -> bool:
    """True only when a repo value is clearly not a credential and can be copied into the plan."""
    if value is None:
        return True
    if has_secret_segment(key) or any(
        has_secret_segment(var) for var in source_vars
    ):
        return False
    if any(has_secret_segment(name) for name in _VAR_REF.findall(value)):
        return False
    if "@" in value or looks_random(value) or scan_text(value, ""):
        return False
    return _clearly_not_secret(value)


def sanitize_text(text: str) -> str:
    """Masks credentials in free text that reaches the plan (warnings, reasons, notes)."""
    out = redact_url_credentials(text)
    for _kind, pattern in _TOKEN_PATTERNS:
        out = pattern.sub(MASK, out)
    spans = sorted(_random_spans(out))
    if not spans:
        return out
    parts = []
    last = 0
    for start, end in spans:
        if end <= last:
            continue
        parts.append(out[last : max(start, last)])
        parts.append(MASK)
        last = end
    parts.append(out[last:])
    return "".join(parts)


def scan_text(text: str, file: str) -> list[Finding]:
    findings: list[Finding] = []
    for kind, pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            token = match.group(0)
            findings.append(Finding(file, line, kind, f"{token[:4]}{MASK}"))
    return findings
