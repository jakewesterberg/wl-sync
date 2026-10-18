"""The HTTP endpoint through which the task PC opens, closes and reads sessions.

A small JSON service inside the recording process, standard library only. Its callers
are wl-xcon (Python), MonkeyLogic through MATLAB's `webwrite`, `curl` and PowerShell, so
it stays plain: JSON bodies, `Authorization: Bearer <token>`, no client library, and
EVERY response -- errors included -- is `application/json`. A caller that gets an HTML
error page or a dropped connection cannot tell "refused" from "box is down".

THE ENDPOINT NEVER TOUCHES THE SESSION STATE FILE TO CHANGE IT. An open or a close is
handed to the run loop as a `SessionRequest` on a queue, and the handler thread waits on
the request's future. The run loop owns the recording, so only it may decide where the
next segment goes; a handler that wrote `.wl-sync-session` itself could move the session
under a segment already being written. `GET /session` is the one exception, because it
only reads, and `read_open` never raises.

THE ORDER OF CHECKS ON A POST is route (404 / 405), token configured (403), bearer token
(401), body (400 / 413), then queue and wait. Each check comes before the next because
each answer must not leak what a later one would reveal: an unauthenticated caller learns
nothing about what a valid body looks like, and a bad request never reaches the run loop.

THE WAIT HAS ONE SHARP EDGE. When the run loop does not answer within `timeout_s`, the
handler cancels the future. A successful cancel means the loop never started it, so a 503
is true: nothing happened. A FAILED cancel means the loop has already started it and WILL
finish it (it never abandons a started future), so answering 503 would tell the task PC
"nothing happened" about a session that was in fact opened. In that case the handler
waits for the result with no further bound and answers from it.
"""

from __future__ import annotations

import concurrent.futures
import hmac
import json
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from wl_sync.session import SessionId
from wl_sync.sessions import (
    Opened,
    SessionRequest,
    SessionsExhausted,
    WrongSession,
    read_open,
)

logger = logging.getLogger(__name__)

# A body here is one short JSON object. The cap stops a stray or hostile client from
# making a handler thread buffer megabytes on a box whose job is recording.
MAX_BODY_BYTES = 64 * 1024

# A client that connects and then says nothing would otherwise hold its thread forever.
_SOCKET_TIMEOUT_S = 30.0


class _Reply(Exception):
    """An answer decided before or instead of normal completion; carries the response."""

    def __init__(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None):
        super().__init__(status)
        self.status = status
        self.payload = payload
        self.headers = headers or {}


@dataclass(frozen=True, slots=True)
class _Route:
    """One entry of the route table.

    `mutating` routes need the shared token and a JSON body; read-only ones are open to
    anyone on the rig network (spec 3.5: `GET /session` is "anyone").
    """

    handle: Callable[[_Handler, dict[str, Any] | None], tuple[int, dict[str, Any]]]
    mutating: bool


