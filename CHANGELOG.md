# Changelog

## 0.3.12

- Stop cleanly on SIGTERM/SIGINT so app stop and restart close charger sessions with a WebSocket close frame instead of waiting for the container kill timeout, and log `relay_shutdown` for those closures.
- Use the Supervisor default init so container signals reach the relay process.
- Bound shutdown well inside Supervisor's ten-second kill timeout: one shared teardown with a five-second aggregate deadline, two-second close handshakes, a three-second graceful window, immediate cancellation of a session still inside a stalled upstream handshake, and timing HTTP calls on a daemon thread that can never hold the process open.
- Track and observe the WebSocket server's close task on every teardown path, including `wait_closed()` failures; after the bounded cleanup deadline, the process event loop no longer waits indefinitely for a cancellation-resistant residual task.
- Attribute a charger dropping mid-forward to `charger_close` rather than `upstream_failure` in connection diagnostics.
- Reject `.` and `..` path segments in `expected_paths` and `upstream_path`.
- Fill the chosen charge-point ID into the installed fallback timing script and example package, and default both to `central` like the app, so custom charger IDs work end to end.
- Write every installer-generated charger ID and path as a quoted YAML string, so IDs such as `123`, `true`, `null` or `2026-01-01` are never retyped, and reject `.` and `..` before installation. Tests now run the real installer substitutions and parse the results.
- Keep the strict single-charger boundary: one pinned charger IP, one pinned charger path and one pinned OCPP identity, exactly as in 0.3.9 and 0.3.11.

## 0.3.11

- Withdraw the unsafe 0.3.10 auto-identity, CIDR and multi-charger expansion.
- Restore the dedicated single-charger IP and path allowlists from 0.3.9.
- Restore bounded single-job timing behavior while the broader design is corrected and reviewed.

## 0.3.10 — withdrawn

- This release was withdrawn after independent review found unbounded learned sessions and timing work, potentially overlong shutdown, overlapping same-ID upstream sessions, unusable IPv6 claims and installer/YAML validation gaps.

## 0.3.9

- Normalize dropped HTTP connections and malformed response encoding into bounded timing retries.
- Document the required Home Assistant OCPP service-action response capability for upstream releases and custom forks.

## 0.3.8

- Apply the required Ocular timing values automatically after each charger connection.
- Verify `HeartbeatInterval=60`, `WebSocketPingInterval=60` and `MeterValueSampleInterval=10` through Home Assistant OCPP service readback.
- Retry bounded transient failures without blocking charger traffic, coalesce reconnect timing jobs, and retain the YAML timing script as a manual fallback.
- Keep charger traffic available if Home Assistant API access is unavailable, with an explicit timing-disabled error.

## 0.3.7

- Refresh the Home Assistant app store correctly during install, rollback and removal.
- Allow custom charger IDs and OCPP ports while retaining runtime tests in the image build.
- Log query-free, escaped rejected paths at debug level without recording credentials.
- Record explicit charger, upstream, relay and message-size connection termination causes.
- Make charger timing a required manual post-install step with readback verification.

## 0.3.6

- Add plain-language, generic Home Assistant controls for Start, Pause, Resume and Stop.
- Add a commissioned-current helper, optional sustained-taper completion and charger-fault notification.
- Add a dashboard example using only built-in Home Assistant cards.
- Keep site-specific solar, tariff and battery policy out of the public examples.

## 0.3.5

- Add genuine OCPPSetTool interface captures from Ocular's official LTE Plus v3 guide.
- Show the main Charger ID/server controls, Other Settings, DHCP selection and Online mode.
- Link directly to the official Android and iPhone OCPPSetTool listings.

## 0.3.4

- Document transaction-preserving 0 A hold and same-transaction resume.
- Document delayed OCPP reply handling and sustained terminal-taper completion.
- Document the tested Hard Reset recovery boundary for a genuinely command-unresponsive charger.
- Clarify that the compatibility relay works around the observed connection problem but does not generate charger-control commands.

## 0.3.3

- Replace the optional Buy Me a Coffee support link with Ko-fi.

## 0.3.2

- Publish the compatibility relay as a standalone public project.
- Add GitHub Actions verification and MIT licensing.
- Add an explicitly optional support link.

## 0.3.1

- Move reinstall backups outside Supervisor's local-app discovery tree.
- Add automatic rollback when a reinstall fails.
- Verify the app reaches both started and listening states.
- Include a tested ZIP installer, uninstaller and charger timing script.
