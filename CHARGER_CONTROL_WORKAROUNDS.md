# Ocular charger control workarounds

This guide records field-tested OCPP behaviour from an Ocular LTE Plus V3 / BS-EV07 running firmware `405.3251.0Q03196`. Treat the current thresholds and reset behaviour as a starting point, then verify them against your own charger and vehicle.

The compatibility relay in this repository provides a workaround for the observed WebSocket connection problem and automatically applies and verifies the three documented charger timing values. It does not generate charging-profile, transaction-control or reset commands described below. Implement those rules in your OCPP central system or Home Assistant automations.

## Keep temporary hold separate from final stop

A temporary pause should preserve the active transaction. Send a transaction-bound `TxProfile` with a 0 A limit:

```json
{
  "connectorId": 1,
  "csChargingProfiles": {
    "chargingProfileId": 2,
    "transactionId": 123,
    "stackLevel": 1,
    "chargingProfilePurpose": "TxProfile",
    "chargingProfileKind": "Relative",
    "chargingSchedule": {
      "chargingRateUnit": "A",
      "chargingSchedulePeriod": [
        {
          "startPeriod": 0,
          "limit": 0,
          "numberPhases": 1
        }
      ]
    }
  }
}
```

Replace `123` with the active transaction ID reported by your central system. Do not copy a transaction ID from an example or supply one from an untrusted caller.

The expected transition is:

```text
Charging -> SuspendedEVSE
```

The transaction remains active. To resume, replace the same transaction profile with a whole-amp limit from 6 A through 32 A, capped at the lowest commissioned limit of the charger, circuit, cable, vehicle and site. Do not send another `RemoteStartTransaction` while the original transaction remains active.

Use `RemoteStopTransaction` only when the session should genuinely end. Verify the later physical state rather than treating an `Accepted` reply as completion:

```text
transaction ID = 0
charge control = off
measured current = 0 A
connector status = Finishing or Available
```

Profile IDs and stack levels are central-system ownership choices. The values above are illustrative; the [Home Assistant package](examples/ocular-everyday-controls.yaml) retains its own ID 3001 and stack 1. Avoid collisions with profiles owned by another controller, and keep the pause/resume pair identical except for its authorized limit. Use a Relative schedule bound to the active transaction.

A generic `limit_amps` or Maximum Current update may only alter a station-wide/default profile and leave a transaction-bound 0 A hold in force. Explicitly replace the owned hold profile for both resume and active-session current adjustment. Do not broadly clear profiles: other station-wide ceilings remain authoritative and can still prevent charging. Require fresh measured current on the same transaction; neither an accepted service call nor a retained current setting proves resume.

## Do not blindly retry timed-out commands

This charger can return a delayed `Accepted` response after the controller has timed out. An immediate retry can leave several profile commands in flight and allow an older command to take effect later.

After a timeout:

1. Treat the result as ambiguous.
2. Wait for a fresh heartbeat, connector status, transaction ID and measured current.
3. Correlate the delayed response when possible, then require a command-quiescence period and stable authoritative readback long enough to exclude a late command effect.
4. Reconcile the observed state before sending another consequential command.
5. Serialize charger commands so one normal controller owns the connection.

A service-call result is not physical proof. Require causal current, power, transaction or connector-state readback after every consequential command.

## Terminal taper completion

Some vehicles can remain in an active OCPP `Charging` transaction while drawing a small top-balancing current after the vehicle reports full.

The tested installation uses this completion rule:

```text
OCPP telemetry is fresh
connector status = Charging
transaction ID > 0
charge control = on
target current >= 6 A
measured current remains above 1 A and at or below 3 A for 15 minutes
-> terminal-taper candidate
```

The non-zero lower bound avoids treating a missing or failed current reading as completion. The 15-minute dwell avoids startup and short taper transients. When the candidate is detected, request a final stop through the normal serialized command path, then separately verify transaction ID 0, charge control off, measured current 0 A and a terminal connector state. Qualify the current band and dwell for your own vehicle before automating it. Vehicle-cloud state of charge should remain advisory.

## Reset recovery

Do not reset the charger for ordinary `SuspendedEV`, `SuspendedEVSE`, `Finishing`, zero-current or delayed-response states.

On the tested firmware, OCPP Soft Reset could return a delayed `Accepted` response without a demonstrated reboot, reconnect or exit from `Finishing`. An explicit OCPP Hard Reset proved more useful when the charger was genuinely command-unresponsive and the OCPP channel was still alive.

A successful recovery requires more than the reset response:

```text
old WebSocket session closes
new WebSocket session opens
BootNotification is accepted
fresh heartbeat and connector status arrive
command authority is usable again
```

If the OCPP connection itself is dead, no OCPP reset can travel over it. Follow the charger's electrical safety instructions and use its dedicated isolation procedure instead. Never interrupt an active wanted charge without first establishing a safe state.

## One normal command authority

Do not let several automations independently send start, stop, profile and reset commands. Route normal control through one serialized coordinator. Independent safety paths may reduce current or stop charging, but they should not issue competing positive commands.

Use OCPP heartbeat freshness, connector status, transaction ID, charge-control state, measured current/power and connector error as the authoritative control signals. Vendor-cloud vehicle data can be displayed, but should not authorize physical charger commands.

## Connection workaround

The connection problem and command problem are separate:

- The compatibility relay handles the charger-facing WebSocket behaviour and opens a clean second WebSocket session to Home Assistant.
- The relay automatically applies and verifies heartbeat, WebSocket ping and meter intervals after each upstream connection.
- The control rules above handle transactions, delayed replies, taper completion and exceptional recovery.

After any reset or power cycle, confirm that the reconnect log contains `timing_verified`. On the tested firmware, Soft Reset could restore `HeartbeatInterval` to `3600` while leaving `WebSocketPingInterval` at `60`. Use the supplied timing script only as a manual fallback if automatic verification fails.
