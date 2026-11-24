"""A command-line client for DeepPCB's routing API (task R1c, 2026-11-24).

    python hardware/breakout/bakeoff/deeppcb_client.py balance
    python hardware/breakout/bakeoff/deeppcb_client.py create --pcb P.kicad_pcb --pro P.kicad_pro --name NAME
    python hardware/breakout/bakeoff/deeppcb_client.py cost BOARD_ID --timeout MINUTES
    python hardware/breakout/bakeoff/deeppcb_client.py submit BOARD_ID --timeout MINUTES --yes
    python hardware/breakout/bakeoff/deeppcb_client.py status BOARD_ID
    python hardware/breakout/bakeoff/deeppcb_client.py download BOARD_ID [--revision N] --out FILE

Standard library only. Every endpoint, field and parameter is read from DeepPCB's own KiCad
plugin (github.com/instadeepai/deeppcb-kicad-plugin at 2139843, 2026-11-06):
helpers/http_helpers.py, helpers/contracts.py, helpers/routing_jobs.py, config.py and
panels/board_creation_panel.py.

- Base URL https://api.deeppcb.ai/api/v1 (config.API_URL); the key goes in the header
  x-deeppcb-api-key (http_helpers.build_headers).
- create: POST /boards, multipart, with the form fields the plugin sends
  (http_helpers.create_board) and the files kicadBoardFile and kicadProjectFile. Only the
  .kicad_pcb and the .kicad_pro go up: the plugin sends no .kicad_dru, so DeepPCB never sees
  the custom rules. The reply is the board id. The plugin then waits for the board to reach
  "Pending" (board_creation_panel.py), as this does.
- cost: GET /boards/{id}: requiresCredits and creditsCostPerMinute (contracts.DeepPCBBoard);
  the cost of a run is creditsCostPerMinute x timeout in minutes when requiresCredits
  (routing_jobs.start_routing_job). Reads only.
- submit: PATCH /boards/{id}/confirm (the plugin uses PATCH, not PUT), JSON body
  {timeout, maxBatchTimeout, timeToLive: 300, jobType: "Routing"}, with maxBatchTimeout from
  GET /boards/{id}/recommended-max-batch-timeout (default 30). The timeout is in minutes
  (the plugin's slider runs 5 to 120, default 45). As the plugin does, it first checks the
  balance against the cost and refuses when it is short. It also refuses without --yes:
  submitting spends credits.
- status: GET /boards/{id}.
- download: GET /boards/{id}/revision-artifact?revision=N&type=KicadFile&revisionType=Tidy
  (utils.download_and_save_board), saved as text.
- balance: GET /apiuser/credit-flow, whose JSON has "balance".

The key is read from ~/.config/deeppcb/api_key (or the file DEEPPCB_API_KEY_FILE names). It
must be one printable token with no whitespace inside (a key file with a comment line is
refused, without quoting it: http.client would otherwise quote the whole value in a
ValueError). It is never printed or logged: main() catches every exception and prints only
its message, with the key and every piece of it 8 characters or longer replaced by
[redacted], and no traceback.
DEEPPCB_BASE_URL overrides the base URL; the tests point it at a local fake server.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API_URL = "https://api.deeppcb.ai/api/v1"
KEY_FILE = Path.home() / ".config" / "deeppcb" / "api_key"
USER_AGENT = "wl-sync-bakeoff/1"
REQUEST_TIMEOUT = 60          # seconds per HTTP request (the plugin's FILES_TIMEOUT)
PENDING_WAIT = 90             # seconds to wait for a created board to reach Pending
DEFAULT_MAX_BATCH_TIMEOUT = 30
REDACT_MIN = 8                # every piece of the key this long or longer is redacted
_TOKEN = re.compile(r"[\x21-\x7e]+")   # printable ASCII, no whitespace
TIME_TO_LIVE = 300
JOB_TYPE = "Routing"
ROUTING_TYPE = "CurrentUnprotectedWiring"
BOARD_INPUT_TYPE = "Kicad"


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def read_key(path: Path | None = None) -> str:
    p = Path(path or os.environ.get("DEEPPCB_API_KEY_FILE") or KEY_FILE)
    try:
        key = p.read_text().strip()
    except FileNotFoundError:
        raise SystemExit(f"no DeepPCB API key: {p} does not exist") from None
    if not key:
        raise SystemExit(f"no DeepPCB API key: {p} is empty")
    if not _TOKEN.fullmatch(key):
        raise SystemExit(f"no DeepPCB API key: {p} must hold one printable token and nothing "
                         "else (no spaces, comments or further lines); its content is not shown")
    return key


class Client:
    def __init__(self, key: str, base_url: str | None = None, sleep=time.sleep):
        self._key = key
        self.base = (base_url or os.environ.get("DEEPPCB_BASE_URL") or API_URL).rstrip("/")
        self.sleep = sleep

    # -- plumbing -------------------------------------------------------------------------
    def redact(self, text: str) -> str:
        return redact(text, self._key)

    def request(self, method: str, path: str, *, params: dict | None = None,
                body: bytes | None = None, content_type: str | None = None) -> bytes:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"accept": "*/*", "x-deeppcb-api-key": self._key, "User-Agent": USER_AGENT}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "errorMessage" in data:
                    text = str(data["errorMessage"])
            except ValueError:
                pass
            raise ApiError(e.code, self.redact(text)) from None
        except urllib.error.URLError as e:
            raise ApiError(0, self.redact(f"connection failed: {e.reason}")) from None

    def get_json(self, path: str, **kw):
        return json.loads(self.request("GET", path, **kw) or b"null")

    # -- the API ----------------------------------------------------------------------------
    def balance(self) -> float:
        data = self.get_json("/apiuser/credit-flow")
        if not isinstance(data, dict) or data.get("balance") is None:
            raise ApiError(200, "credit-flow reply has no balance")
        return float(data["balance"])

    def board(self, board_id: str) -> dict:
        return self.get_json(f"/boards/{urllib.parse.quote(board_id)}")

    def create(self, pcb: Path, pro: Path, name: str, request_id: str | None = None) -> str:
        fields = {  # http_helpers.create_board, field for field
            "routingType": ROUTING_TYPE, "webhookToken": "webhook_token",
            "requestId": request_id or str(uuid.uuid4()), "jsonFileUrl": "",
            "boardName": name, "webhookUrl": "https://plugin.kicad.deeppcb.ai",
            "batch": "1", "dsnFile": "", "boardInputType": BOARD_INPUT_TYPE,
        }
        files = [("kicadBoardFile", pcb), ("kicadProjectFile", pro)]
        body, ctype = multipart(fields, files)
        board_id = self.request("POST", "/boards", body=body, content_type=ctype).decode()
        board_id = board_id.strip().strip('"')
        if not board_id:
            raise ApiError(200, "create returned no board id")
        return board_id

    def wait_for(self, board_id: str, status: str, seconds: float) -> dict:
        t0 = time.monotonic()
        while True:
            b = self.board(board_id)
            if b.get("boardStatus") == status or time.monotonic() - t0 >= seconds:
                return b
            self.sleep(2)

    def cost(self, board_id: str, timeout_min: int) -> dict:
        b = self.board(board_id)
        needs = bool(b.get("requiresCredits", False))
        rate = float(b.get("creditsCostPerMinute", 0) or 0)
        return {"board_status": b.get("boardStatus", "Unknown"), "requires_credits": needs,
                "credits_cost_per_minute": rate, "timeout_min": timeout_min,
                "cost": rate * timeout_min if needs else 0.0}

    def max_batch_timeout(self, board_id: str) -> int:
        try:
            v = int(self.get_json(f"/boards/{urllib.parse.quote(board_id)}"
                                  "/recommended-max-batch-timeout"))
        except (ApiError, TypeError, ValueError):
            return DEFAULT_MAX_BATCH_TIMEOUT
        return v if v > 0 else DEFAULT_MAX_BATCH_TIMEOUT

    def submit(self, board_id: str, timeout_min: int) -> dict:
        c = self.cost(board_id, timeout_min)
        if c["requires_credits"]:
            bal = self.balance()
            c["balance"] = bal
            if bal < c["cost"]:
                raise SystemExit(f"not submitted: the run costs {c['cost']:g} credits and the "
                                 f"balance is {bal:g}")
        payload = {"timeout": timeout_min, "maxBatchTimeout": self.max_batch_timeout(board_id),
                   "timeToLive": TIME_TO_LIVE, "jobType": JOB_TYPE}
        self.request("PATCH", f"/boards/{urllib.parse.quote(board_id)}/confirm",
                     body=json.dumps(payload).encode(),
                     content_type="application/json-patch+json")
        c["submitted"] = payload
        return c

    def download(self, board_id: str, revision: int | None, out: Path) -> int:
        if revision is None:
            revs = revisions(self.board(board_id))
            if not revs:
                raise SystemExit("no revision to download yet")
            revision = revs[-1]
        data = self.request("GET", f"/boards/{urllib.parse.quote(board_id)}/revision-artifact",
                            params={"revision": revision, "type": "KicadFile",
                                    "revisionType": "Tidy"})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return revision


def redact(text: str, key: str) -> str:
    """`text` with `key`, and every substring of it REDACT_MIN characters or longer,
    replaced by [redacted], longest first."""
    if not key:
        return text
    n = len(key)
    for length in range(n, min(REDACT_MIN, n) - 1, -1):
        for i in range(n - length + 1):
            piece = key[i:i + length]
            if piece in text:
                text = text.replace(piece, "[redacted]")
    return text


def revisions(board: dict) -> list[int]:
    """Every revision number the board lists, ascending; the last is the latest. A revision
    without a number is left out."""
    return sorted({r["revisionNumber"] for w in board.get("workflows") or []
                   for r in w.get("revisions") or [] if r.get("revisionNumber") is not None})


def multipart(fields: dict, files: list) -> tuple[bytes, str]:
    boundary = "----wlsync" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out += [f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(),
                str(v).encode(), b"\r\n"]
    for k, p in files:
        p = Path(p)
        out += [f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{k}"; filename="{p.name}"\r\n'.encode(),
                b"Content-Type: application/octet-stream\r\n\r\n", p.read_bytes(), b"\r\n"]
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def summary(board: dict) -> dict:
    wf = board.get("workflow") or {}
    return {"board_status": board.get("boardStatus", "Unknown"),
            "requires_credits": board.get("requiresCredits", False),
            "credits_cost_per_minute": board.get("creditsCostPerMinute", 0),
            "job_type": wf.get("jobType"), "started_on": wf.get("startedOn"),
            "timeout_min": wf.get("workflowTimeOut"), "revisions": revisions(board)}


def main(argv=None, client: Client | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("balance", help="the credit balance (reads only)")
    c = sub.add_parser("create", help="upload a board and project (spends nothing)")
    c.add_argument("--pcb", type=Path, required=True)
    c.add_argument("--pro", type=Path, required=True)
    c.add_argument("--name", required=True)
    for name, h in (("cost", "what a run would cost (reads only)"),
                    ("submit", "start a routing run (spends credits)")):
        s = sub.add_parser(name, help=h)
        s.add_argument("board_id")
        s.add_argument("--timeout", type=int, required=True, help="minutes (plugin: 5 to 120)")
        if name == "submit":
            s.add_argument("--yes", action="store_true", help="confirm spending credits")
    s = sub.add_parser("status", help="the board's status and revisions (reads only)")
    s.add_argument("board_id")
    d = sub.add_parser("download", help="save a revision's KiCad board")
    d.add_argument("board_id")
    d.add_argument("--revision", type=int)
    d.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)

    if a.cmd == "submit" and not a.yes:
        print("not submitted: submitting spends credits; run `cost` first, then add --yes")
        return 2
    cl = None
    try:
        cl = client or Client(read_key())
        if a.cmd == "balance":
            print(f"balance: {cl.balance():g}")
        elif a.cmd == "create":
            board_id = cl.create(a.pcb, a.pro, a.name)
            b = cl.wait_for(board_id, "Pending", PENDING_WAIT)
            print(json.dumps({"board_id": board_id, **summary(b)}, indent=1))
        elif a.cmd == "cost":
            print(json.dumps(cl.cost(a.board_id, a.timeout), indent=1))
        elif a.cmd == "submit":
            print(json.dumps(cl.submit(a.board_id, a.timeout), indent=1))
        elif a.cmd == "status":
            print(json.dumps(summary(cl.board(a.board_id)), indent=1))
        elif a.cmd == "download":
            rev = cl.download(a.board_id, a.revision, a.out)
            print(f"revision {rev} saved to {a.out}")
    except SystemExit as e:
        if isinstance(e.code, str):          # read_key's and submit's refusals
            print(cl.redact(e.code) if cl else e.code, file=sys.stderr)
            return 1
        raise
    except BaseException as e:               # anything, KeyboardInterrupt included
        msg = f"{type(e).__name__}: {e}"
        print(f"deeppcb {a.cmd} failed: {cl.redact(msg) if cl else msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
