import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from jana.api import create_app
from jana.contracts import PUBLIC_FIELDS


def test_offline_socket_guard_blocks_external_but_allows_loopback():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import errno,socket; import jana.api; "
                "external=socket.socket().connect_ex(('1.1.1.1',443)); "
                "loopback=socket.socket().connect_ex(('127.0.0.1',9)); "
                "udp=False; dns=[]; "
                "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                "\ntry: s.sendto(b'x',('1.1.1.1',53))\n"
                "except PermissionError: udp=True\n"
                "sendmsg=True\n"
                "if hasattr(s,'sendmsg'):\n"
                " sendmsg=False\n"
                " try: s.sendmsg([b'x'],[],0,('1.1.1.1',53))\n"
                " except PermissionError: sendmsg=True\n"
                "for call in [lambda: socket.getaddrinfo('example.com',443), "
                "lambda: socket.gethostbyname('example.com'), "
                "lambda: socket.gethostbyname_ex('example.com'), "
                "lambda: socket.gethostbyaddr('1.1.1.1'), "
                "lambda: socket.getnameinfo(('1.1.1.1',443),0), "
                "lambda: socket.gethostbyaddr('127.22.33.44'), "
                "lambda: socket.getnameinfo(('127.22.33.44',443),0)]:\n"
                " try: call(); dns.append(False)\n"
                " except (socket.gaierror,socket.herror) as exc: "
                "dns.append('external DNS disabled' in str(exc))\n"
                "raise SystemExit(0 if external==errno.EACCES and "
                "loopback!=errno.EACCES and udp and sendmsg and all(dns) else 1)"
            ),
        ],
        env={**os.environ, "JANA_OFFLINE": "1"},
        check=False,
    )
    assert probe.returncode == 0


def test_multipart_stays_in_memory_and_closes_files(monkeypatch):
    from starlette import formparsers
    original = formparsers.SpooledTemporaryFile
    created = []

    def capture(*args, **kwargs):
        file = original(*args, **kwargs)
        created.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", capture)
    records = [{"id": str(index), "raw_address": "a" * 6000} for index in range(200)]
    payload = json.dumps(records).encode()
    assert len(payload) > 1024 * 1024
    with TestClient(create_app(parser=engine)) as client:
        response = client.post("/parse", files={"file": ("input.json", payload, "application/json")})
    assert response.status_code == 200
    assert created and all(not file._rolled and file.closed for file in created)


def engine(records, gazetteer=None, explain=False):
    output = []
    for record in records:
        result = dict.fromkeys(PUBLIC_FIELDS)
        result.update(id=record["id"], raw_address=record["raw_address"], confidence=0.1)
        output.append({"result": result, "fields": {}, "warnings": []} if explain else result)
    return output


@pytest.fixture
def client():
    with TestClient(create_app(parser=engine)) as instance:
        yield instance


@pytest.mark.parametrize("path", ["/parse", "/batch", "/api/parse", "/api/v1/parse"])
def test_aliases_accept_json_and_multipart_identically(client, path):
    records = [{"id": "same", "raw_address": "  Ә үлгі  "}, {"id": "same", "raw_address": ""}]
    json_response = client.post(path, json=records)
    file_response = client.post(path, files={"file": ("input.json", json.dumps(records).encode(), "application/json")})
    assert json_response.status_code == file_response.status_code == 200
    assert json_response.json() == file_response.json()
    assert [row["raw_address"] for row in json_response.json()] == [r["raw_address"] for r in records]
    assert all(list(row) == list(PUBLIC_FIELDS) for row in json_response.json())


def test_internal_explanation_does_not_leak_into_public_contract(client):
    records = [{"id": "test", "raw_address": ""}]
    response = client.post("/internal/parse", json=records)
    assert response.status_code == 200
    assert response.json()["records"][0]["result"] == client.post("/parse", json=records).json()[0]
    assert response.json()["meta"]["count"] == 1


@pytest.mark.parametrize("content,content_type,status", [(b"{", "application/json", 400), (b"[]", "text/plain", 415), (b"{}", "application/json", 422)])
def test_transport_failures(client, content, content_type, status):
    response = client.post("/parse", content=content, headers={"content-type": content_type})
    assert response.status_code == status


def test_invalid_record_response_is_index_only(client):
    response = client.post("/parse", json=[{"id": "PRIVATE-VALUE", "raw_address": ["PRIVATE-VALUE"]}])
    assert response.status_code == 422
    assert "PRIVATE-VALUE" not in response.text
    assert response.json()["detail"]["index"] == 0


def test_lone_unicode_surrogates_are_rejected_before_parser(client):
    for field in ("id", "raw_address"):
        record = {"id": "x", "raw_address": "valid"}
        record[field] = "\ud800"
        response = client.post("/parse", content=json.dumps([record]), headers={"content-type": "application/json"})
        assert response.status_code == 422
        assert response.json()["detail"] == {"code": "INVALID_UNICODE", "index": 0}


def test_excessive_json_nesting_is_a_safe_bad_request(client):
    response = client.post("/parse", content=b"[" * 10000 + b"]" * 10000, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_JSON"


def test_missing_and_extra_multipart_files_are_rejected(client):
    assert client.post("/parse", files={"other": ("x.json", b"[]")}).status_code == 422
    assert client.post("/parse", files=[("file", ("a", b"[]")), ("file", ("b", b"[]"))]).status_code == 400


def test_body_limit_with_and_without_content_length():
    with TestClient(create_app(parser=engine, max_body_bytes=100)) as client:
        assert client.post("/parse", content=b" " * 101).status_code == 413
        assert client.post("/parse", content=iter([b" " * 60, b" " * 60]), headers={"content-type": "application/json"}).status_code == 413


def test_health_and_embedded_ui(client):
    assert client.get("/health/live").json()["status"] == "alive"
    assert client.get("/health/ready").json()["status"] == "ready"
    assert "JANA ADDRESS" in client.get("/").text
    assert client.get("/src/ui-state.mjs").status_code == 200
    assert client.get("/data/private/official/sample_input.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_broken_engine_does_not_return_partial_or_sensitive_error():
    def broken(*args, **kwargs):
        raise ValueError("PRIVATE-VALUE")
    with TestClient(create_app(parser=broken), raise_server_exceptions=False) as client:
        response = client.post("/parse", json=[{"id": "x", "raw_address": "SECRET"}])
        assert response.status_code == 500
        assert "PRIVATE-VALUE" not in response.text
        assert "SECRET" not in response.text
