# Changelog

## 0.3.10

- Stop cleanly on SIGTERM/SIGINT so app stop and restart close charger sessions with a WebSocket close frame instead of waiting for the container kill timeout, and log `relay_shutdown` for those closures.
- Use the Supervisor default init so container signals reach the relay process.
- Attribute a charger dropping mid-forward to `charger_close` rather than `upstream_failure` in connection diagnostics.
- Reject `.` and `..` path segments in `expected_paths` and `upstream_path`.
- Accept the charger's configured ID by default (`charge_point_id: auto`): any well-formed charger path is forwarded to Home Assistant unchanged and its last segment is used for automatic timing. Pass an ID to the installer to pin the relay to a single charger path as before.
- Fill a pinned charge-point ID into the installed fallback timing script and example package, which now default to `central` like the previous app option.
- Accept CIDR networks in `allowed_sources` and as the installer's `CHARGER_IP`, no broader than /16 (IPv4) or /64 (IPv6), so a DHCP change or a second charger on the LAN does not lock the charger out.
- Queue automatic timing per charger ID so overlapping reconnects from several chargers each get their timing run.
- Replace a stale session when the same charger reconnects with the same ID from the same address (`session_superseded`), and refuse the same ID from a different address with HTTP 409 (`rejected_duplicate_id`).

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
