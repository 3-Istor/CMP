import io
from unittest import mock

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers import app_icons as icons_router
from app.services import app_icons, keycloak_service
from app.services.keycloak_service import get_current_user

USER_TOKENS = {
    "admin": {"preferred_username": "adm", "realm_access": {"roles": []}},
    "member": {"preferred_username": "mem", "realm_access": {"roles": []}},
    "viewer": {"preferred_username": "vie", "realm_access": {"roles": []}},
    "outsider": {"preferred_username": "eve", "realm_access": {"roles": []}},
}
KEYCLOAK_IDS = {
    "adm": "uid-adm",
    "mem": "uid-mem",
    "vie": "uid-vie",
    "eve": "uid-eve",
}
ROLES = {
    ("uid-adm", "shop"): "admin",
    ("uid-mem", "shop"): "member",
    ("uid-vie", "shop"): "guest",
}
BUILTIN = {"kind": "builtin", "name": "Leaf", "color": "emerald"}


def _image_bytes(fmt, size=(600, 300), color=(200, 30, 30), **save):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt, **save)
    return out.getvalue()


def _png():
    return _image_bytes("PNG")


def _jpeg_with_exif():
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    return _image_bytes("JPEG", exif=exif.tobytes())


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(
            Deployment(
                id=1,
                name="web",
                template_id="k3s-gitops-app",
                project_id="shop",
                provider_type=ProviderType.KUBERNETES,
                status=DeploymentStatus.RUNNING,
            )
        )
        db.commit()
    return factory


@pytest.fixture(autouse=True)
def keycloak():
    with (
        mock.patch.object(
            keycloak_service, "_get_admin_token", return_value="admin"
        ),
        mock.patch.object(
            keycloak_service,
            "_find_user_by_username",
            side_effect=lambda username, _: {"id": KEYCLOAK_IDS[username]},
        ),
        mock.patch.object(
            keycloak_service,
            "get_project_role",
            side_effect=lambda user_id, project: ROLES.get((user_id, project)),
        ),
    ):
        yield


@pytest.fixture
def s3_settings():
    with (
        mock.patch.object(settings, "ICONS_S3_ACCESS_KEY_ID", "k"),
        mock.patch.object(settings, "ICONS_S3_SECRET_ACCESS_KEY", "s"),
        mock.patch.object(settings, "ICONS_S3_ENDPOINT", "http://garage"),
    ):
        yield


@pytest.fixture
def storage(s3_settings):
    objects = {}
    client = mock.Mock()
    client.put_object.side_effect = lambda **kw: objects.update(
        {kw["Key"]: kw["Body"]}
    )
    client.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO(objects[kw["Key"]])
    }
    client.delete_object.side_effect = lambda **kw: objects.pop(
        kw["Key"], None
    )
    with mock.patch.object(app_icons, "_client", return_value=client):
        yield objects


