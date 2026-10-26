import base64
import json
from pathlib import Path
from unittest import mock

import pytest

from app.models.project import TargetCloud
from app.services import kube_client as kc
from app.services.clusters import state

PEM = b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n"
SECRET_URL = (
    "https://10.0.0.1:443/api/v1/namespaces/argocd/secrets/cluster-aws"
)


def _b64(raw: bytes | str) -> str:
    return base64.b64encode(
        raw.encode() if isinstance(raw, str) else raw
    ).decode()


SECRET = {
    "data": {
        "name": _b64("aws"),
        "server": _b64("https://x"),
        "config": _b64(
            json.dumps(
                {
                    "bearerToken": "t",
                    "tlsClientConfig": {
                        "insecure": False,
                        "caData": _b64(PEM),
                    },
                }
            )
        ),
    }
}


def _response(status, body=None):
    response = mock.Mock(status_code=status, ok=status < 400)
    response.json.return_value = body
    return response


@pytest.fixture(autouse=True)
def cluster(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("sa-token\n")
    (tmp_path / "ca.crt").write_bytes(b"in-cluster")
    monkeypatch.setattr(kc, "SERVICE_ACCOUNT_DIR", tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.setattr(kc, "_credentials", {})
    monkeypatch.setattr(state, "_cache", {TargetCloud.AWS: "up"})


def test_onprem_default_unchanged():
    with mock.patch.object(kc, "_session") as session:
        session.request.return_value = _response(200, {"items": [1]})
        assert kc.kube_list("/api/v1/namespaces") == [1]

    _, kwargs = session.request.call_args
    assert session.request.call_args.args[1] == (
        "https://10.0.0.1:443/api/v1/namespaces"
    )
    assert kwargs["headers"]["Authorization"] == "Bearer sa-token"
    assert kwargs["verify"].endswith("ca.crt")


def test_cloud_down_raises_without_request(monkeypatch):
    monkeypatch.setattr(state, "_cache", {TargetCloud.AWS: "down"})
    with mock.patch.object(kc, "_session") as session:
        with pytest.raises(kc.ClusterNotConnectedError) as exc:
            kc.kube_get("/api/v1/namespaces", cloud=TargetCloud.AWS)

    assert exc.value.cloud == "aws"
    assert not isinstance(exc.value, kc.KubeUnavailableError)
    session.request.assert_not_called()


def _routed(cloud_responses, secret_reads):
    def request(method, url, **kwargs):
        if url == SECRET_URL:
            secret_reads.append(kwargs["headers"]["Authorization"])
            return _response(200, SECRET)
        cloud_responses[0]["seen"].append((url, kwargs))
        return cloud_responses.pop(0)["response"]

    return request


def test_cloud_up_uses_argocd_secret():
    seen, reads = [], []
    responses = [{"response": _response(200, {"items": []}), "seen": seen}]
    with mock.patch.object(kc, "_session") as session:
        session.request.side_effect = _routed(responses, reads)
        assert kc.kube_get("/api/v1/namespaces", cloud=TargetCloud.AWS) == {
            "items": []
        }

    url, kwargs = seen[0]
    assert url == "https://x/api/v1/namespaces"
    assert kwargs["headers"]["Authorization"] == "Bearer t"
    assert Path(kwargs["verify"]).read_bytes() == PEM
    assert reads == ["Bearer sa-token"]


def test_cloud_target_rereads_secret_on_401():
    seen, reads = [], []
    responses = [
        {"response": _response(401), "seen": seen},
        {"response": _response(200, {"kind": "NamespaceList"}), "seen": seen},
    ]
    with mock.patch.object(kc, "_session") as session:
        session.request.side_effect = _routed(responses, reads)
        result = kc.kube_get("/api/v1/namespaces", cloud=TargetCloud.AWS)

    assert result == {"kind": "NamespaceList"}
    assert len(reads) == 2


def test_outside_cluster_stays_kube_unavailable(monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    with pytest.raises(kc.KubeUnavailableError):
        kc.kube_get("/api/v1/namespaces")


def test_malformed_cloud_secret_is_kube_unavailable():
    secret = {"data": {**SECRET["data"], "config": _b64("[]")}}
    with mock.patch.object(kc, "_session") as session:
        session.request.return_value = _response(200, secret)
        with pytest.raises(kc.KubeUnavailableError):
            kc.kube_get("/api/v1/namespaces", cloud=TargetCloud.AWS)
