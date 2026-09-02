from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from typing import Any

import websockets
from websockets.legacy.server import WebSocketServerProtocol

LOGGER = logging.getLogger("ocular_ocpp_websocket_proxy")
OCPP_SUBPROTOCOL = "ocpp1.6"
OPEN_TIMEOUT_SECONDS = 10
# Supervisor kills the container 10 s after "ha apps stop", so every phase of
# stop() is bounded and the whole teardown has one aggregate deadline.
CLOSE_TIMEOUT_SECONDS = 2  # close-frame and TCP-close wait per session
GRACEFUL_STOP_SECONDS = 3  # window for close frames and handler completion
STOP_TIMEOUT_SECONDS = 5  # aggregate limit for the whole teardown
FORCED_STOP_GRACE_SECONDS = 0.5  # settle time for tasks cancelled at the deadline
HANDSHAKE_READ_LIMIT = 16_384
LOGGED_PATH_LIMIT = 128
WEBSOCKET_MAX_QUEUE = 16


def _remaining(deadline: float, floor: float = 0.0) -> float:
    return max(deadline - asyncio.get_running_loop().time(), floor)


def _safe_log_path(request_target: str) -> str:
    """Return a bounded, query-free, control-character-safe path for diagnostics."""
    path = request_target.partition("?")[0][:LOGGED_PATH_LIMIT]
    return ascii(path)


@dataclass(frozen=True)
class RelayConfig:
    listen_host: str
    listen_port: int
    upstream_base: str
    upstream_path: str
    charge_point_id: str
    allowed_sources: tuple[str, ...]
    expected_paths: tuple[str, ...]
    max_message_bytes: int


@dataclass(frozen=True)
class BridgeTermination:
    cause: str
    upstream_code: int | None


class GuardedServerProtocol(WebSocketServerProtocol):
    """Reject unauthorized openings before a WebSocket session is established."""

    def __init__(self, *args: Any, relay: OcppWebSocketRelay, **kwargs: Any) -> None:
        self.relay = relay
        super().__init__(*args, **kwargs)

    async def process_request(self, path, request_headers):
        peer = self.remote_address
        source = str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"
        if source not in self.relay.config.allowed_sources:
            self.relay.counters["rejected_source"] += 1
            LOGGER.warning("opening_rejected reason=source source=%s", source)
            return self._rejection(HTTPStatus.FORBIDDEN, b"source rejected\n")

        if path not in self.relay.config.expected_paths:
            self.relay.counters["rejected_path"] += 1
            LOGGER.warning("opening_rejected reason=path source=%s", source)
            LOGGER.debug(
                "rejected_path source=%s path=%s", source, _safe_log_path(path)
            )
            return self._rejection(HTTPStatus.NOT_FOUND, b"path rejected\n")

        offered = []
        for value in request_headers.get_all("Sec-WebSocket-Protocol"):
            offered.extend(token.strip() for token in value.split(","))
        if OCPP_SUBPROTOCOL not in offered:
            self.relay.counters["rejected_subprotocol"] += 1
            LOGGER.warning("opening_rejected reason=subprotocol source=%s", source)
            return self._rejection(
                HTTPStatus.BAD_REQUEST, b"ocpp1.6 subprotocol required\n"
            )
        return None

    @staticmethod
    def _rejection(status: HTTPStatus, body: bytes):
        return (
            status,
            [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))],
            body,
        )


