"""The HTTP endpoint: every row of the status-code table, over a real socket.

These tests use `urllib.request` against a real `serve(...)` on port 0 because the
callers are plain HTTP clients (curl, MATLAB's webwrite); a handler called directly would
not catch a missing Content-Type or a dropped connection. A helper thread stands in for
the run loop. Every wait has a timeout, so a regression fails instead of hanging the suite.
"""

from __future__ import annotations

import datetime
import http.client
import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from wl_sync.endpoint import serve
from wl_sync.session import SessionId
from wl_sync.sessions import (
    Opened,
    OpenSession,
    SessionRequest,
    SessionsExhausted,
    WrongSession,
    write_open,
)

TOKEN = "s3cret"
WAIT_S = 5.0
SID = SessionId(datetime.date(2026, 10, 18), 2)
OPENED_AT = datetime.datetime(2026, 10, 18, 9, 30, tzinfo=datetime.timezone.utc)


class Rig:
    """A running endpoint plus the stand-in run loop's queue."""

    def __init__(self, root, token=TOKEN, timeout_s=5.0):
        self.root = root
        self.requests: queue.SimpleQueue = queue.SimpleQueue()
        self.server = serve(root, self.requests, token, "127.0.0.1", 0, timeout_s=timeout_s)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.seen: list[SessionRequest] = []
        self._helpers: list[threading.Thread] = []

    def answer(self, outcome):
        """Stand in for the run loop: take one request, resolve it with `outcome`.

        `outcome` is a value, or an exception instance to set as the failure.
        """

        def run():
            request = self.requests.get(timeout=WAIT_S)
            self.seen.append(request)
            assert request.reply.set_running_or_notify_cancel()
            if isinstance(outcome, BaseException):
                request.reply.set_exception(outcome)
            else:
                request.reply.set_result(outcome)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self._helpers.append(thread)

    def call(self, method, path, body=None, token=TOKEN, raw=None, headers=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=WAIT_S) as response:
                return response.status, json.loads(response.read()), response
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), error

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        for thread in self._helpers:
            thread.join(WAIT_S)


@pytest.fixture
def make_rig(tmp_path):
    made = []

    def make(**kwargs):
        rig = Rig(tmp_path, **kwargs)
        made.append(rig)
        return rig

    yield make
    for rig in made:
        rig.close()


@pytest.fixture
def rig(make_rig):
    return make_rig()


def opened(reused=False, closed=None):
    return Opened(OpenSession(SID, OPENED_AT, "r1"), closed, reused)


def test_get_session_with_nothing_open(rig):
    status, body, response = rig.call("GET", "/session", token=None)
    assert status == 200
    assert body == {"session_id": None, "opened_at": None}
    assert response.headers["Content-Type"] == "application/json"


def test_get_session_reads_the_saved_session(rig):
    write_open(rig.root, OpenSession(SID, OPENED_AT, "r1"))
    status, body, _ = rig.call("GET", "/session", token=None)
    assert status == 200
    assert body == {"session_id": "2026-10-18_02", "opened_at": OPENED_AT.isoformat()}


def test_get_session_works_with_no_token_configured(make_rig):
    rig = make_rig(token=None)
    status, _, _ = rig.call("GET", "/session", token=None)
    assert status == 200


def test_open_answers_the_new_session(rig):
    rig.answer(opened(closed=SessionId(datetime.date(2026, 10, 18), 1)))
    status, body, response = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert status == 200
    assert response.headers["Content-Type"] == "application/json"
    assert body == {
        "session_id": "2026-10-18_02",
        "opened_at": OPENED_AT.isoformat(),
        "closed": "2026-10-18_01",
    }
    request = rig.seen[0]
    assert (request.kind, request.argument) == ("open", "r1")


def test_open_with_nothing_displaced_answers_closed_null(rig):
    rig.answer(opened())
    _, body, _ = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert body["closed"] is None


def test_a_reused_open_answers_like_the_original(rig):
    rig.answer(opened(reused=True))
    status, body, _ = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert status == 200
    assert body == {
        "session_id": "2026-10-18_02",
        "opened_at": OPENED_AT.isoformat(),
        "closed": None,
    }