def _client(session_factory, who="member"):
    app = FastAPI()
    app.include_router(icons_router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS[who]
    return TestClient(app)


def _upload(client, data, name="icon.png", mime="image/png"):
    return client.put(
        "/api/deployments/1/icon", files={"file": (name, data, mime)}
    )


class TestMagicBytes:
    @pytest.mark.parametrize(
        "data,expected",
        [
            (_image_bytes("PNG"), "png"),
            (_image_bytes("JPEG"), "jpeg"),
            (_image_bytes("WEBP"), "webp"),
        ],
    )
    def test_accepts_png_jpeg_and_webp(self, data, expected):
        assert app_icons.sniff_type(data) == expected

    @pytest.mark.parametrize(
        "data",
        [
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            b'<?xml version="1.0"?><svg></svg>',
            _image_bytes("GIF"),
            b"hello" + _image_bytes("PNG"),
            b"",
        ],
    )
    def test_refuses_svg_gif_and_polyglots(self, data):
        assert app_icons.sniff_type(data) is None

    def test_truncated_png_is_refused_on_decode(self):
        with pytest.raises(app_icons.IconError) as err:
            app_icons.normalize_upload(_png()[:40])

        assert err.value.status_code == 415


class TestNormalize:
    def test_output_is_a_256_square_webp(self):
        out = app_icons.normalize_upload(_png())

        image = Image.open(io.BytesIO(out))
        assert (image.format, image.size) == ("WEBP", (256, 256))

    def test_output_has_no_exif(self):
        out = app_icons.normalize_upload(_jpeg_with_exif())

        assert not Image.open(io.BytesIO(out)).getexif()

    def test_rejects_files_over_512_kb(self):
        with pytest.raises(app_icons.IconError) as err:
            app_icons.normalize_upload(b"\x89PNG\r\n\x1a\n" + b"0" * 600_000)

        assert err.value.status_code == 413

    def test_rejects_empty_files(self):
        with pytest.raises(app_icons.IconError) as err:
            app_icons.normalize_upload(b"")

        assert err.value.status_code == 400


class TestSetIcon:
    def test_member_can_choose_a_builtin_icon_without_storage(
        self, session_factory
    ):
        res = _client(session_factory).put(
            "/api/deployments/1/icon", json=BUILTIN
        )

        assert res.json() == {**BUILTIN, "hash": None}

    @pytest.mark.parametrize(
        "body",
        [
            {"kind": "builtin", "name": "Skull", "color": "emerald"},
            {"kind": "builtin", "name": "Leaf", "color": "pink"},
            {"kind": "upload", "name": "Leaf", "color": "rose"},
        ],
    )
    def test_unknown_builtin_choices_are_refused(self, session_factory, body):
        res = _client(session_factory).put(
            "/api/deployments/1/icon", json=body
        )

        assert res.status_code == 422

    @pytest.mark.parametrize("who", ["viewer", "outsider"])
    def test_below_member_cannot_change_the_icon(self, session_factory, who):
        res = _client(session_factory, who).put(
            "/api/deployments/1/icon", json=BUILTIN
        )

        assert res.status_code == 403

    def test_viewer_cannot_reset_the_icon(self, session_factory):
        res = _client(session_factory, "viewer").delete(
            "/api/deployments/1/icon"
        )

        assert res.status_code == 403

    def test_upload_returns_503_when_storage_is_not_configured(
        self, session_factory
    ):
        res = _upload(_client(session_factory), _png())

        assert res.status_code == 503

    def test_upload_returns_503_when_storage_fails(
        self, session_factory, s3_settings
    ):
        failing = mock.Mock()
        failing.put_object.side_effect = EndpointConnectionError(
            endpoint_url="http://garage"
        )

        with mock.patch.object(app_icons, "_client", return_value=failing):
            res = _upload(_client(session_factory), _png())

        assert res.status_code == 503

    def test_upload_stores_a_webp_under_project_app_hash(
        self, session_factory, storage
    ):
        res = _upload(_client(session_factory), _png())

        (key,) = storage
        assert key == f"shop/web/{res.json()['hash']}.webp"
        assert Image.open(io.BytesIO(storage[key])).format == "WEBP"

    def test_upload_refuses_svg_even_with_an_image_content_type(
        self, session_factory, storage
    ):
        res = _upload(_client(session_factory), b"<svg></svg>", "a.png")

        assert (res.status_code, storage) == (415, {})

    def test_upload_refuses_a_polyglot_with_leading_text(
        self, session_factory, storage
    ):
        res = _upload(_client(session_factory), b"x" + _png())

        assert (res.status_code, storage) == (415, {})

    def test_upload_refuses_oversized_images(self, session_factory, storage):
        big = b"\x89PNG\r\n\x1a\n" + b"0" * 600_000

        res = _upload(_client(session_factory), big)

        assert (res.status_code, storage) == (413, {})

    def test_replacing_an_upload_removes_the_old_object(
        self, session_factory, storage
    ):
        client = _client(session_factory)
        _upload(client, _png())

        res = client.put("/api/deployments/1/icon", json=BUILTIN)

        assert (res.status_code, storage) == (200, {})


class TestGetIcon:
    def test_streams_the_webp_with_private_cache_and_etag(
        self, session_factory, storage
    ):
        _upload(_client(session_factory), _png())

        res = _client(session_factory, "viewer").get("/api/deployments/1/icon")

        assert (
            res.headers["content-type"],
            res.headers["cache-control"],
            bool(res.headers["etag"]),
        ) == ("image/webp", "private, max-age=300", True)

    def test_answers_304_when_the_etag_matches(self, session_factory, storage):
        _upload(_client(session_factory), _png())
        client = _client(session_factory, "viewer")
        etag = client.get("/api/deployments/1/icon").headers["etag"]

        res = client.get(
            "/api/deployments/1/icon", headers={"If-None-Match": etag}
        )

        assert res.status_code == 304

    def test_outsider_cannot_read_the_icon(self, session_factory, storage):
        _upload(_client(session_factory), _png())

        res = _client(session_factory, "outsider").get(
            "/api/deployments/1/icon"
        )

        assert res.status_code == 403

    def test_returns_404_when_no_custom_icon(self, session_factory):
        res = _client(session_factory, "viewer").get("/api/deployments/1/icon")

        assert res.status_code == 404


class TestResetIcon:
    def test_delete_returns_to_the_default_and_removes_the_object(
        self, session_factory, storage
    ):
        client = _client(session_factory)
        _upload(client, _png())

        res = client.delete("/api/deployments/1/icon")

        assert (res.status_code, storage) == (204, {})
        with session_factory() as db:
            assert db.get(Deployment, 1).icon is None