class OcppWebSocketRelay:
    """Terminate charger WebSockets and relay only application messages to HA."""

    def __init__(
        self,
        config: RelayConfig,
        on_upstream_connected: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self.config = config
        self.on_upstream_connected = on_upstream_connected
        self.server = None
        self._stopping = False
        self._stop_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._connecting: dict[asyncio.Task, Any] = {}
        self._timing_pending = False
        self.counters: Counter[str] = Counter()

    async def start(self):
        self._stopping = False
        self._stop_task = None
        self._connecting.clear()
        self._timing_pending = False
        self.server = await websockets.serve(
            self._handle_charger,
            self.config.listen_host,
            self.config.listen_port,
            create_protocol=partial(GuardedServerProtocol, relay=self),
            subprotocols=[OCPP_SUBPROTOCOL],
            ping_interval=None,
            compression=None,
            max_size=None,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            close_timeout=CLOSE_TIMEOUT_SECONDS,
            max_queue=WEBSOCKET_MAX_QUEUE,
            read_limit=HANDSHAKE_READ_LIMIT,
            write_limit=65_536,
        )
        return self.server

    async def stop(self) -> None:
        """Tear the relay down once; concurrent callers share the same teardown."""
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._teardown())
        await asyncio.shield(self._stop_task)

    async def _teardown(self) -> None:
        deadline = asyncio.get_running_loop().time() + STOP_TIMEOUT_SECONDS
        self._stopping = True
        self._timing_pending = False
        server, self.server = self.server, None
        residual: set[asyncio.Task] = set()
        try:
            if server is not None:
                residual |= await self._close_server(server, deadline)
        except Exception as exc:
            LOGGER.error("stop_error error_type=%s", type(exc).__name__)
        finally:
            # Timing work is cancelled even if closing the server failed.
            for task in self._background_tasks:
                task.cancel()
            residual |= self._background_tasks
            self._background_tasks = set()
            await self._settle(residual, deadline)

    async def _close_server(self, server, deadline: float) -> set[asyncio.Task]:
        """Close the listener and sessions; return tasks still alive afterwards."""
        server.close()
        # A session still opening its upstream connection has no upstream to
        # close gracefully; close its charger side and cancel it right away so
        # a stalled handshake cannot consume the graceful window.
        abort = asyncio.create_task(self._abort_connecting())
        closed = asyncio.ensure_future(server.wait_closed())
        done, _ = await asyncio.wait(
            {closed}, timeout=min(GRACEFUL_STOP_SECONDS, _remaining(deadline))
        )
        residual: set[asyncio.Task] = {abort}
        if closed in done:
            error = closed.exception()
            if error is not None:
                LOGGER.error("stop_error error_type=%s", type(error).__name__)
        else:
            closed.cancel()
            handlers = {
                websocket.handler_task
                for websocket in server.websockets
                if not websocket.handler_task.done()
            }
            LOGGER.warning("stop_timeout sessions=%d", len(handlers))
            for task in handlers:
                task.cancel()
            residual.update(handlers)
            residual.add(closed)
        self._collect_close_task(server, residual)
        return residual

    @staticmethod
    def _collect_close_task(server, residual: set[asyncio.Task]) -> None:
        """Track a server-owned close task and consume a completed failure."""
        close_task = getattr(server, "close_task", None)
        if close_task is None or close_task.cancelled():
            return
        if not close_task.done():
            residual.add(close_task)
            return
        error = close_task.exception()
        if error is not None:
            LOGGER.error("stop_error error_type=%s", type(error).__name__)

    async def _abort_connecting(self) -> None:
        async def abort(handler: asyncio.Task, charger) -> None:
            try:
                await charger.close(code=1001, reason="relay shutdown")
            finally:
                handler.cancel()

        await asyncio.gather(
            *(abort(handler, charger) for handler, charger in self._connecting.items()),
            return_exceptions=True,
        )

    @staticmethod
    def _observe_stop_task(task: asyncio.Task) -> None:
        """Consume and log a task failure so asyncio never reports it as unhandled."""
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error("stop_task_failed error_type=%s", type(error).__name__)

    @staticmethod
    async def _settle(tasks: set[asyncio.Task], deadline: float) -> None:
        pending = {task for task in tasks if not task.done()}
        completed = tasks - pending
        for task in completed:
            OcppWebSocketRelay._observe_stop_task(task)
        if pending:
            completed, pending = await asyncio.wait(
                pending, timeout=_remaining(deadline)
            )
            for task in completed:
                OcppWebSocketRelay._observe_stop_task(task)
        if pending:
            for task in pending:
                task.cancel()
            completed, pending = await asyncio.wait(
                pending, timeout=_remaining(deadline, FORCED_STOP_GRACE_SECONDS)
            )
            for task in completed:
                OcppWebSocketRelay._observe_stop_task(task)
        if pending:
            # If an abandoned task later finishes before process exit, consume
            # its exception in the event-loop callback.
            for task in pending:
                task.add_done_callback(OcppWebSocketRelay._observe_stop_task)
            LOGGER.error("stop_abandoned tasks=%d", len(pending))

    def _start_timing(self) -> None:
        if self.on_upstream_connected is None:
            return
        if self._background_tasks:
            self.counters["timing_coalesced"] += 1
            self._timing_pending = True
            return
        self.counters["timing_attempts"] += 1
        task = asyncio.create_task(self.on_upstream_connected())
        self._background_tasks.add(task)
        task.add_done_callback(self._timing_done)

    def _timing_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error("timing_task_failed error_type=%s", type(error).__name__)
        if self._timing_pending and not self._stopping:
            self._timing_pending = False
            self._start_timing()

    async def _handle_charger(self, charger, path: str) -> None:
        source = self._source(charger)
        termination = BridgeTermination("upstream_failure", None)
        if charger.subprotocol != OCPP_SUBPROTOCOL:
            self.counters["rejected_subprotocol"] += 1
            await charger.close(code=1002, reason="ocpp1.6 required")
            return

        self.counters["accepted_connections"] += 1
        LOGGER.info("connection_open source=%s", source)
        upstream_url = (
            f"{self.config.upstream_base.rstrip('/')}{self.config.upstream_path}"
        )
        handler = asyncio.current_task()
        self._connecting[handler] = charger
        try:
            async with websockets.connect(
                upstream_url,
                subprotocols=[OCPP_SUBPROTOCOL],
                ping_interval=None,
                compression=None,
                max_size=None,
                open_timeout=OPEN_TIMEOUT_SECONDS,
                close_timeout=CLOSE_TIMEOUT_SECONDS,
                max_queue=WEBSOCKET_MAX_QUEUE,
                read_limit=HANDSHAKE_READ_LIMIT,
                write_limit=65_536,
            ) as upstream:
                self._connecting.pop(handler, None)
                if upstream.subprotocol != OCPP_SUBPROTOCOL:
                    raise RuntimeError("upstream rejected ocpp1.6 subprotocol")
                self.counters["upstream_connections"] += 1
                self._start_timing()
                termination = await self._bridge(charger, upstream)
        except asyncio.CancelledError:
            termination = BridgeTermination("relay_shutdown", None)
            raise
        except Exception as exc:
            if self._stopping:
                termination = BridgeTermination("relay_shutdown", None)
            else:
                self.counters["upstream_failures"] += 1
                LOGGER.error("upstream_failure error_type=%s", type(exc).__name__)
            if not charger.closed:
                await charger.close(code=1011, reason="upstream unavailable")
        finally:
            self._connecting.pop(handler, None)
            LOGGER.info(
                "connection_closed source=%s cause=%s charger_code=%s upstream_code=%s",
                source,
                termination.cause,
                charger.close_code,
                termination.upstream_code,
            )

    async def _bridge(self, charger, upstream) -> BridgeTermination:
        oversized_message = asyncio.Event()
        charger_to_upstream = asyncio.create_task(
            self._copy_messages(
                charger,
                upstream,
                "forwarded_charger_messages",
                charger,
                upstream,
                oversized_message,
            )
        )
        upstream_to_charger = asyncio.create_task(
            self._copy_messages(
                upstream,
                charger,
                "forwarded_upstream_messages",
                charger,
                upstream,
                oversized_message,
            )
        )
        tasks = {charger_to_upstream, upstream_to_charger}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        ordered_tasks = [*done, *pending]
        results = await asyncio.gather(*ordered_tasks, return_exceptions=True)
        task_results = dict(zip(ordered_tasks, results))
        for result in task_results.values():
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, Exception) and not isinstance(
                result, websockets.ConnectionClosed
            ):
                raise result

        if oversized_message.is_set() or any(
            result == "message_too_large" for result in task_results.values()
        ):
            return BridgeTermination("message_too_large", upstream.close_code)

        if self._stopping:
            return BridgeTermination("relay_shutdown", upstream.close_code)

        # A copy task only finishes when its source or destination session has
        # ended, so the side that is still open tells us which peer went away.
        if not charger.closed:
            if upstream.close_code not in (1000, 1001):
                self.counters["upstream_failures"] += 1
                LOGGER.error("upstream_failure error_type=ConnectionClosed")
                await charger.close(code=1011, reason="upstream unavailable")
                return BridgeTermination("upstream_failure", upstream.close_code)
            else:
                await charger.close(code=1001, reason="upstream closed")
                return BridgeTermination("upstream_close", upstream.close_code)
        if not upstream.closed:
            await upstream.close(code=1001, reason="charger closed")
            return BridgeTermination("charger_close", upstream.close_code)

        if upstream_to_charger in done and charger_to_upstream not in done:
            cause = (
                "upstream_close"
                if upstream.close_code in (1000, 1001)
                else "upstream_failure"
            )
            return BridgeTermination(cause, upstream.close_code)
        return BridgeTermination("charger_close", upstream.close_code)

    async def _copy_messages(
        self,
        source,
        destination,
        counter_name: str,
        charger,
        upstream,
        oversized_message: asyncio.Event,
    ) -> str | None:
        async for message in source:
            if self._message_size(message) > self.config.max_message_bytes:
                self.counters["oversized_messages"] += 1
                LOGGER.warning(
                    "message_rejected reason=size direction=%s", counter_name
                )
                oversized_message.set()
                await asyncio.gather(
                    charger.close(code=1009, reason="message too large"),
                    upstream.close(code=1009, reason="message too large"),
                    return_exceptions=True,
                )
                return "message_too_large"
            await destination.send(message)
            self.counters[counter_name] += 1
        return None

    @staticmethod
    def _message_size(message: str | bytes) -> int:
        return (
            len(message) if isinstance(message, bytes) else len(message.encode("utf-8"))
        )

    @staticmethod
    def _source(websocket) -> str:
        peer = websocket.remote_address
        return str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"
