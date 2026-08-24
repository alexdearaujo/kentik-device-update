# kentik-device-update

Python CLI that migrates Kentik devices from an SNMPv2 vault credential to an
SNMPv3 vault credential by swapping `nms.snmp.credentialName` on each matching
device record via the Kentik Device API. No vault reads, no inline v3 fields —
purely a credential name update.

## Architecture

```text
┌─────────────────────────────────────────────────────┐
│  Entry point: src/kentik_snmp_migrate/main.py       │
│                                                     │
│  Interactive mode          Non-interactive / CI     │
│  (no flags)                (--old/--new-credential) │
│       │                           │                 │
│       └──────────┬────────────────┘                 │
│                  ▼                                  │
│         KentikClient.list_devices()                 │
│         GET /device/v202504beta2/device             │
│                  │                                  │
│          filter_devices()                           │
│          old-cred / site / vendor                   │
│                  │                                  │
│         ┌────────▼────────┐                         │
│         │  dry_run=True?  │                         │
│         │  Rich table     │                         │
│         │  (no writes)    │                         │
│         └────────┬────────┘                         │
│                  │ dry_run=False                    │
│   KentikClient.get_device() per candidate           │
│   GET /device/v202504beta2/device/{id}              │
│                  │                                  │
│   build_update_payload()  ← minimal NMS fields only │
│                  │                                  │
│   Chunk ≤ 100 devices                               │
│                  │                                  │
│   KentikClient.update_devices_batch()               │
│   PUT /device/v202504beta2/device/batch_update      │
│   (fallback: PUT /device/v202504beta2/device/{id})  │
└─────────────────────────────────────────────────────┘
```

## Module responsibilities

| File | Responsibility |
| --- | --- |
| `main.py` | Loads `.env`, validates env vars, parses args, dispatches to interactive or non-interactive path |
| `client.py` | `KentikClient`: `list_devices()`, `get_device()`, `update_device()`, `update_devices_batch()` |
| `filter.py` | `filter_devices()`: exact match on old-cred name, optional site/vendor |
| `migrate.py` | `build_update_payload()`, `run_migration()` (dry-run table + live batch update) |
| `interactive.py` | `prompt_run()`: questionary-driven selection flow with D/A/Q confirmation |

## Key design decisions

### Minimal PUT payload only

The Kentik Device API returns deprecated fields in GET responses that cause
errors when echoed back in a PUT. The update payload is deliberately minimal:

```json
{
  "device": {
    "nms": {
      "agentId": "<from GET>",
      "ipAddress": "<from GET>",
      "snmp": {
        "credentialName": "<new-v3-cred>"
      }
    },
    "monitoringTemplateId": 12345
  }
}
```

Rules for optional NMS sub-fields:

- `nms.st` — **must** be included verbatim when present; omitting it clears
  the device's streaming-telemetry configuration (lab confirmed)
- `nms.snmp.port` — omit when value is `0`; Kentik portal defaults to 161 but
  the API returns `0`, and sending `0` resets the device config
- `nms.snmp.timeout` — omit when empty string

### Re-fetch before every update

Each candidate device is re-fetched via `GET /device/{id}` immediately before
building its update payload. This ensures `agentId`, `ipAddress`, and
`monitoringTemplateId` are current even if the list response was cached or the
device was modified between the list call and the update.

### Batch updates preferred, single-device as fallback

`PUT /device/v202504beta2/device/batch_update` accepts up to 100 devices per
request and accepts the same minimal NMS-only payload as the single-device
endpoint (lab confirmed). `run_migration` chunks candidates into groups of
≤ 100. The single-device fallback remains in place for any unexpected
rejection.

### Stop on first error

Live runs halt on the first batch (or single-device call) that returns a
non-2xx response or a non-empty `failed_devices` list. No rollback is
attempted; previously updated devices retain their new credential name. This
limits blast radius while keeping the implementation simple.

### Candidate detection

Devices qualify for migration when `device.nms.snmp.credentialName` exactly
equals the value passed as `--old-credential`. The SNMPv2 community string
(`device_snmp_community`) is left intact.

## API reference

- Base URL: `https://grpc.api.kentik.com` (override via `KENTIK_API_BASE_URL`)
- Auth headers: `X-CH-Auth-Email`, `X-CH-Auth-API-Token`
- Required token scopes: `admin.device:read`, `admin.device:write`

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/device/v202504beta2/device` | List all devices |
| GET | `/device/v202504beta2/device/{id}` | Re-fetch single device before update |
| GET | `/device/v202504beta2/device/name/{name}` | Lookup by device name |
| PUT | `/device/v202504beta2/device/{id}` | Single-device update (fallback) |
| PUT | `/device/v202504beta2/device/batch_update` | Batch update ≤ 100 devices |
| GET | `/label/v202210/labels` | List all org labels (interactive filter) |

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `KENTIK_EMAIL` | Yes | — | Kentik user account email |
| `KENTIK_API_TOKEN` | Yes | — | Kentik API token |
| `KENTIK_API_BASE_URL` | No | `https://grpc.api.kentik.com` | Override for EU or staging |

## CLI modes

### Interactive (default — no flags)

```text
python -m kentik_snmp_migrate
```

Prompt sequence:

1. Select old credential from unique `nms.snmp.credentialName` values
2. Enter new v3 credential name
3. Optionally filter by site (select)
4. Optionally filter by vendor (select)
5. Optionally filter by label (select; derived from current candidate set)
6. Review candidate table (includes Labels column)
7. Choose: Dry-run / Apply / Quit

### Non-interactive / CI

```text
python -m kentik_snmp_migrate \
  --old-credential <name> \
  --new-credential <name> \
  [--site <site_name>] \
  [--vendor <device_vendor_type>] \
  [--label <label_name>] \
  [--dry-run]
```

## Open questions

1. What is the exact set of deprecated keys returned in GET responses that
   trigger errors in PUT? (still to be determined)

## Lab-confirmed findings

1. `batch_update` accepts the same minimal NMS-only payload as single-device
   PUT — no full `DeviceConcise` required.
2. Omitting `nms.st` from a PUT body **clears** streaming-telemetry config on
   devices that have it set. The block must be included verbatim.
3. No fields beyond `monitoringTemplateId` have been found to be silently
   required in the PUT body.

## Scope exclusions

- Does not read credential contents from the vault
- Does not modify `device_snmp_v3_conf` inline fields
- Does not clear `device_snmp_community`
- No pagination (ListDevices returns all devices in a single response)
- No rollback on partial failure
