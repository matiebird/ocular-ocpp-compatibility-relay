# Ocular LTE Plus V3 OCPP compatibility relay

[![Tests](https://github.com/matiebird/ocular-ocpp-compatibility-relay/actions/workflows/test.yml/badge.svg)](https://github.com/matiebird/ocular-ocpp-compatibility-relay/actions/workflows/test.yml)

This package installs a small Home Assistant app that sits between an Ocular LTE Plus V3 charger and Home Assistant's OCPP integration.

It is for chargers that repeatedly drop or fail their WebSocket connection when talking directly to Home Assistant.

## What it does

```text
Ocular charger
  -> Home Assistant host port 19000
  -> this compatibility relay
  -> existing Home Assistant OCPP port 9000
```

The relay uses Python `websockets==12.0` for the charger-facing connection and opens a separate connection to Home Assistant. It does not translate OCPP or change charging commands. Both sides still use WebSocket RFC 6455 and OCPP 1.6J.

The existing Home Assistant OCPP listener stays where it is. The installer does not edit `.storage`, Home Assistant's OCPP integration, automations or scripts. After a charger connection, the relay uses Home Assistant's OCPP services only to apply and verify the required charger timing values.

## Requirements

- Home Assistant OS or Home Assistant Supervised
- A working SSH session on the Home Assistant host with access to the Home Assistant CLI (`ha`)
- Home Assistant OCPP integration already running, normally on port 9000
- Home Assistant OCPP service-action response capability: `ocpp.configure` must return `reboot_required`, and `ocpp.get_configuration` must return `value`
- Ocular charger on wired Ethernet with a reserved DHCP address
- Charger configured for OCPP 1.6J over plain `WS` on a trusted home LAN

Supported Home Assistant host architectures:

- aarch64
- amd64

OCPP integration custom forks do not always use versions comparable with the upstream release numbers, so compatibility is defined by those two action-response fields rather than a semantic-version floor. If they are unavailable, charger traffic continues normally, automatic timing reports `timing_verification_failed`, and the supplied manual timing script remains available.

## Installation prerequisites

The setup requires access to:

1. The router's device list, to identify the charger and Home Assistant LAN addresses.
2. A working SSH session on the Home Assistant host, to run the installer command.
3. [OCPPSetTool for Android](https://play.google.com/store/apps/details?id=com.evsemaster.ocppset) or [OCPPSetTool for iPhone](https://apps.apple.com/au/app/ocppsettool/id6504644430), to enter the values printed by the installer.

Any method that provides the required SSH session is suitable. Home Assistant app examples include **Terminal & SSH** and **Advanced SSH & Web Terminal**. A separate SSH client is also suitable when the resulting session has the `ha` command available.

The installer does not require Python, Docker or Home Assistant internal-storage edits. Changing the physical charger's endpoint remains a separate OCPPSetTool step and cannot be automated by a Home Assistant app.

![Connection overview](docs/images/connection-overview.svg)

Before starting, write down:

```text
Charger IP:       ____________________
Home Assistant IP: ____________________
Current charger OCPP server: ______________________________
```

Keep the current OCPP server value so you can restore it if needed.

## Install

### 1. Copy and extract the ZIP

Download the latest `ocular-ocpp-easy-deploy` ZIP from the [GitHub releases page](https://github.com/matiebird/ocular-ocpp-compatibility-relay/releases/latest). Put the ZIP in `/config`, then extract it. The folder should be:

```text
/config/ocular-ocpp-easy-deploy/
```

### 2. Run the installer

Establish an SSH session to the Home Assistant host and run:

```bash
bash /config/ocular-ocpp-easy-deploy/install.sh CHARGER_IP HOME_ASSISTANT_IP
```

Example:

```bash
bash /config/ocular-ocpp-easy-deploy/install.sh 192.168.1.50 192.168.1.10
```

`CHARGER_IP` may also be a LAN network such as `192.168.1.0/24`; see below.

The defaults are:

```text
Charger ID: auto — the relay accepts the ID already configured in the charger
Home Assistant OCPP port: 9000
Relay port: 19000
Charger path: forwarded to Home Assistant unchanged
```

With the default `auto` charger ID, the relay learns the charger's configured OCPP identity from the path it connects with, forwards that path unchanged to Home Assistant OCPP, and uses the learned ID for automatic timing. You keep whatever Charger ID the charger already has. The learned ID appears as `charge_point_id=` on each `connection_open` log line.

To pin the relay to one charger ID, or if the Home Assistant OCPP port is different:

```bash
bash /config/ocular-ocpp-easy-deploy/install.sh CHARGER_IP HOME_ASSISTANT_IP CHARGE_POINT_ID HA_OCPP_PORT
```

Example:

```bash
bash /config/ocular-ocpp-easy-deploy/install.sh 192.168.1.50 192.168.1.10 driveway 9000
```

A pinned relay accepts only the path `/CHARGE_POINT_ID/CHARGE_POINT_ID` and rejects every other charger path. Pass `auto` as the charger ID to keep learning it while changing the port.

#### Charger address changes and multiple chargers

`CHARGER_IP` may be a single address or a CIDR network such as `192.168.1.0/24`. A network keeps the relay accepting the charger after a DHCP address change and admits several chargers on the same LAN. Networks broader than `/16` (IPv4) or `/64` (IPv6) are refused, so the allowlist can never become "everyone". The same rule applies to `allowed_sources` in the app options, which accepts up to 16 addresses or networks.

With the default `auto` charger ID, multiple chargers each keep their own ID and are forwarded to Home Assistant separately, provided your Home Assistant OCPP integration version supports more than one charge point. Automatic timing is queued per charger ID, so a reconnect during another charger's timing job is never dropped.

Two sessions with the same charger ID are handled by source address. A second session from the **same** address is the charger reconnecting after a dropout: the relay closes the stale session (logged as `session_superseded` and `cause=superseded`) and serves the new one immediately, rather than locking the charger out until TCP notices the old session is dead. A second session from a **different** address is refused with HTTP 409 and logged as `opening_rejected reason=duplicate_id`, so two chargers cannot fight over one identity.

The installer checks the IP addresses and existing OCPP listener, backs up an older relay outside Supervisor's `/addons` scan tree, installs the local app, verifies that it is started and listening, and prints the exact charger settings. If a reinstall fails after removing the previous version, it automatically attempts to restore and restart that version.

### 3. Change the charger endpoint

Set OCPPSetTool to the following, where `CHARGER_ID` is the Charger ID already configured in the charger (or the ID you pinned with the installer):

```text
Protocol: WS
Server: HOME_ASSISTANT_IP:19000/CHARGER_ID
Charger ID: CHARGER_ID (unchanged)
Authentication: blank, unless you configured it yourself
Mode: Online
```

For example, a charger with the ID `central` uses `HOME_ASSISTANT_IP:19000/central`.

Save the server and charger ID before setting Online mode.

Some Ocular firmware requires the server field without `ws://`. The example above intentionally omits it.

#### Actual OCPPSetTool screens

These are genuine OCPPSetTool interface captures reproduced from Ocular's official [LTE Plus v3 OCPP configuration guide](https://evse.com.au/wp-content/uploads/LTE-Plus-v3-OCPP-configuration-guide.pdf), not recreated mock-ups.

> **Do not copy the charger ID or Exploren server shown in the first screenshot.** They are Ocular's example values. Enter the exact charger ID and server printed by this installer's output.

**1. Main screen — set Charger ID, select `WS`, enter Server, and tap each blue tick to save**

<img src="docs/images/ocppsettool-main-official.png" alt="Actual OCPPSetTool main screen showing Charger ID, WS or WSS selection, server URL and save tick buttons" width="360">

**2. Open `Other Settings`**

<img src="docs/images/ocppsettool-other-settings-official.png" alt="Actual OCPPSetTool Other Settings screen with Set IP and Set Mode controls" width="360">

**3. Open `Set IP`, select `DHCP IP`, then tap `Set`**

<img src="docs/images/ocppsettool-set-ip-official.png" alt="Actual OCPPSetTool Set IP screen with Static IP and DHCP IP choices" width="360">

**4. Open `Set Mode`, select `Online`, then tap `Set`**

<img src="docs/images/ocppsettool-set-mode-official.png" alt="Actual OCPPSetTool Set Mode screen with Online and Offline choices" width="360">

The screen layout may differ slightly between Android, iPhone and app versions. The controls and field names above are the ones used by the Ocular LTE Plus v3 guide.

For a compact value-only view, use this labelled reference:

![OCPPSetTool field reference](docs/images/ocular-settings-reference.svg)

## Check that it connected

In the same SSH session, run:

```bash
ha apps logs local_ocular_ocpp_compatibility_relay
```

A good connection shows:

```text
connection_open
```

Home Assistant should then receive a fresh BootNotification and heartbeat. The connector should become `Available` or `Preparing`, with `NoError`.

If you do not see `connection_open`, do not keep changing settings at random. Check that:

- the charger address entered in the installer matches the charger in your router;
- the Home Assistant address entered in OCPPSetTool is correct;
- the server uses port `19000` and has no `ws://` prefix;
- the charger ID and path match the values printed by the installer (with the default `auto` ID, any well-formed charger path is accepted);
- the compatibility relay app is started.

Do not treat one successful command after reboot as proof. Leave it connected for a few minutes and try `ocpp.get_configuration` more than once before relying on it.

## Automatic post-connect charger timing

For the tested Ocular LTE Plus V3 firmware, these values are required. After each upstream charger connection, the relay waits for Home Assistant OCPP to finish establishing the charger, applies all three settings and reads them back automatically. Timing work runs separately from charger traffic and does not block the WebSocket relay. The relay terminates WebSocket control pings locally, so they do not become OCPP application traffic between the charger and Home Assistant.

The relay sets and reads back:

```text
HeartbeatInterval = 60
WebSocketPingInterval = 60
MeterValueSampleInterval = 10
```

`HeartbeatInterval` is connection-critical because it creates regular OCPP
request/response traffic. `WebSocketPingInterval` controls charger-generated
WebSocket keepalive traffic, which the relay answers locally.
`MeterValueSampleInterval` is the telemetry frequency; it is useful for current
and power updates but is not the setting that prevents an otherwise idle OCPP
session.

The relay addresses Home Assistant OCPP with the charger ID learned from each connection, or the pinned ID if you gave one to the installer. The integration accepts either its Home Assistant charger identifier or the charger's OCPP identifier, so no separate device ID is required.

Successful application is recorded in the app log as `timing_verified` with readback values `60`, `60` and `10`. The relay retries transient failures for up to several minutes without interrupting charging. It runs the process again after every reconnect because the tested firmware may reset `HeartbeatInterval` to `3600` after a Soft Reset or power cycle.

The supplied `ha-timing-script.yaml` remains available as a manual fallback if automatic verification reports `timing_verification_failed`. If you pinned a charger ID with the installer, the installed copy already carries it; otherwise set `ocpp_device_id` to the ID shown as `charge_point_id=` in the app log. If Home Assistant API access is unavailable, the app logs `timing_disabled` but keeps relaying charger traffic.

## Everyday Home Assistant controls

For a simple charger dashboard, use the generic examples in [`examples/README.md`](examples/README.md):

- [`examples/ocular-everyday-controls.yaml`](examples/ocular-everyday-controls.yaml) — Start, transaction-preserving Pause, same-session Resume, final Stop, current selection, optional sustained-taper finishing and fault notification.
- [`examples/ocular-dashboard-card.yaml`](examples/ocular-dashboard-card.yaml) — a dashboard built only from standard Home Assistant cards.

These examples are intentionally independent of any particular home's solar, tariff or battery policy. Automatic finishing is disabled until the user enables it, and routine reset controls are deliberately excluded.

## Charger control workarounds

The connection relay is only one part of a reliable installation. See [Ocular charger control workarounds](CHARGER_CONTROL_WORKAROUNDS.md) for the field-tested transaction and recovery rules:

- transaction-preserving 0 A hold and same-transaction resume;
- safe handling of delayed OCPP replies and ambiguous timeouts;
- sustained terminal-taper completion;
- explicit Hard Reset for exceptional command-unresponsive recovery;
- one serialized normal command authority.

The guide includes the tested OCPP profile shape and verification boundaries. The relay itself does not send charging or reset commands.

## Test charging

First test while the car can accept charge and someone is present:

1. Set 6 A while charging is off.
2. Start with the normal Home Assistant OCPP control.
3. Confirm real current and power.
4. Increase the current.
5. Wait about 60 seconds before another routine current change.
6. Reduce to 6 A and confirm the current falls.
7. Stop normally and confirm zero current and power.

Do not judge success from a service call alone. Use fresh charger current and power.

## Remove it

Establish an SSH session to the Home Assistant host and run:

```bash
cd /config/ocular-ocpp-easy-deploy
bash uninstall.sh HOME_ASSISTANT_IP
```

Example:

```bash
bash uninstall.sh 192.168.1.10
```

The uninstaller removes only this relay. It does not edit Home Assistant's OCPP integration. It prints the direct charger endpoint to restore in OCPPSetTool.

## Limits

This is a compatibility workaround, not an Ocular firmware update.

It can give the charger a WebSocket connection it handles more reliably and allow automatic recovery from brief reconnects. It cannot repair a charger connection attempt that never sends enough of an opening request to classify safely, and it cannot fix firmware that sends heartbeats but later ignores OCPP commands.

If the OCPP channel is alive but the charger is genuinely command-unresponsive, the tested firmware responded more reliably to an explicit Hard Reset than Soft Reset. Do not reset it merely for `Finishing`, `SuspendedEV`, `SuspendedEVSE`, zero current or one delayed response. If the OCPP channel is dead, no OCPP Reset command can travel over it; follow the charger's electrical safety and dedicated isolation procedure. After recovery, require BootNotification, heartbeat, a usable connector state and `NoError` before charging.

## Security boundary

The app:

- accepts only the configured charger IP;
- accepts only the configured path and `ocpp1.6` subprotocol;
- runs without host networking, privileges or `full_access` host permissions;
- receives a Home Assistant API token; the relay code restricts its use to the documented OCPP timing services;
- drops Linux privileges inside the container;
- limits queues, handshake buffers and complete application-message size;
- does not log OCPP frames, credentials or WebSocket keys;
- generates only the three documented timing configuration commands and their readbacks; charging profiles, transaction controls and resets remain outside the relay.

## Optional support

This workaround is free to use. If it saved you time and you would like to support further testing and documentation, you can optionally [leave a tip or donation on Ko-fi](https://ko-fi.com/matiebird).

Support is entirely optional. It does not affect access to the software, documentation, updates or community help.

## Source and licence

Source code, tests and releases are available at [matiebird/ocular-ocpp-compatibility-relay](https://github.com/matiebird/ocular-ocpp-compatibility-relay). This project is provided under the MIT License.
