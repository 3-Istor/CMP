"""
Real VictoriaMetrics binaries on 127.0.0.1 for the isolation tests, and a
minimal OTLP encoder. Each binary is found on PATH or in an environment
variable (nix build -o vm nixpkgs#victoriametrics, likewise victorialogs,
victoriatraces, opentelemetry-collector-contrib); tests skip without them.
"""

import os
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

import httpx
import pytest

BINARIES = {
    "vmauth": "VMAUTH",
    "victoria-metrics": "VICTORIA_METRICS",
    "victoria-logs": "VICTORIA_LOGS",
    "victoria-traces": "VICTORIA_TRACES",
    "otelcol-contrib": "OTELCOL_CONTRIB",
}


def binary(name: str) -> str | None:
    return os.environ.get(BINARIES[name]) or shutil.which(name)


def need(*names: str) -> None:
    missing = [n for n in names if not binary(n)]
    if missing:
        pytest.skip(f"not installed: {', '.join(missing)}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Process:
    def __init__(
        self, name: str, args: list[str], ready_path: str = "/health"
    ):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        listen = (
            []
            if name == "otelcol-contrib"
            else [f"-httpListenAddr=127.0.0.1:{self.port}"]
        )
        self.process = subprocess.Popen(
            [binary(name), *listen, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.ready_path = ready_path

    def wait(self, url: str | None = None) -> "Process":
        target = url or f"{self.url}{self.ready_path}"
        for _ in range(150):
            if self.process.poll() is not None:
                break
            try:
                if httpx.get(target).status_code < 500:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.process.kill()
        pytest.fail(self.process.stderr.read().decode()[-3000:])

    def stop(self) -> None:
        self.process.kill()
        self.process.wait()


STORE_FLAGS = {
    "victoria-metrics": ["-search.latencyOffset=0s"],
    # A trace is findable by id only once its index entry is flushed.
    "victoria-traces": ["-insert.indexFlushInterval=1s"],
}


def store(name: str, data: Path) -> Process:
    return Process(
        name,
        [
            f"-storageDataPath={data / name}",
            "-retentionPeriod=100y",
            *STORE_FLAGS.get(name, []),
        ],
    ).wait()


def wait_for_trace(traces_url: str, trace_id: str, account: int) -> None:
    for _ in range(100):
        httpx.get(f"{traces_url}/internal/force_flush")
        found = httpx.get(
            f"{traces_url}/select/jaeger/api/traces/{trace_id}",
            headers={"AccountID": str(account)},
        ).json()
        if found.get("data"):
            return
        time.sleep(0.2)
    pytest.fail(f"trace {trace_id} never indexed")


# ---- OTLP/protobuf, just what an ExportTraceServiceRequest needs --------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _bytes(field: int, data: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(data)) + data


def _attribute(key: str, value: str) -> bytes:
    return _bytes(1, key.encode()) + _bytes(2, _bytes(1, value.encode()))


def otlp_trace(trace_id: str, service: str, project: str | None) -> bytes:
    now = time.time_ns()
    attributes = _bytes(1, _attribute("service.name", service))
    if project is not None:
        attributes += _bytes(1, _attribute("cnp.project", project))
    span = (
        _bytes(1, bytes.fromhex(trace_id))
        + _bytes(2, bytes.fromhex("00f067aa0ba902b7"))
        + _bytes(5, b"GET /")
        + _varint(6 << 3)
        + _varint(2)
        + _varint(7 << 3 | 1)
        + struct.pack("<Q", now - 1_000_000)
        + _varint(8 << 3 | 1)
        + struct.pack("<Q", now)
    )
    resource_spans = _bytes(1, attributes) + _bytes(2, _bytes(2, span))
    return _bytes(1, resource_spans)
