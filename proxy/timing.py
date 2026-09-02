from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable, Sequence
from http.client import HTTPException
from typing import Any
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("ocular_ocpp_websocket_proxy")
TIMING_VALUES = {
    "HeartbeatInterval": "60",
    "WebSocketPingInterval": "60",
    "MeterValueSampleInterval": "10",
}
DEFAULT_API_BASE = "http://supervisor/core"


class TimingVerificationError(RuntimeError):
    """Automatic charger timing could not be applied and verified."""


class HomeAssistantTimingClient:
    def __init__(
        self,
        token: str,
        device_id: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        request_json: Callable[[str, dict[str, str]], Any] | None = None,
    ) -> None:
        if not token:
            raise ValueError("Home Assistant API token is unavailable")
        if (
            not isinstance(device_id, str)
            or not 1 <= len(device_id) <= 64
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                for character in device_id
            )
        ):
            raise ValueError("ocpp_device_id is invalid")
        self._token = token
        self.device_id = device_id
        self.api_base = api_base.rstrip("/")
        self._request_json = request_json or self._default_request_json

    def apply_and_verify(self) -> dict[str, str]:
        for key, value in TIMING_VALUES.items():
            response = self._request_json(
                "/api/services/ocpp/configure?return_response",
                {"devid": self.device_id, "ocpp_key": key, "value": value},
            )
            try:
                reboot_required = response["service_response"]["reboot_required"]
            except (KeyError, TypeError) as exc:
                raise TimingVerificationError(
                    f"{key} configure response is missing"
                ) from exc
            if not isinstance(reboot_required, bool):
                raise TimingVerificationError(f"{key} configure response is invalid")
            if reboot_required:
                raise TimingVerificationError(f"{key} requires a charger reboot")

        readback: dict[str, str] = {}
        for key, expected in TIMING_VALUES.items():
            response = self._request_json(
                "/api/services/ocpp/get_configuration?return_response",
                {"devid": self.device_id, "ocpp_key": key},
            )
            try:
                actual = str(response["service_response"]["value"])
            except (KeyError, TypeError) as exc:
                raise TimingVerificationError(f"{key} readback is missing") from exc
            readback[key] = actual
            if actual != expected:
                raise TimingVerificationError(
                    f"{key} readback {actual!r} did not match {expected!r}"
                )
        return readback

    def _default_request_json(self, path: str, payload: dict[str, str]) -> Any:
        request = Request(
            f"{self.api_base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except (
            HTTPException,
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise TimingVerificationError(
                f"Home Assistant timing service failed ({type(exc).__name__})"
            ) from exc


async def _run_in_daemon_thread(function: Callable[[], Any]) -> Any:
    """Run a blocking call on a daemon thread so it never delays process exit.

    asyncio.to_thread uses the default executor, whose threads are joined when
    the event loop and the interpreter shut down. A Home Assistant request that
    is hanging in urlopen would then hold the container past Supervisor's kill
    timeout. A daemon thread is simply abandoned once the task is cancelled.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def deliver(result: Any, error: BaseException | None) -> None:
        if future.done():
            return
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)

    def worker() -> None:
        try:
            outcome = (function(), None)
        except BaseException as exc:  # delivered to the awaiting task
            outcome = (None, exc)
        try:
            loop.call_soon_threadsafe(deliver, *outcome)
        except RuntimeError:
            pass  # the loop already closed during shutdown

    threading.Thread(target=worker, name="ocular-timing", daemon=True).start()
    return await future


class TimingController:
    def __init__(
        self,
        client: HomeAssistantTimingClient,
        *,
        initial_delay: float = 10,
        retry_delays: Sequence[float] = (10, 20, 30, 60, 60),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.initial_delay = initial_delay
        self.retry_delays = tuple(retry_delays)
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def apply_after_connection(self) -> dict[str, str]:
        async with self._lock:
            await self._sleep(self.initial_delay)
            delays = (*self.retry_delays, None)
            for attempt, retry_delay in enumerate(delays, start=1):
                try:
                    readback = await _run_in_daemon_thread(
                        self.client.apply_and_verify
                    )
                    LOGGER.info(
                        "timing_verified heartbeat=%s websocket_ping=%s meter_sample=%s",
                        readback["HeartbeatInterval"],
                        readback["WebSocketPingInterval"],
                        readback["MeterValueSampleInterval"],
                    )
                    return readback
                except TimingVerificationError as exc:
                    if retry_delay is None:
                        LOGGER.error(
                            "timing_verification_failed attempts=%d error_type=%s",
                            attempt,
                            type(exc).__name__,
                        )
                        raise
                    LOGGER.warning(
                        "timing_retry attempt=%d error_type=%s",
                        attempt,
                        type(exc).__name__,
                    )
                    await self._sleep(retry_delay)
        raise AssertionError("unreachable")
