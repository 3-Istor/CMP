"""
App icons: validation, re-encoding and storage of uploaded images.

The bytes a user sends are never stored. They are identified by magic bytes,
decoded, cropped to a square and re-encoded as a 256x256 WebP, which drops
every piece of metadata. The result goes to a private Garage bucket and is
only ever served back through the CMP.
"""

import hashlib
import io
import logging
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings

logger = logging.getLogger(__name__)

BUILTIN_ICONS = frozenset(
    {
        "MessageCircle",
        "Globe",
        "Database",
        "ShoppingCart",
        "BarChart3",
        "Cpu",
        "Mail",
        "Calendar",
        "Lock",
        "Gamepad2",
        "Leaf",
        "Package",
    }
)
BUILTIN_COLORS = frozenset(
    {"indigo", "emerald", "amber", "rose", "sky", "slate"}
)

MAX_UPLOAD_BYTES = 512 * 1024
ICON_SIZE = 256
MAX_SOURCE_PIXELS = 25_000_000

_FORMATS = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}


class IconError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def sniff_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def normalize_upload(data: bytes) -> bytes:
    if not data:
        raise IconError(400, "The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IconError(413, "The image is larger than 512 KB.")
    kind = sniff_type(data)
    if kind is None:
        raise IconError(415, "Only PNG, JPEG and WebP images are accepted.")
    try:
        with Image.open(io.BytesIO(data), formats=[_FORMATS[kind]]) as image:
            if image.width * image.height > MAX_SOURCE_PIXELS:
                raise IconError(413, "The image has too many pixels.")
            image.load()
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image = ImageOps.fit(
                image, (ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS
            )
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=85, method=4)
    except IconError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise IconError(415, "The image cannot be read.") from exc
    return out.getvalue()


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value) or "_"


def object_key(project: str, app: str, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return f"{_safe_segment(project)}/{_safe_segment(app)}/{digest}.webp"


def is_configured() -> bool:
    return bool(
        settings.ICONS_S3_ACCESS_KEY_ID
        and settings.ICONS_S3_SECRET_ACCESS_KEY
        and (settings.ICONS_S3_ENDPOINT or settings.AVATARS_S3_ENDPOINT)
    )


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.ICONS_S3_ENDPOINT
        or settings.AVATARS_S3_ENDPOINT,
        aws_access_key_id=settings.ICONS_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.ICONS_S3_SECRET_ACCESS_KEY,
        region_name=settings.ICONS_S3_REGION,
        config=Config(
            connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}
        ),
    )


def unavailable() -> IconError:
    return IconError(
        503,
        "Custom icon storage is unavailable. Built-in icons still work.",
    )


def store(key: str, content: bytes) -> None:
    if not is_configured():
        raise unavailable()
    try:
        _client().put_object(
            Bucket=settings.ICONS_S3_BUCKET,
            Key=key,
            Body=content,
            ContentType="image/webp",
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("icon upload to object storage failed: %s", exc)
        raise unavailable() from exc


def fetch(key: str) -> bytes:
    if not is_configured():
        raise unavailable()
    try:
        response = _client().get_object(
            Bucket=settings.ICONS_S3_BUCKET, Key=key
        )
        return response["Body"].read()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "NoSuchKey",
            "404",
        }:
            raise IconError(404, "Icon not found.") from exc
        logger.warning("icon read from object storage failed: %s", exc)
        raise unavailable() from exc
    except BotoCoreError as exc:
        logger.warning("icon read from object storage failed: %s", exc)
        raise unavailable() from exc


def remove(key: str) -> None:
    if not is_configured():
        return
    try:
        _client().delete_object(Bucket=settings.ICONS_S3_BUCKET, Key=key)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("icon cleanup in object storage failed: %s", exc)
