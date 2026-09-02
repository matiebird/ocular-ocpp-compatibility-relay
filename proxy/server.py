from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from typing import Any

import websockets
from websockets.legacy.server import WebSocketServerProtocol

LOGGER = logging.getLogger("ocular_ocpp_websocket_proxy")
OCPP_SUBPROTOCOL = "ocpp1.6"
OPEN_TIMEOUT_SECONDS = 10
HANDSHAKE_READ_LIMIT = 16_384
LOGGED_PATH_LIMIT = 128
WEBSOCKET_MAX_QUEUE = 16
MAX_PATH_LENGTH = 256
MAX_CHARGE_POINT_ID_LENGTH = 64
# Allowed-source networks may not be broader than these prefixes, so an
# allowlist can cover a LAN or site but never "everything".
MIN_IPV4_PREFIX = 16
MIN_IPV6_PREFIX = 64
CHARGE_POINT_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)


def _safe_log_path(request_target: str) -> str:
    """Return a bounded, query-free, control-character-safe path for diagnostics."""
    path = request_target.partition("?")[0][:LOGGED_PATH_LIMIT]
    return ascii(path)


def is_valid_charge_point_id(value: Any) -> bool:
    """Bounded OCPP charge-point identity made only of URL-safe characters."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_CHARGE_POINT_ID_LENGTH
        and all(character in CHARGE_POINT_ID_CHARACTERS for character in value)
    )


def is_canonical_path(value: Any) -> bool:
    """Absolute, bounded WebSocket path without query, fragment or dot segments."""
    return (
        isinstance(value, str)
        and value.startswith("/")
        and len(value) <= MAX_PATH_LENGTH
        and "?" not in value
        and "#" not in value
        and all(segment not in ("", ".", "..") for segment in value[1:].split("/"))
    )


def normalize_source(value: Any) -> str:
    """Return a canonical IP address or CIDR network string, else raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("allowed source must be a string")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    network = ipaddress.ip_network(value, strict=True)
    minimum = MIN_IPV4_PREFIX if network.version == 4 else MIN_IPV6_PREFIX
    if network.prefixlen < minimum:
        raise ValueError(f"allowed source network is broader than /{minimum}")
    return str(network)


