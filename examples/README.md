# Everyday Home Assistant controls

These examples add straightforward controls for an Ocular LTE Plus v3 charger without copying one household's solar, tariff or battery rules.

They provide:

- **Start** — begin a new charging transaction.
- **Pause** — hold an active transaction at 0 A without ending it.
- **Resume** — continue the same transaction at the selected current.
- **Stop** — end the transaction and verify that current reaches zero.
- A **6 A to 32 A** current slider.
- An optional sustained-taper finish automation.
- A connector-fault notification.
- A dashboard made only from built-in Home Assistant cards.

There is deliberately no routine Reset control. Reset is an exceptional recovery action, not an everyday charger button.

## Before copying the files

The charger must already be connected to Home Assistant through the OCPP integration.

Open **Settings → Devices & services → OCPP → your charger** and confirm the charger has equivalents of these entities:

```text
Charge Control
Maximum Current
Status Connector
Transaction ID
Current Import
Power Active Import
Session Energy
Error Code Connector
Heartbeat
```

The supplied example assumes a Home Assistant charger identifier of `ocular`, which normally creates:

```text
switch.ocular_charge_control
number.ocular_maximum_current
sensor.ocular_status_connector
sensor.ocular_transaction_id
sensor.ocular_current_import
sensor.ocular_power_active_import
sensor.ocular_session_energy
sensor.ocular_error_code_connector
sensor.ocular_heartbeat
```

Entity IDs can differ if the charge-point ID was changed or an entity was renamed. Replace the example IDs with the IDs shown in your Home Assistant charger device page.

If `Current Import` or `Power Active Import` is missing, open the OCPP integration options and include the OCPP measurands `Current.Import` and `Power.Active.Import`.

## Install the controls

1. Copy [`ocular-everyday-controls.yaml`](ocular-everyday-controls.yaml) to:

   ```text
   /config/packages/ocular_everyday_controls.yaml
   ```

2. If Home Assistant packages are not already enabled, add this under the existing `homeassistant:` section in `/config/configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

   Do not create a second `homeassistant:` section. If one already exists, add only the `packages:` line beneath it.

3. In the copied package, replace the `ocular` entity IDs if your Home Assistant charger identifier differs. The `devid` values are the OCPP charge-point ID: replace `devid: central` with the ID shown as `charge_point_id=` in the relay log, unless you pinned an ID with the installer, in which case the installed copy under `/addons/ocular_ocpp_compatibility_relay/examples` already carries it.

4. Set the `input_number.ocular_charge_current` maximum no higher than the lowest commissioned limit of the charger, circuit, cable, vehicle and site. The example shows the charger's supported **6 A** to **32 A** range; a lower installation limit must remain lower.

5. In an SSH session on the Home Assistant host, check the configuration:

   ```bash
   ha core check
   ```

6. Restart Home Assistant only after the configuration check passes.

The automatic finish helper starts **off**. Nothing in this package starts charging on a schedule.

## Add the dashboard

1. Open the dashboard and choose **Edit dashboard → Add card → Manual**.
2. Paste [`ocular-dashboard-card.yaml`](ocular-dashboard-card.yaml).
3. Replace any entity IDs that differ from your charger.
4. Save the card.

The dashboard uses only built-in cards. It does not require HACS dashboard components.

## How the controls behave

### Start

Start first applies the selected current and then turns on the OCPP `Charge Control` switch. It refuses to start another transaction if the transaction ID is already above zero.

### Pause

Pause sends a transaction-bound `TxProfile` at 0 A. The expected state is `SuspendedEVSE`, with the original transaction ID still active. This is different from Stop.

### Resume

Resume replaces that same transaction profile with the selected whole-amp limit. It does not send another remote-start request.

### Stop

Stop turns off `Charge Control`, then waits for all of these:

```text
transaction ID = 0
current at or below 0.5 A
status = Finishing or Available
```

If those signals are not all confirmed within 90 seconds, Home Assistant creates a notification instead of blindly retrying.

### Optional automatic finish

`Ocular automatically finish after sustained taper` is off by default. When enabled, it requests the normal Stop script only while all of these remain true:

```text
heartbeat is fresh
status = Charging
transaction ID > 0
Charge Control = on
target current >= 6 A
measured current remains above 1 A and at or below 3 A for 15 minutes
```

The lower bound prevents a missing or failed current reading from looking like completion. Different vehicles can taper differently, so observe a normal full charge before enabling this option.

## First test

Test while the vehicle can accept charge and someone is present:

1. Select 6 A and press Start.
2. Confirm the charger reports real current and power.
3. Increase the current without exceeding the commissioned limit.
4. Return to 6 A and confirm current falls.
5. Press Pause and confirm current reaches zero while the transaction ID remains active.
6. Press Resume and confirm the same transaction continues.
7. Press Stop and confirm transaction ID 0, zero current, and `Finishing` or `Available`.

Do not judge success from a button press or service response alone. Use the live current, power, status and transaction entities.
