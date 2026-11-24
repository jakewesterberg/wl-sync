"""hardware/breakout/bakeoff/deeppcb_client.py against a local fake of DeepPCB's API
(task R1c, 2026-11-24). No network: the fake listens on 127.0.0.1, and every request the
client makes is recorded so the tests can say what it sent and what it did not."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from hardware.breakout.bakeoff import deeppcb_client as dc

KEY = "sk-test-0123456789abcdef"


class Fake:
    """DeepPCB as the plugin sees it: canned replies per (method, path), every request kept."""

    def __init__(self):
        self.requests = []
        self.routes = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"

    def on(self, method, path, status=200, body=b"", ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.routes[(method, path)] = (status, body, ctype)

    def sent(self, method=None):
        return [r for r in self.requests if method is None or r["method"] == method]

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                u = urlparse(self.path)
                fake.requests.append({"method": self.command, "path": u.path,
                                      "query": parse_qs(u.query), "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": self.rfile.read(n) if n else b""})
                status, body, ctype = fake.routes.get(
                    (self.command, u.path.removeprefix("/api/v1")),
                    (404, b'{"errorMessage": "no such route"}', "application/json"))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PATCH = do_PUT = _any
        return H


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.server.shutdown()


@pytest.fixture
def client(fake):
    return dc.Client(KEY, base_url=fake.url, sleep=lambda s: None)


BOARD = {"boardStatus": "Pending", "boardPId": "p1", "requiresCredits": True,
         "creditsCostPerMinute": 2.5,
         "workflow": {"jobType": "Routing", "startedOn": None, "workflowTimeOut": 45},
         "workflows": [{"jobType": "Routing", "revisions": [{"revisionNumber": 1},
                                                            {"revisionNumber": 3}]}]}


def run(capsys, client, *argv):
    code = dc.main(list(argv), client=client)
    out = capsys.readouterr()
    assert KEY not in out.out and KEY not in out.err
    return code, out.out, out.err


def test_defaults_are_the_plugins(monkeypatch):
    monkeypatch.delenv("DEEPPCB_BASE_URL", raising=False)
    assert dc.API_URL == "https://api.deeppcb.ai/api/v1"
    assert dc.KEY_FILE == Path.home() / ".config" / "deeppcb" / "api_key"
    assert dc.Client(KEY).base == dc.API_URL


def test_balance_sends_the_key_in_its_header_and_never_prints_it(fake, client, capsys):
    fake.on("GET", "/apiuser/credit-flow", body={"balance": 12.5})
    code, out, _ = run(capsys, client, "balance")
    assert code == 0 and out.strip() == "balance: 12.5"
    (r,) = fake.sent()
    assert r["path"] == "/api/v1/apiuser/credit-flow"
    assert r["headers"]["x-deeppcb-api-key"] == KEY
    assert "KicadPlugin" not in json.dumps(r["headers"])     # we do not pose as the plugin


def test_create_uploads_board_and_project_only_and_waits_for_pending(fake, client, tmp_path, capsys):
    pcb, pro = tmp_path / "breakout.kicad_pcb", tmp_path / "breakout.kicad_pro"
    pcb.write_text("(kicad_pcb)")
    pro.write_text("{}")
    (tmp_path / "breakout.kicad_dru").write_text("(version 1)")
    fake.on("POST", "/boards", body=b'"board-123"', ctype="text/plain")
    fake.on("GET", "/boards/board-123", body=BOARD)
    code, out, _ = run(capsys, client, "create", "--pcb", str(pcb), "--pro", str(pro),
                       "--name", "ni-island")
    assert code == 0 and json.loads(out)["board_id"] == "board-123"
    post = fake.sent("POST")[0]
    assert post["headers"]["content-type"].startswith("multipart/form-data; boundary=")
    body = post["body"]
    for field, value in (("routingType", b"CurrentUnprotectedWiring"),
                         ("boardInputType", b"Kicad"), ("boardName", b"ni-island"),
                         ("batch", b"1")):
        assert f'name="{field}"\r\n\r\n'.encode() + value in body
    assert b'name="kicadBoardFile"; filename="breakout.kicad_pcb"' in body
    assert b'name="kicadProjectFile"; filename="breakout.kicad_pro"' in body
    assert b"kicad_dru" not in body and b"(kicad_pcb)" in body
    assert not fake.sent("PATCH") and not fake.sent("PUT")


def test_cost_reads_and_never_submits(fake, client, capsys):
    fake.on("GET", "/boards/b1", body=BOARD)
    code, out, _ = run(capsys, client, "cost", "b1", "--timeout", "30")
    c = json.loads(out)
    assert code == 0 and c["requires_credits"] is True
    assert c["credits_cost_per_minute"] == 2.5 and c["cost"] == 75.0
    assert {r["method"] for r in fake.sent()} == {"GET"}


def test_cost_is_zero_when_no_credits_are_required(fake, client, capsys):
    fake.on("GET", "/boards/b1", body=dict(BOARD, requiresCredits=False))
    assert json.loads(run(capsys, client, "cost", "b1", "--timeout", "30")[1])["cost"] == 0.0


def test_submit_without_yes_sends_nothing(fake, client, capsys):
    code, out, _ = run(capsys, client, "submit", "b1", "--timeout", "30")
    assert code == 2 and "not submitted" in out
    assert fake.sent() == []


def test_submit_patches_confirm_with_the_plugins_payload(fake, client, capsys):
    fake.on("GET", "/boards/b1", body=BOARD)
    fake.on("GET", "/apiuser/credit-flow", body={"balance": 100})
    fake.on("GET", "/boards/b1/recommended-max-batch-timeout", body=b"12")
    fake.on("PATCH", "/boards/b1/confirm", body=b"")
    code, out, _ = run(capsys, client, "submit", "b1", "--timeout", "30", "--yes")
    assert code == 0
    (p,) = fake.sent("PATCH")
    assert p["path"] == "/api/v1/boards/b1/confirm"
    assert p["headers"]["content-type"] == "application/json-patch+json"
    assert json.loads(p["body"]) == {"timeout": 30, "maxBatchTimeout": 12, "timeToLive": 300,
                                     "jobType": "Routing"}
    assert json.loads(out)["cost"] == 75.0


def test_submit_refuses_when_the_balance_is_short(fake, client):
    fake.on("GET", "/boards/b1", body=BOARD)
    fake.on("GET", "/apiuser/credit-flow", body={"balance": 10})
    with pytest.raises(SystemExit, match="costs 75 credits and the balance is 10"):
        dc.main(["submit", "b1", "--timeout", "30", "--yes"], client=client)
    assert not fake.sent("PATCH")


def test_submit_falls_back_to_the_default_batch_timeout(fake, client, capsys):
    fake.on("GET", "/boards/b1", body=dict(BOARD, requiresCredits=False))
    fake.on("PATCH", "/boards/b1/confirm", body=b"")
    assert run(capsys, client, "submit", "b1", "--timeout", "5", "--yes")[0] == 0
    assert json.loads(fake.sent("PATCH")[0]["body"])["maxBatchTimeout"] == 30


def test_status_lists_revisions(fake, client, capsys):
    fake.on("GET", "/boards/b1", body=BOARD)
    s = json.loads(run(capsys, client, "status", "b1")[1])
    assert s["board_status"] == "Pending" and s["revisions"] == [1, 3]
    assert s["timeout_min"] == 45


def test_download_takes_the_latest_revision_by_default(fake, client, tmp_path, capsys):
    fake.on("GET", "/boards/b1", body=BOARD)
    fake.on("GET", "/boards/b1/revision-artifact", body=b"(kicad_pcb routed)", ctype="text/plain")
    out = tmp_path / "results" / "deeppcb" / "breakout.kicad_pcb"
    code, text, _ = run(capsys, client, "download", "b1", "--out", str(out))
    assert code == 0 and out.read_bytes() == b"(kicad_pcb routed)"
    r = fake.sent("GET")[-1]
    assert r["query"] == {"revision": ["3"], "type": ["KicadFile"], "revisionType": ["Tidy"]}


def test_an_error_that_echoes_the_key_is_redacted(fake, client, capsys):
    fake.on("GET", "/apiuser/credit-flow", status=401,
            body={"errorMessage": f"invalid key {KEY}"})
    code, out, err = run(capsys, client, "balance")
    assert code == 1 and "HTTP 401" in err and "invalid key [redacted]" in err


def test_read_key_strips_and_refuses_a_missing_or_empty_file(tmp_path):
    p = tmp_path / "api_key"
    p.write_text(KEY + "\n")
    assert dc.read_key(p) == KEY
    p.write_text("\n")
    with pytest.raises(SystemExit, match="is empty"):
        dc.read_key(p)
    with pytest.raises(SystemExit, match="does not exist"):
        dc.read_key(tmp_path / "missing")