def test_close_answers_the_closed_id(rig):
    rig.answer(SID)
    status, body, _ = rig.call("POST", "/session/close", {"session_id": "2026-10-18_02"})
    assert status == 200
    assert body == {"closed": "2026-10-18_02"}
    request = rig.seen[0]
    assert (request.kind, request.argument) == ("close", "2026-10-18_02")


def test_no_token_configured_refuses_any_post(make_rig):
    rig = make_rig(token=None)
    for path, body in (
        ("/session/open", {"request_id": "r1"}),
        ("/session/close", {"session_id": "2026-10-18_02"}),
    ):
        status, payload, _ = rig.call("POST", path, body, token="anything")
        assert status == 403
        assert "error" in payload
    assert rig.requests.empty()


@pytest.mark.parametrize("token", [None, "wrong", ""])
def test_wrong_or_missing_bearer_is_401(rig, token):
    status, payload, response = rig.call("POST", "/session/open", {"request_id": "r1"}, token=token)
    assert status == 401
    assert "error" in payload
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert rig.requests.empty()


def test_a_non_ascii_bearer_is_401_not_a_crash(rig):
    status, _, _ = rig.call(
        "POST", "/session/open", {"request_id": "r1"}, token=None,
        headers={"Authorization": "Bearer café"},
    )
    assert status == 401


@pytest.mark.parametrize(
    "path,raw",
    [
        ("/session/open", b"{not json"),
        ("/session/open", b"[]"),
        ("/session/open", b"{}"),
        ("/session/open", b'{"request_id": ""}'),
        ("/session/open", b'{"request_id": 7}'),
        ("/session/close", b"{}"),
        ("/session/close", b'{"session_id": 3}'),
        ("/session/close", b'{"session_id": "tomorrow"}'),
    ],
)
def test_bad_json_or_field_is_400_and_never_queued(rig, path, raw):
    status, payload, _ = rig.call("POST", path, raw=raw)
    assert status == 400
    assert "error" in payload
    assert rig.requests.empty()


def test_a_post_without_content_length_is_400(rig):
    host, port = rig.server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=WAIT_S)
    connection.putrequest("POST", "/session/open")
    connection.putheader("Authorization", f"Bearer {TOKEN}")
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 400
    assert "error" in json.loads(response.read())
    connection.close()


def test_an_oversized_body_is_413_without_being_read(rig):
    # Headers only, no body: if the server tried to read 70000 bytes it would hang
    # until the client's timeout instead of answering.
    host, port = rig.server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=WAIT_S)
    connection.putrequest("POST", "/session/open")
    connection.putheader("Authorization", f"Bearer {TOKEN}")
    connection.putheader("Content-Length", "70000")
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 413
    assert response.getheader("Content-Type") == "application/json"
    assert "error" in json.loads(response.read())
    assert rig.requests.empty()
    connection.close()


def test_wrong_session_is_409_and_says_what_is_open(rig):
    rig.answer(WrongSession(SID))
    status, body, _ = rig.call("POST", "/session/close", {"session_id": "2026-10-18_01"})
    assert status == 409
    assert body["open"] == "2026-10-18_02"
    assert "error" in body


def test_wrong_session_with_nothing_open_says_null(rig):
    rig.answer(WrongSession(None))
    status, body, _ = rig.call("POST", "/session/close", {"session_id": "2026-10-18_01"})
    assert status == 409
    assert body["open"] is None


def test_sessions_exhausted_is_409(rig):
    rig.answer(SessionsExhausted("2026-10-18 already has 99 sessions"))
    status, body, _ = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert status == 409
    assert "99" in body["error"]
    assert "open" not in body


def test_a_value_error_from_the_run_loop_is_400(rig):
    rig.answer(ValueError("bad id"))
    status, body, _ = rig.call("POST", "/session/close", {"session_id": "2026-10-18_01"})
    assert status == 400
    assert body["error"] == "bad id"


@pytest.mark.parametrize("error", [OSError("disk full"), RuntimeError("roll failed"), KeyError("x")])
def test_any_other_failure_is_500_json(rig, error):
    rig.answer(error)
    status, body, response = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert status == 500
    assert "error" in body
    assert response.headers["Content-Type"] == "application/json"


def test_an_unanswered_request_is_503_and_cancelled(make_rig):
    rig = make_rig(timeout_s=0.2)
    status, body, _ = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert status == 503
    assert "error" in body
    request = rig.requests.get(timeout=WAIT_S)
    assert request.reply.cancelled()


