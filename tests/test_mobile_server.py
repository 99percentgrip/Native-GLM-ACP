from __future__ import annotations

import asyncio
import json
import urllib.request

import pytest

from glm_acp.mobile_server import MobileServer, MobileServerError, _lan_address


def _request(server: MobileServer, path: str, data: bytes | None = None):
    request = urllib.request.Request(
        server.url.rstrip("/") + path, data=data, method="POST" if data else "GET"
    )
    if data:
        request.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(request, timeout=2)


def test_mobile_server_defaults_to_loopback():
    assert MobileServer().host == "127.0.0.1"


def test_mobile_server_refuses_public_bind_without_acknowledgement():
    with pytest.raises(MobileServerError, match="Refusing"):
        MobileServer("0.0.0.0:8765")


def test_lan_address_prefers_active_route_over_stale_hostname(monkeypatch):
    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def connect(self, _address):
            return None

        def getsockname(self):
            return ("192.168.254.125", 12345)

    monkeypatch.setattr("glm_acp.mobile_server.socket.socket", lambda *_args: Probe())
    monkeypatch.setattr(
        "glm_acp.mobile_server.socket.getaddrinfo",
        lambda *_args: [(None, None, None, None, ("192.168.254.103", 0))],
    )
    assert _lan_address() == "192.168.254.125"


def test_mobile_server_public_bind_advertises_lan_address(monkeypatch):
    monkeypatch.setattr("glm_acp.mobile_server._lan_address", lambda: "192.0.2.42")
    server = MobileServer("0.0.0.0:0", allow_public=True)
    server.start()
    try:
        assert server.phone_reachable is True
        assert server.url.startswith("http://192.0.2.42:")
    finally:
        server.stop()


def test_mobile_server_public_bind_requires_a_reachable_advertised_address(monkeypatch):
    monkeypatch.setattr("glm_acp.mobile_server._lan_address", lambda: None)
    server = MobileServer("0.0.0.0:0", allow_public=True)
    with pytest.raises(MobileServerError, match="Could not determine"):
        server.start()


def test_mobile_approval_url_carries_a_single_approval_id():
    server = MobileServer("127.0.0.1:8765")
    assert server.approval_url("one time/id") == "http://127.0.0.1:8765/?approval=one%20time%2Fid"


@pytest.mark.asyncio
async def test_mobile_approval_id_resolves_future():
    server = MobileServer("127.0.0.1:0")
    future = asyncio.get_running_loop().create_future()
    approval_id = server.register_approval(future)
    assert server._resolve(approval_id, True) is True
    assert await future is True


@pytest.mark.asyncio
async def test_mobile_expired_approval_is_rejected():
    server = MobileServer("127.0.0.1:0")
    future = asyncio.get_running_loop().create_future()
    approval_id = server.register_approval(future, ttl_seconds=-1)
    assert server._resolve(approval_id, True) is False
    assert future.done() is False


def test_mobile_server_never_echoes_credential_headers(capsys):
    server = MobileServer("127.0.0.1:0")
    server.start()
    try:
        request = urllib.request.Request(
            server.url, headers={"Authorization": "Bearer secret-value"}
        )
        urllib.request.urlopen(request, timeout=2).read()
    finally:
        server.stop()
    assert "secret-value" not in capsys.readouterr().out + capsys.readouterr().err


def test_mobile_server_start_stop_lifecycle():
    server = MobileServer("127.0.0.1:0")
    server.start()
    assert server.running
    server.stop()
    assert not server.running


def test_mobile_server_get_root_returns_pwa():
    server = MobileServer("127.0.0.1:0")
    server.start()
    try:
        assert b"Native GLM ACP" in _request(server, "/").read()
    finally:
        server.stop()


def test_mobile_server_get_root_with_approval_query_returns_pwa():
    server = MobileServer("127.0.0.1:0")
    server.start()
    try:
        assert b"Native GLM ACP" in _request(server, "/?approval=scan-token").read()
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_mobile_server_post_approval_shape():
    server = MobileServer("127.0.0.1:0")
    future = asyncio.get_running_loop().create_future()
    approval_id = server.register_approval(future)
    server.start()
    try:
        response = _request(
            server, f"/approve/{approval_id}", json.dumps({"approved": False}).encode()
        )
        assert response.status == 200
        assert await future is False
    finally:
        server.stop()