@dataclass(frozen=True)
class RelayConfig:
    """Relay settings.

    ``allowed_sources`` holds IP addresses or CIDR networks; a charger may open
    a session only from an address inside one of them.

    With ``expected_paths`` set, only those charger paths are accepted and every
    session is forwarded to ``upstream_path`` for the pinned ``charge_point_id``.
    With ``expected_paths`` empty, ``upstream_path`` and ``charge_point_id`` must
    be ``None``: any canonical charger path is accepted, forwarded unchanged and
    its last segment is the charger's configured OCPP identity.
    """

    listen_host: str
    listen_port: int
    upstream_base: str
    upstream_path: str | None
    charge_point_id: str | None
    allowed_sources: tuple[str, ...]
    expected_paths: tuple[str, ...]
    max_message_bytes: int
    allowed_networks: tuple[Any, ...] = field(
        init=False, repr=False, compare=False, default=()
    )

    def __post_init__(self) -> None:
        if not self.allowed_sources:
            raise ValueError("allowed_sources must not be empty")
        networks = tuple(
            ipaddress.ip_network(normalize_source(value), strict=True)
            for value in self.allowed_sources
        )
        object.__setattr__(self, "allowed_networks", networks)
        if self.expected_paths:
            if self.upstream_path is None or self.charge_point_id is None:
                raise ValueError(
                    "upstream_path and charge_point_id are required with expected_paths"
                )
        elif self.upstream_path is not None or self.charge_point_id is not None:
            raise ValueError(
                "upstream_path and charge_point_id must be unset without expected_paths"
            )

    @property
    def learns_charge_point_id(self) -> bool:
        return not self.expected_paths

    def allows_source(self, source: str) -> bool:
        try:
            address = ipaddress.ip_address(source)
        except ValueError:
            return False
        return any(address in network for network in self.allowed_networks)

    def resolve(self, path: str) -> tuple[str, str] | None:
        """Return ``(upstream_path, charge_point_id)`` for a charger path, else None."""
        if self.expected_paths:
            if path not in self.expected_paths:
                return None
            assert self.upstream_path is not None and self.charge_point_id is not None
            return self.upstream_path, self.charge_point_id
        if not is_canonical_path(path):
            return None
        charge_point_id = path.rsplit("/", 1)[-1]
        if not is_valid_charge_point_id(charge_point_id):
            return None
        return path, charge_point_id


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
        if not self.relay.config.allows_source(source):
            self.relay.counters["rejected_source"] += 1
            LOGGER.warning("opening_rejected reason=source source=%s", source)
            return self._rejection(HTTPStatus.FORBIDDEN, b"source rejected\n")

        resolved = self.relay.config.resolve(path)
        if resolved is None:
            self.relay.counters["rejected_path"] += 1
            LOGGER.warning("opening_rejected reason=path source=%s", source)
            LOGGER.debug(
                "rejected_path source=%s path=%s", source, _safe_log_path(path)
            )
            return self._rejection(HTTPStatus.NOT_FOUND, b"path rejected\n")

        if self.relay.conflicts(resolved[1], source):
            self.relay.counters["rejected_duplicate_id"] += 1
            LOGGER.warning(
                "opening_rejected reason=duplicate_id source=%s charge_point_id=%s",
                source,
                resolved[1],
            )
            return self._rejection(
                HTTPStatus.CONFLICT, b"charge point already connected\n"
            )

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
        on_upstream_connected: Callable[[str], Awaitable[object]] | None = None,
    ) -> None:
        self.config = config
        self.on_upstream_connected = on_upstream_connected
        self.server = None
        self._stopping = False
        self._background_tasks: set[asyncio.Task] = set()
        self._timing_pending: dict[str, None] = {}
        self._active: dict[str, tuple[str, Any]] = {}
        self._superseded: set[int] = set()
        self._supersede_tasks: set[asyncio.Task] = set()
        self.counters: Counter[str] = Counter()

    def conflicts(self, charge_point_id: str, source: str) -> bool:
        """True when the ID is active from a different source address."""
        active = self._active.get(charge_point_id)
        return active is not None and active[0] != source

    @property
    def active_charge_point_ids(self) -> tuple[str, ...]:
        return tuple(self._active)

    async def start(self):
        self._stopping = False
        self._timing_pending.clear()
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
            max_queue=WEBSOCKET_MAX_QUEUE,
            read_limit=HANDSHAKE_READ_LIMIT,
            write_limit=65_536,
        )
        return self.server

    async def stop(self) -> None:
        self._timing_pending.clear()
        if self.server is not None:
            self._stopping = True
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        tasks = [*self._background_tasks, *self._supersede_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._supersede_tasks.clear()
        self._active.clear()
        self._superseded.clear()

    def _start_timing(self, charge_point_id: str) -> None:
        if self.on_upstream_connected is None:
            return
        if self._background_tasks:
            # One timing job runs at a time; queue each charger ID once so a
            # reconnect during another charger's job is never dropped.
            self.counters["timing_coalesced"] += 1
            self._timing_pending[charge_point_id] = None
            return
        self.counters["timing_attempts"] += 1
        task = asyncio.create_task(self.on_upstream_connected(charge_point_id))
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
            next_id = next(iter(self._timing_pending))
            del self._timing_pending[next_id]
            self._start_timing(next_id)

    async def _handle_charger(self, charger, path: str) -> None:
        source = self._source(charger)
        termination = BridgeTermination("upstream_failure", None)
        if charger.subprotocol != OCPP_SUBPROTOCOL:
            self.counters["rejected_subprotocol"] += 1
            await charger.close(code=1002, reason="ocpp1.6 required")
            return
        resolved = self.config.resolve(path)
        if resolved is None:
            self.counters["rejected_path"] += 1
            await charger.close(code=1008, reason="path rejected")
            return
        upstream_path, charge_point_id = resolved
        if not self._claim(charge_point_id, source, charger):
            await charger.close(code=1008, reason="charge point already connected")
            return

        self.counters["accepted_connections"] += 1
        LOGGER.info(
            "connection_open source=%s charge_point_id=%s", source, charge_point_id
        )
        upstream_url = f"{self.config.upstream_base.rstrip('/')}{upstream_path}"
        try:
            async with websockets.connect(
                upstream_url,
                subprotocols=[OCPP_SUBPROTOCOL],
                ping_interval=None,
                compression=None,
                max_size=None,
                open_timeout=OPEN_TIMEOUT_SECONDS,
                max_queue=WEBSOCKET_MAX_QUEUE,
                read_limit=HANDSHAKE_READ_LIMIT,
                write_limit=65_536,
            ) as upstream:
                if upstream.subprotocol != OCPP_SUBPROTOCOL:
                    raise RuntimeError("upstream rejected ocpp1.6 subprotocol")
                self.counters["upstream_connections"] += 1
                self._start_timing(charge_point_id)
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
            self._release(charge_point_id, charger)
            cause = termination.cause
            if id(charger) in self._superseded:
                self._superseded.discard(id(charger))
                cause = "superseded"
            LOGGER.info(
                "connection_closed source=%s charge_point_id=%s cause=%s "
                "charger_code=%s upstream_code=%s",
                source,
                charge_point_id,
                cause,
                charger.close_code,
                termination.upstream_code,
            )

    def _claim(self, charge_point_id: str, source: str, charger) -> bool:
        """Register the session for its ID, replacing a stale one from the same source."""
        active = self._active.get(charge_point_id)
        if active is not None:
            active_source, active_charger = active
            if active_source != source:
                self.counters["rejected_duplicate_id"] += 1
                LOGGER.warning(
                    "opening_rejected reason=duplicate_id source=%s charge_point_id=%s",
                    source,
                    charge_point_id,
                )
                return False
            # The same charger reconnecting: its previous session is stale, and
            # keepalive pings are off, so close it rather than lock the charger
            # out until TCP notices.
            self.counters["superseded_sessions"] += 1
            LOGGER.warning(
                "session_superseded source=%s charge_point_id=%s",
                source,
                charge_point_id,
            )
            self._superseded.add(id(active_charger))
            task = asyncio.create_task(
                active_charger.close(code=1001, reason="superseded by reconnect")
            )
            self._supersede_tasks.add(task)
            task.add_done_callback(self._supersede_tasks.discard)
        self._active[charge_point_id] = (source, charger)
        return True

    def _release(self, charge_point_id: str, charger) -> None:
        active = self._active.get(charge_point_id)
        if active is not None and active[1] is charger:
            del self._active[charge_point_id]

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