class _Server(ThreadingHTTPServer):
    """The server, carrying what handlers need.

    `daemon_threads` so a handler stuck in a wait cannot keep the process alive past a
    shutdown: the box must be killable by systemd without a hung request vetoing it.
    """

    daemon_threads = True

    root: Path
    requests: queue.SimpleQueue
    token: str | None
    timeout_s: float


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    timeout = _SOCKET_TIMEOUT_S

    # ---- plumbing -------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        # The base class writes to stderr, which under systemd is the journal at the
        # wrong level and mixed with the recording's own output.
        logger.debug("%s - %s", self.address_string(), format % args)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """The stdlib's own error paths (bad request line, 414, 431, 408, 501) in JSON.

        Otherwise they answer in HTML, and a MATLAB or PowerShell caller that parses
        every body as JSON would fail with a confusing parse error instead of the cause.
        """
        if message is None:
            message = self.responses.get(code, ("error",))[0]
        # A request line that did not parse has no `command`; HEAD gets headers only.
        if getattr(self, "command", None) == "HEAD":
            body = b""
        else:
            body = json.dumps({"error": message}).encode()
        try:
            self.send_response(code, message)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            pass
        self.close_connection = True

    def _send(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            # The caller hung up before reading the answer. The work (if any) is done
            # and cannot be undone; raising would only spam a traceback.
            logger.debug("client %s went away before the response was sent", self.address_string())
            self.close_connection = True

    def _dispatch(self, method: str) -> None:
        try:
            status, payload = self._respond(method)
            self._send(status, payload)
        except _Reply as reply:
            self._send(reply.status, reply.payload, reply.headers)
        except ConnectionError:
            self.close_connection = True
        except Exception as error:
            # A bug here must still answer in JSON; a dropped connection tells the task
            # PC nothing and invites a retry of something that may have succeeded.
            logger.exception("endpoint failed on %s %s", method, self.path)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"internal error: {error}"})

    def _respond(self, method: str) -> tuple[int, dict[str, Any]]:
        path = self.path.split("?", 1)[0]
        route = ROUTES.get((method, path))
        if route is None:
            allowed = sorted(m for (m, p) in ROUTES if p == path)
            if allowed:
                raise _Reply(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": f"{method} not allowed on {path}"},
                    {"Allow": ", ".join(allowed)},
                )
            raise _Reply(HTTPStatus.NOT_FOUND, {"error": f"no such path: {path}"})
        if not route.mutating:
            return route.handle(self, None)
        self._require_token()
        return route.handle(self, self._read_json_object())

    # ---- checks, in the order the module docstring gives ------------------------

    def _require_token(self) -> None:
        expected = self.server.token
        if not expected:
            raise _Reply(
                HTTPStatus.FORBIDDEN,
                {"error": "this box has no token configured, so it refuses opens and closes"},
            )
        header = self.headers.get("Authorization", "")
        # RFC 7235: the scheme is case-insensitive and may be followed by any run of
        # spaces, so `bearer  tok` is as valid as `Bearer tok`.
        scheme, _, presented = header.strip().partition(" ")
        presented = presented.strip()
        # Bytes, not str: compare_digest raises TypeError on a non-ASCII str, which
        # would turn a hostile header into a 500 instead of a 401. `http.client` decodes
        # header values as latin-1, so latin-1 gives back the bytes the client sent,
        # which a UTF-8 token is configured as; re-encoding as UTF-8 would never match.
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.encode("latin-1"), expected.encode("utf-8")
        ):
            raise _Reply(
                HTTPStatus.UNAUTHORIZED,
                {"error": "missing or wrong bearer token"},
                {"WWW-Authenticate": "Bearer"},
            )

    def _read_json_object(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length")
        if length_text is None or not length_text.isascii() or not length_text.isdigit():
            raise _Reply(HTTPStatus.BAD_REQUEST, {"error": "a valid Content-Length is required"})
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            # Not read: the point of the cap is to not buffer it. Close the connection
            # so the unread bytes are not parsed as the next request.
            self.close_connection = True
            raise _Reply(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"body is {length} bytes; the limit is {MAX_BODY_BYTES}"},
                {"Connection": "close"},
            )
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError) as error:
            # ValueError includes UnicodeDecodeError. RecursionError is deeply nested
            # input such as 100000 `[`; it is a bad body, not a server fault.
            raise _Reply(
                HTTPStatus.BAD_REQUEST, {"error": f"body is not valid JSON: {type(error).__name__}"}
            ) from None
        if not isinstance(body, dict):
            raise _Reply(HTTPStatus.BAD_REQUEST, {"error": "body must be a JSON object"})
        return body

    # ---- verbs ------------------------------------------------------------------

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    # Other verbs get the same JSON 404/405 instead of the base class's HTML 501.
    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    # ---- talking to the run loop --------------------------------------------------

    def ask_run_loop(self, kind: str, argument: str) -> Any:
        """Queue a request and return the run loop's result, or raise its failure.

        Raises `_Reply` 503 only when the loop never started the request.
        """
        reply: concurrent.futures.Future = concurrent.futures.Future()
        self.server.requests.put(SessionRequest(kind, argument, reply))  # type: ignore[arg-type]
        done, _ = concurrent.futures.wait([reply], timeout=self.server.timeout_s)
        if not done and reply.cancel():
            raise _Reply(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "the recording loop did not take the request in time; nothing was changed"},
            )
        # Either it finished in time, or the cancel failed because the loop already
        # started it -- which it always completes. Wait with no further bound.
        return reply.result()