def test_a_started_request_is_waited_for_past_the_timeout(make_rig):
    rig = make_rig(timeout_s=0.2)
    started = threading.Event()

    def slow_loop():
        request = rig.requests.get(timeout=WAIT_S)
        assert request.reply.set_running_or_notify_cancel()
        started.set()
        threading.Event().wait(0.6)
        request.reply.set_result(opened())

    thread = threading.Thread(target=slow_loop, daemon=True)
    thread.start()
    status, body, _ = rig.call("POST", "/session/open", {"request_id": "r1"})
    thread.join(WAIT_S)
    assert started.is_set()
    assert status == 200
    assert body["session_id"] == "2026-10-18_02"


def test_unknown_path_is_404(rig):
    for method in ("GET", "POST"):
        status, body, _ = rig.call(method, "/nope", {} if method == "POST" else None)
        assert status == 404
        assert "error" in body


def test_known_path_wrong_method_is_405_with_allow(rig):
    status, _, response = rig.call("GET", "/session/open", token=None)
    assert status == 405
    assert response.headers["Allow"] == "POST"
    status, _, response = rig.call("POST", "/session", {})
    assert status == 405
    assert response.headers["Allow"] == "GET"


def test_check_order_is_route_then_403(make_rig):
    rig = make_rig(token=None)
    status, _, _ = rig.call("POST", "/nope", raw=b"{bad", token=None)
    assert status == 404
    status, _, _ = rig.call("POST", "/session/open", raw=b"{bad", token=None)
    assert status == 403


def test_auth_is_checked_before_the_body(rig):
    status, _, _ = rig.call("POST", "/session/open", raw=b"{bad", token="wrong")
    assert status == 401


def test_the_server_uses_daemon_threads(rig):
    assert rig.server.daemon_threads is True


@pytest.mark.parametrize("header", [f"bearer {TOKEN}", f"BEARER {TOKEN}", f"Bearer  {TOKEN}"])
def test_scheme_case_and_extra_spaces_are_accepted(rig, header):
    rig.answer(opened())
    status, _, _ = rig.call(
        "POST", "/session/open", {"request_id": "r1"}, token=None, headers={"Authorization": header}
    )
    assert status == 200


def test_a_wrong_token_is_still_refused_with_loose_parsing(rig):
    status, _, _ = rig.call(
        "POST", "/session/open", {"request_id": "r1"}, token=None,
        headers={"Authorization": "bearer  nope"},
    )
    assert status == 401


def test_a_non_ascii_token_matches_its_utf8_bytes(make_rig):
    rig = make_rig(token="caf\u00e9")
    rig.answer(opened())
    host, port = rig.server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=WAIT_S)
    body = b'{"request_id": "r1"}'
    connection.putrequest("POST", "/session/open")
    connection.putheader("Authorization", b"Bearer caf\xc3\xa9")
    connection.putheader("Content-Length", str(len(body)))
    connection.endheaders(body)
    assert connection.getresponse().status == 200
    connection.close()


def test_a_trailing_newline_in_a_close_id_is_canonicalised(rig):
    rig.answer(SID)
    status, _, _ = rig.call("POST", "/session/close", {"session_id": "2026-10-18_01\n"})
    assert status == 200
    assert rig.seen[0].argument == "2026-10-18_01"


def test_the_stdlib_error_paths_are_json_too(rig):
    host, port = rig.server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=WAIT_S)
    connection.request("OPTIONS", "/session")
    response = connection.getresponse()
    assert response.status == 501
    assert response.getheader("Content-Type") == "application/json"
    assert "error" in json.loads(response.read())
    connection.close()


def test_too_many_headers_gets_a_json_error(rig):
    import socket

    host, port = rig.server.server_address[:2]
    with socket.create_connection((host, port), timeout=WAIT_S) as sock:
        sock.sendall(b"GET /session HTTP/1.1\r\n" + b"X-A: b\r\n" * 150 + b"\r\n")
        data = b""
        while chunk := sock.recv(4096):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    assert b"application/json" in head
    assert head.startswith(b"HTTP/1.")
    assert b" 431 " in head.split(b"\r\n")[0]
    assert "error" in json.loads(body)


def test_deeply_nested_json_is_400_not_500(rig):
    status, payload, _ = rig.call("POST", "/session/open", raw=b"[" * 50000)
    assert status == 400
    assert "error" in payload
