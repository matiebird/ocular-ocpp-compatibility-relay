from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pwd
import signal
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .server import (
    OcppWebSocketRelay,
    RelayConfig,
    is_canonical_path,
    MIN_IPV4_PREFIX,
    MIN_IPV6_PREFIX,
    is_valid_charge_point_id,
    normalize_source,
)
from .timing import HomeAssistantTimingClient, TimingController

LOGGER = logging.getLogger("ocular_ocpp_websocket_proxy")
MAX_ALLOWLIST_ENTRIES = 16
MAX_EXPECTED_PATHS = 8
AUTO_CHARGE_POINT_ID = "auto"
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _validate_path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{name} must be an absolute WebSocket path")
    if not is_canonical_path(value):
        raise ValueError(f"{name} must be a bounded canonical path without a query")
    return value


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def config_from_options(options: dict[str, Any]) -> RelayConfig:
    sources = options.get("allowed_sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_ALLOWLIST_ENTRIES:
        raise ValueError("allowed_sources must be a bounded non-empty list")
    try:
        normalized_sources = tuple(normalize_source(value) for value in sources)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "allowed_sources must contain IP addresses or CIDR networks "
            f"no broader than /{MIN_IPV4_PREFIX} (IPv4) or /{MIN_IPV6_PREFIX} (IPv6)"
        ) from exc
    if len(set(normalized_sources)) != len(normalized_sources):
        raise ValueError("allowed_sources contains duplicates")

    charge_point_id = options.get("charge_point_id", AUTO_CHARGE_POINT_ID)
    paths = options.get("expected_paths")
    if charge_point_id == AUTO_CHARGE_POINT_ID:
        # Learn the charger's configured identity from each connection path and
        # forward that path unchanged, so no ID has to be agreed in advance.
        if not _empty(paths) or not _empty(options.get("upstream_path")):
            raise ValueError(
                "expected_paths and upstream_path must be empty when "
                "charge_point_id is auto"
            )
        expected_paths: tuple[str, ...] = ()
        upstream_path = None
        charge_point_id = None
    else:
        if not is_valid_charge_point_id(charge_point_id):
            raise ValueError("charge_point_id is invalid")
        if (
            not isinstance(paths, list)
            or not 1 <= len(paths) <= MAX_EXPECTED_PATHS
        ):
            raise ValueError("expected_paths must be a bounded non-empty list")
        expected_paths = tuple(_validate_path(value, "expected_path") for value in paths)
        if len(set(expected_paths)) != len(expected_paths):
            raise ValueError("expected_paths contains duplicates")
        upstream_path = _validate_path(options.get("upstream_path"), "upstream_path")
        if upstream_path.rsplit("/", 1)[-1] != charge_point_id:
            raise ValueError(
                "upstream_path must preserve the HA charge-point identity"
            )

    size = options.get("max_message_bytes")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 1024 <= size <= 1_048_576
    ):
        raise ValueError("max_message_bytes must be between 1024 and 1048576")

    upstream_port = options.get("upstream_port")
    if (
        isinstance(upstream_port, bool)
        or not isinstance(upstream_port, int)
        or not 1 <= upstream_port <= 65535
    ):
        raise ValueError("upstream_port must be an integer between 1 and 65535")

    return RelayConfig(
        listen_host="0.0.0.0",
        listen_port=9000,
        upstream_base=f"ws://homeassistant:{upstream_port}",
        upstream_path=upstream_path,
        charge_point_id=charge_point_id,
        allowed_sources=normalized_sources,
        expected_paths=expected_paths,
        max_message_bytes=size,
    )


async def _report_metrics(relay: OcppWebSocketRelay) -> None:
    while True:
        await asyncio.sleep(300)
        counters = relay.counters
        LOGGER.info(
            "counters accepted=%d upstream=%d charger_messages=%d upstream_messages=%d "
            "rejected_source=%d rejected_path=%d rejected_subprotocol=%d "
            "oversized=%d upstream_failures=%d",
            counters["accepted_connections"],
            counters["upstream_connections"],
            counters["forwarded_charger_messages"],
            counters["forwarded_upstream_messages"],
            counters["rejected_source"],
            counters["rejected_path"],
            counters["rejected_subprotocol"],
            counters["oversized_messages"],
            counters["upstream_failures"],
        )


def _drop_privileges(user: str = "proxy") -> None:
    if os.geteuid() != 0:
        return
    account = pwd.getpwnam(user)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # websockets DEBUG logs can include HTTP headers and application frames.
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _request_stop(stop_event: asyncio.Event, signum: int) -> None:
    if not stop_event.is_set():
        LOGGER.info("shutdown_requested signal=%s", signal.Signals(signum).name)
    stop_event.set()


def _timing_callback(environment: Mapping[str, str]):
    token = environment.get("SUPERVISOR_TOKEN", "")
    if not token:
        LOGGER.error("timing_disabled reason=home_assistant_api_token_unavailable")
        return None
    return TimingController(HomeAssistantTimingClient(token)).apply_after_connection


async def run(options_path: Path) -> None:
    options = json.loads(options_path.read_text(encoding="utf-8"))
    if not isinstance(options, dict):
        raise ValueError("options file must contain an object")
    level_name = str(options.get("log_level", "info")).upper()
    if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("unsupported log_level")
    _configure_logging(level_name)
    config = config_from_options(options)
    timing_callback = _timing_callback(os.environ)
    _drop_privileges()
    relay = OcppWebSocketRelay(config, on_upstream_connected=timing_callback)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in SHUTDOWN_SIGNALS:
        loop.add_signal_handler(signum, _request_stop, stop_event, signum)
    await relay.start()
    LOGGER.info(
        "listening port=%d sources=%d paths=%d charge_point_id=%s upstream=%s uid=%d",
        config.listen_port,
        len(config.allowed_sources),
        len(config.expected_paths),
        config.charge_point_id or AUTO_CHARGE_POINT_ID,
        config.upstream_base,
        os.geteuid(),
    )
    metrics_task = asyncio.create_task(_report_metrics(relay))
    try:
        await stop_event.wait()
    finally:
        metrics_task.cancel()
        await asyncio.gather(metrics_task, return_exceptions=True)
        await relay.stop()
        for signum in SHUTDOWN_SIGNALS:
            loop.remove_signal_handler(signum)
        LOGGER.info("stopped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    args = parser.parse_args()
    asyncio.run(run(args.options))


if __name__ == "__main__":
    main()