# ---- routes ---------------------------------------------------------------------


def _get_session(handler: _Handler, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    current = read_open(handler.server.root)
    if current is None:
        return 200, {"session_id": None, "opened_at": None}
    return 200, {"session_id": str(current.session_id), "opened_at": current.opened_at.isoformat()}


def _call(handler: _Handler, kind: str, argument: str) -> tuple[Any, tuple[int, dict[str, Any]] | None]:
    """Run a request through the run loop; map its refusals to responses.

    Returns `(result, None)` on success, `(None, (status, payload))` on a failure.
    """
    try:
        return handler.ask_run_loop(kind, argument), None
    except _Reply:
        # The 503: already a finished answer, not a run-loop failure to log as a 500.
        raise
    except WrongSession as error:
        open_id = None if error.open is None else str(error.open)
        return None, (409, {"error": str(error), "open": open_id})
    except SessionsExhausted as error:
        return None, (409, {"error": str(error)})
    except ValueError as error:
        return None, (400, {"error": str(error)})
    except Exception as error:
        # OSError, a failed roll (RuntimeError), or anything else. The table has no row
        # for these, and a caller must still get an answer rather than a dropped socket.
        logger.exception("run loop failed a session %s request", kind)
        return None, (500, {"error": f"{type(error).__name__}: {error}"})


def _post_open(handler: _Handler, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    assert body is not None
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise _Reply(HTTPStatus.BAD_REQUEST, {"error": "request_id must be a non-empty string"})
    result, failure = _call(handler, "open", request_id)
    if failure is not None:
        return failure
    opened: Opened = result
    # A reused open answers exactly like the original, so `closed` is whatever the run
    # loop reports, not something derived here.
    return 200, {
        "session_id": str(opened.session.session_id),
        "opened_at": opened.session.opened_at.isoformat(),
        "closed": None if opened.closed is None else str(opened.closed),
    }


def _post_close(handler: _Handler, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    assert body is not None
    session_id = body.get("session_id")
    if not isinstance(session_id, str):
        raise _Reply(HTTPStatus.BAD_REQUEST, {"error": "session_id must be a string"})
    try:
        canonical = str(SessionId.parse(session_id))
    except ValueError as error:
        raise _Reply(HTTPStatus.BAD_REQUEST, {"error": str(error)}) from None
    result, failure = _call(handler, "close", canonical)
    if failure is not None:
        return failure
    return 200, {"closed": str(result)}


# A table, not a chain of `if`s: the reward-delivered record (spec 2) is one more entry.
ROUTES: dict[tuple[str, str], _Route] = {
    ("GET", "/session"): _Route(_get_session, mutating=False),
    ("POST", "/session/open"): _Route(_post_open, mutating=True),
    ("POST", "/session/close"): _Route(_post_close, mutating=True),
}


def serve(
    root: Path,
    requests: queue.SimpleQueue,
    token: str | None,
    host: str,
    port: int,
    timeout_s: float = 5.0,
) -> ThreadingHTTPServer:
    """Bind, start serving on a daemon thread, and return the server.

    The socket is bound and listening when this returns, so a caller can read
    `server.server_address` (port 0 picks a free one) and connect at once. With
    `token=None` the server answers `GET` only. Stop it with `shutdown()` then
    `server_close()`.
    """
    server = _Server((host, port), _Handler)
    server.root = root
    server.requests = requests
    server.token = token
    server.timeout_s = timeout_s
    # A short poll so `shutdown()` returns promptly; the default 0.5 s makes every
    # shutdown, and so every test teardown, wait half a second.
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05),
        name="wl-sync-endpoint",
        daemon=True,
    )
    thread.start()
    logger.info("session endpoint listening on %s:%s", *server.server_address[:2])
    return server
