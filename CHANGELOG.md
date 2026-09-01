# Changelog

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
