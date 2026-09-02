import asyncio
import gc
import logging
import unittest
from unittest.mock import AsyncMock, patch

import websockets
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

from proxy.server import OcppWebSocketRelay, RelayConfig, _safe_log_path


class RelayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.upstream_connections = []
        self.upstream_paths = []
        self.upstream_messages = []

        async def upstream_handler(websocket, path):
            self.upstream_connections.append(websocket)
            self.upstream_paths.append(path)
            try:
                async for message in websocket:
                    self.upstream_messages.append(message)
                    await websocket.send(
                        b"upstream:" + message
                        if isinstance(message, bytes)
                        else "upstream:" + message
                    )
            except websockets.ConnectionClosed:
                pass

        self.upstream_server = await websockets.serve(
            upstream_handler,
            "127.0.0.1",
            0,
            subprotocols=["ocpp1.6"],
            ping_interval=None,
            compression=None,
            max_size=None,
        )
        self.upstream_port = self.upstream_server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.upstream_server.close()
        await self.upstream_server.wait_closed()

    async def start_relay(self, **overrides):
        values = {
            "listen_host": "127.0.0.1",
            "listen_port": 0,
            "upstream_base": f"ws://127.0.0.1:{self.upstream_port}",
            "upstream_path": "/central/central",
            "charge_point_id": "central",
            "allowed_sources": ("127.0.0.1",),
            "expected_paths": ("/central/central",),
            "max_message_bytes": 1024,
        }
        values.update(overrides)
        on_upstream_connected = values.pop("on_upstream_connected", None)
        relay = OcppWebSocketRelay(
            RelayConfig(**values), on_upstream_connected=on_upstream_connected
        )
        server = await relay.start()
        self.addAsyncCleanup(self.close_server, relay)
        return relay, server.sockets[0].getsockname()[1]

    async def close_server(self, relay):
        await relay.stop()

    def connect(self, port, *, path="/central/central", subprotocols=("ocpp1.6",)):
        return websockets.connect(
            f"ws://127.0.0.1:{port}{path}",
            subprotocols=list(subprotocols),
            ping_interval=None,
            compression=None,
            max_size=None,
        )

    async def test_charger_and_upstream_are_separate_sessions_and_forward_both_ways(
        self,
    ):
        relay, port = await self.start_relay()
        async with self.connect(port) as charger:
            await charger.send('[2,"uid","Heartbeat",{}]')
            self.assertEqual(await charger.recv(), 'upstream:[2,"uid","Heartbeat",{}]')
            await charger.send(b"binary")
            self.assertEqual(await charger.recv(), b"upstream:binary")
            self.assertIsNot(charger, self.upstream_connections[0])
        self.assertEqual(
            self.upstream_messages, ['[2,"uid","Heartbeat",{}]', b"binary"]
        )
        self.assertEqual(relay.counters["forwarded_charger_messages"], 2)
        self.assertEqual(relay.counters["forwarded_upstream_messages"], 2)

    async def test_upstream_connection_triggers_timing_without_blocking_relay(self):
        timing_started = asyncio.Event()
        second_timing_started = asyncio.Event()
        release_timing = asyncio.Event()
        timing_calls = 0

        async def apply_timing():
            nonlocal timing_calls
            timing_calls += 1
            if timing_calls == 1:
                timing_started.set()
                await release_timing.wait()
            else:
                second_timing_started.set()

        relay, port = await self.start_relay(on_upstream_connected=apply_timing)
        async with self.connect(port) as charger:
            await asyncio.wait_for(timing_started.wait(), 1)
            relay._start_timing()
            await charger.send("hello")
            self.assertEqual(await charger.recv(), "upstream:hello")
            release_timing.set()
            await asyncio.wait_for(second_timing_started.wait(), 1)
        self.assertEqual(relay.counters["timing_attempts"], 2)
        self.assertEqual(relay.counters["timing_coalesced"], 1)

    async def test_timing_failure_is_logged_without_interrupting_relay(self):
        async def apply_timing():
            raise RuntimeError("timing failed")

        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="ERROR") as captured:
            _, port = await self.start_relay(on_upstream_connected=apply_timing)
            async with self.connect(port) as charger:
                await asyncio.sleep(0)
                await charger.send("hello")
                self.assertEqual(await charger.recv(), "upstream:hello")
                await asyncio.sleep(0)
        self.assertIn("timing_task_failed", "\n".join(captured.output))

    async def test_upstream_path_is_configured_and_keeps_charge_point_identity(self):
        _, port = await self.start_relay(upstream_path="/ha/central")
        async with self.connect(port) as charger:
            await charger.send("hello")
            await charger.recv()
        self.assertEqual(self.upstream_paths, ["/ha/central"])

    async def test_source_path_and_subprotocol_rejections_fail_closed(self):
        relay, port = await self.start_relay(allowed_sources=("192.0.2.1",))
        with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
            async with self.connect(port):
                pass
        self.assertEqual(relay.counters["rejected_source"], 1)
        self.assertEqual(self.upstream_connections, [])

        relay2, port2 = await self.start_relay()
        with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
            async with self.connect(port2, path="/wrong"):
                pass
        self.assertEqual(relay2.counters["rejected_path"], 1)

        relay3, port3 = await self.start_relay()
        with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
            async with self.connect(port3, subprotocols=("chat",)):
                pass
        self.assertEqual(relay3.counters["rejected_subprotocol"], 1)

    async def test_upstream_failure_closes_charger_without_retry(self):
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        relay, port = await self.start_relay(
            upstream_base=f"ws://127.0.0.1:{dead_port}"
        )
        async with self.connect(port) as charger:
            with self.assertRaises(ConnectionClosedError) as raised:
                await charger.recv()
            self.assertEqual(raised.exception.code, 1011)
        self.assertEqual(relay.counters["upstream_failures"], 1)
        self.assertEqual(relay.counters["upstream_connections"], 0)

    async def test_upstream_failure_after_open_closes_charger(self):
        _, port = await self.start_relay()
        async with self.connect(port) as charger:
            while not self.upstream_connections:
                await asyncio.sleep(0)
            await self.upstream_connections[0].close(code=1011, reason="backend failed")
            with self.assertRaises(ConnectionClosedError) as raised:
                await asyncio.wait_for(charger.recv(), 0.5)
            self.assertEqual(raised.exception.code, 1011)

    async def test_control_ping_terminates_locally_and_is_not_an_ocpp_message(self):
        _, port = await self.start_relay()
        async with self.connect(port) as charger:
            pong = await charger.ping(b"private-ping")
            await asyncio.wait_for(pong, 1)
            await asyncio.sleep(0.05)
            self.assertEqual(self.upstream_messages, [])

    async def test_oversized_message_closes_without_forwarding(self):
        relay, port = await self.start_relay(max_message_bytes=8)
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                await charger.send("ninebytes")
                with self.assertRaises(ConnectionClosedError) as raised:
                    await charger.recv()
                self.assertEqual(raised.exception.code, 1009)
            await asyncio.sleep(0.05)
        self.assertEqual(self.upstream_messages, [])
        self.assertEqual(relay.counters["oversized_messages"], 1)
        output = "\n".join(captured.output)
        self.assertIn("cause=message_too_large", output)
        self.assertIn("charger_code=1009", output)

    async def test_rejected_path_debug_log_removes_query_secrets(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="DEBUG") as captured:
            with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
                async with self.connect(port, path="/wrong?token=TOP-SECRET"):
                    pass
        output = "\n".join(captured.output)
        self.assertIn("path='/wrong'", output)
        self.assertNotIn("TOP-SECRET", output)
        self.assertNotIn("token", output)
        self.assertEqual(relay.counters["rejected_path"], 1)

    async def test_malformed_rejected_path_still_returns_404(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.addAsyncCleanup(writer.wait_closed)
        with self.assertLogs(logger, level="DEBUG") as captured:
            writer.write(
                (
                    "GET //[ HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "Sec-WebSocket-Protocol: ocpp1.6\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), 1)
        writer.close()
        output = "\n".join(captured.output)
        self.assertIn(b"404", status_line)
        self.assertIn("path='//['", output)
        self.assertEqual(relay.counters["rejected_path"], 1)

    def test_safe_log_path_escapes_control_characters(self):
        rendered = _safe_log_path("/wrong\x1b[31m\r\nnext?token=TOP-SECRET")
        self.assertIn("\\x1b", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\r", rendered)
        self.assertNotIn("\n", rendered)
        self.assertNotIn("TOP-SECRET", rendered)

    async def test_connection_log_records_charger_close_cause(self):
        _, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                await charger.send("hello")
                await charger.recv()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=charger_close", output)
        self.assertIn("charger_code=1000", output)

    async def test_connection_log_records_upstream_normal_close_cause(self):
        _, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                while not self.upstream_connections:
                    await asyncio.sleep(0)
                await self.upstream_connections[0].close(code=1000, reason="done")
                with self.assertRaises(websockets.ConnectionClosed) as raised:
                    await charger.recv()
                self.assertEqual(raised.exception.code, 1001)
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=upstream_close", output)
        self.assertIn("upstream_code=1000", output)

    async def test_connection_log_records_upstream_failure_cause(self):
        _, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                while not self.upstream_connections:
                    await asyncio.sleep(0)
                await self.upstream_connections[0].close(code=1011, reason="failed")
                with self.assertRaises(ConnectionClosedError) as raised:
                    await charger.recv()
                self.assertEqual(raised.exception.code, 1011)
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=upstream_failure", output)
        self.assertIn("upstream_code=1011", output)

    async def test_connection_log_records_relay_shutdown_cause(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            charger = await self.connect(port)
            self.addAsyncCleanup(charger.close)
            while not self.upstream_connections:
                await asyncio.sleep(0)
            await relay.stop()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=relay_shutdown", output)
        self.assertIn("charger_code=1001", output)

    async def start_stalled_upstream(self):
        release = asyncio.Event()

        async def stall(reader, writer):
            await release.wait()
            writer.close()

        stalled = await asyncio.start_server(stall, "127.0.0.1", 0)
        self.addAsyncCleanup(stalled.wait_closed)
        self.addCleanup(stalled.close)
        self.addCleanup(release.set)
        return stalled.sockets[0].getsockname()[1]

    async def test_stop_cancels_a_stalled_upstream_handshake_promptly(self):
        stalled_port = await self.start_stalled_upstream()
        # The upstream connect happens inside the handler, so the shortened
        # close timeout has to stay in force for the whole test.
        with (
            patch("proxy.server.CLOSE_TIMEOUT_SECONDS", 0.2),
            self.assertLogs(
                logging.getLogger("ocular_ocpp_websocket_proxy"), level="INFO"
            ) as captured,
        ):
            relay, port = await self.start_relay(
                upstream_base=f"ws://127.0.0.1:{stalled_port}"
            )
            server = relay.server
            charger = await self.connect(port)
            self.addAsyncCleanup(charger.close)
            while relay.counters["accepted_connections"] == 0:
                await asyncio.sleep(0)
            handlers = [websocket.handler_task for websocket in server.websockets]
            self.assertEqual(len(handlers), 1)
            started = asyncio.get_running_loop().time()
            await relay.stop()
            elapsed = asyncio.get_running_loop().time() - started
        self.assertLess(elapsed, 1.5)
        output = "\n".join(captured.output)
        self.assertNotIn("stop_timeout", output)
        self.assertIn("cause=relay_shutdown", output)
        self.assertEqual(charger.close_code, 1001)
        # Every internal task has finished before stop() returns.
        self.assertTrue(all(task.done() for task in handlers))
        self.assertTrue(server.close_task.done())
        self.assertEqual(len(server.websockets), 0)

    async def test_concurrent_stop_calls_share_one_teardown(self):
        relay, port = await self.start_relay()
        charger = await self.connect(port)
        self.addAsyncCleanup(charger.close)
        while not self.upstream_connections:
            await asyncio.sleep(0)
        with patch.object(relay, "_teardown", wraps=relay._teardown) as teardown:
            await asyncio.gather(relay.stop(), relay.stop(), relay.stop())
            await relay.stop()
        self.assertEqual(teardown.call_count, 1)
        self.assertIsNone(relay.server)
        self.assertEqual(charger.close_code, 1001)

    async def test_connection_log_records_initial_upstream_failure(self):
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            _, port = await self.start_relay(
                upstream_base=f"ws://127.0.0.1:{dead_port}"
            )
            async with self.connect(port) as charger:
                with self.assertRaises(ConnectionClosedError):
                    await charger.recv()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=upstream_failure", output)
        self.assertIn("upstream_code=None", output)

    async def test_logs_do_not_contain_raw_credentials_or_frames(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                secret_frame = '[2,"credential-uid","Authorize",{"idTag":"TOP-SECRET"}]'
                await charger.send(secret_frame)
                await charger.recv()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertNotIn("TOP-SECRET", output)
        self.assertNotIn("credential-uid", output)
        self.assertNotIn(secret_frame, output)
        self.assertIn("connection_open", output)
        self.assertIn("connection_closed", output)
        self.assertEqual(relay.counters["forwarded_charger_messages"], 1)


class FakeSession:
    """Minimal WebSocket stand-in: yields queued messages, then behaves closed."""

    def __init__(self, incoming=(), *, fail_send=False):
        self.incoming = list(incoming)
        self.sent = []
        self.closed = False
        self.close_code = None
        self.fail_send = fail_send
        self.released = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.incoming:
            return self.incoming.pop(0)
        await self.released.wait()
        self.closed = True
        raise StopAsyncIteration

    async def send(self, message):
        if self.fail_send:
            self.closed = True
            self.close_code = 1006
            raise ConnectionClosedError(None, None)
        self.sent.append(message)

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.released.set()


class BridgeCauseTests(unittest.IsolatedAsyncioTestCase):
    def relay(self):
        return OcppWebSocketRelay(
            RelayConfig(
                listen_host="127.0.0.1",
                listen_port=0,
                upstream_base="ws://127.0.0.1:1",
                upstream_path="/central/central",
                charge_point_id="central",
                allowed_sources=("127.0.0.1",),
                expected_paths=("/central/central",),
                max_message_bytes=1024,
            )
        )

    async def test_charger_dropping_mid_forward_is_a_charger_close(self):
        charger = FakeSession(fail_send=True)
        upstream = FakeSession(incoming=["from-upstream"])
        relay = self.relay()
        termination = await relay._bridge(charger, upstream)
        self.assertEqual(termination.cause, "charger_close")
        self.assertEqual(upstream.close_code, 1001)
        self.assertEqual(relay.counters["upstream_failures"], 0)

    async def test_upstream_dropping_mid_forward_is_an_upstream_failure(self):
        charger = FakeSession(incoming=["from-charger"])
        upstream = FakeSession(fail_send=True)
        relay = self.relay()
        termination = await relay._bridge(charger, upstream)
        self.assertEqual(termination.cause, "upstream_failure")
        self.assertEqual(charger.close_code, 1011)
        self.assertEqual(relay.counters["upstream_failures"], 1)


class FakeWebSocket:
    def __init__(self, handler_task):
        self.handler_task = handler_task


class FakeServer:
    """websockets server stand-in with controllable close behaviour."""

    def __init__(self, handler_tasks=(), wait_closed_error=None):
        self.websockets = {FakeWebSocket(task) for task in handler_tasks}
        self.close_task = None
        self.closed = asyncio.Event()
        self._wait_closed_error = wait_closed_error

    def close(self):
        self.close_task = asyncio.create_task(self._close())

    async def _close(self):
        handlers = [websocket.handler_task for websocket in self.websockets]
        if handlers:
            await asyncio.wait(handlers)
        self.closed.set()

    async def wait_closed(self):
        if self._wait_closed_error is not None:
            raise self._wait_closed_error
        await self.closed.wait()


class TeardownTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return RelayConfig(
            listen_host="127.0.0.1",
            listen_port=0,
            upstream_base="ws://127.0.0.1:1",
            upstream_path="/central/central",
            charge_point_id="central",
            allowed_sources=("127.0.0.1",),
            expected_paths=("/central/central",),
            max_message_bytes=1024,
        )

    async def start_with(self, fake_server, on_upstream_connected=None):
        relay = OcppWebSocketRelay(
            self.config(), on_upstream_connected=on_upstream_connected
        )
        with patch(
            "proxy.server.websockets.serve",
            new=AsyncMock(return_value=fake_server),
        ):
            await relay.start()
        return relay

    async def test_failed_wait_closed_still_tracks_the_server_close_task(self):
        release = asyncio.Event()

        class BrokenWaitServer(FakeServer):
            def close(self):
                async def cancellation_resistant_close():
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            continue

                self.close_task = asyncio.create_task(cancellation_resistant_close())

        fake_server = BrokenWaitServer(wait_closed_error=RuntimeError("boom"))
        relay = await self.start_with(fake_server)
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        try:
            with (
                patch("proxy.server.STOP_TIMEOUT_SECONDS", 0.2),
                patch("proxy.server.FORCED_STOP_GRACE_SECONDS", 0.1),
                self.assertLogs(logger, level="ERROR") as captured,
            ):
                await relay.stop()
            output = "\n".join(captured.output)
            self.assertIn("stop_error error_type=RuntimeError", output)
            self.assertIn("stop_abandoned tasks=1", output)
            self.assertFalse(fake_server.close_task.done())
        finally:
            release.set()
            await fake_server.close_task

    async def test_close_task_failure_during_settle_is_observed(self):
        release = asyncio.Event()

        class LateFailServer(FakeServer):
            def close(self):
                async def fail_after_collection():
                    await release.wait()
                    raise LookupError("late close failure")

                self.close_task = asyncio.create_task(fail_after_collection())

        fake_server = LateFailServer(wait_closed_error=RuntimeError("wait failed"))
        relay = await self.start_with(fake_server)
        loop = asyncio.get_running_loop()
        unhandled = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        try:
            loop.call_later(0.01, release.set)
            with self.assertLogs(
                logging.getLogger("ocular_ocpp_websocket_proxy"), level="ERROR"
            ) as captured:
                await relay.stop()
            self.assertTrue(fake_server.close_task.done())
            fake_server.close_task = None
            gc.collect()
            await asyncio.sleep(0)
            self.assertEqual(unhandled, [])
            self.assertIn("stop_task_failed error_type=LookupError", "\n".join(captured.output))
        finally:
            loop.set_exception_handler(previous_handler)

    async def test_timing_task_is_cancelled_even_if_server_close_fails(self):
        timing_started = asyncio.Event()

        async def timing():
            timing_started.set()
            await asyncio.sleep(3600)

        relay = await self.start_with(
            FakeServer(wait_closed_error=RuntimeError("boom")),
            on_upstream_connected=timing,
        )
        relay._start_timing()
        await timing_started.wait()
        (timing_task,) = relay._background_tasks
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="ERROR") as captured:
            await relay.stop()
        self.assertIn("stop_error error_type=RuntimeError", "\n".join(captured.output))
        self.assertTrue(timing_task.cancelled())
        self.assertEqual(relay._background_tasks, set())

    async def test_cancellation_resistant_session_cannot_defeat_the_deadline(self):
        async def stubborn_handler():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass  # swallows the first cancellation
            await asyncio.sleep(3600)

        handler = asyncio.create_task(stubborn_handler())

        async def finish(task):
            for _ in range(5):
                if task.done():
                    return
                task.cancel()
                await asyncio.sleep(0)

        self.addAsyncCleanup(finish, handler)
        fake_server = FakeServer(handler_tasks=[handler])
        relay = await self.start_with(fake_server)
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with (
            patch("proxy.server.GRACEFUL_STOP_SECONDS", 0.2),
            patch("proxy.server.STOP_TIMEOUT_SECONDS", 0.6),
            patch("proxy.server.FORCED_STOP_GRACE_SECONDS", 0.2),
            self.assertLogs(logger, level="WARNING") as captured,
        ):
            started = asyncio.get_running_loop().time()
            await relay.stop()
            elapsed = asyncio.get_running_loop().time() - started
        output = "\n".join(captured.output)
        self.assertIn("stop_timeout sessions=1", output)
        self.assertNotIn("stop_abandoned", output)
        self.assertLess(elapsed, 1.5)
        self.assertTrue(handler.done())
        self.assertTrue(fake_server.close_task.done())

    async def test_stop_without_start_is_harmless(self):
        relay = OcppWebSocketRelay(self.config())
        await relay.stop()
        await relay.stop()


class ServerConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_uses_omv_compatible_websocket_options_and_ten_second_open_timeout(
        self,
    ):
        config = RelayConfig(
            listen_host="127.0.0.1",
            listen_port=9000,
            upstream_base="ws://homeassistant:9001",
            upstream_path="/central/central",
            charge_point_id="central",
            allowed_sources=("127.0.0.1",),
            expected_paths=("/central/central",),
            max_message_bytes=65536,
        )
        fake_server = AsyncMock()
        with patch(
            "proxy.server.websockets.serve", new=AsyncMock(return_value=fake_server)
        ) as serve:
            relay = OcppWebSocketRelay(config)
            await relay.start()
        kwargs = serve.await_args.kwargs
        self.assertEqual(kwargs["subprotocols"], ["ocpp1.6"])
        self.assertIsNone(kwargs["ping_interval"])
        self.assertIsNone(kwargs["compression"])
        self.assertIsNone(kwargs["max_size"])
        self.assertEqual(kwargs["open_timeout"], 10)
        self.assertLessEqual(kwargs["close_timeout"], 2)
        self.assertLessEqual(kwargs["read_limit"], 65536)
        self.assertLessEqual(kwargs["max_queue"], 32)
