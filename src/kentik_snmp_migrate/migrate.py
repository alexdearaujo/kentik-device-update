from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from .client import KentikClient

_BATCH_SIZE = 100
_console = Console()


def build_update_payload(device: dict, new_credential: str) -> dict:
    """Returns minimal NMS update fields (no outer 'device' key, no 'id')."""
    nms = device.get("nms") or {}
    snmp = nms.get("snmp") or {}

    snmp_block: dict = {"credentialName": new_credential}
    # port=0 means "use default 161"; sending 0 would reset the device config
    if port := snmp.get("port"):
        snmp_block["port"] = port
    if timeout := snmp.get("timeout"):
        snmp_block["timeout"] = timeout

    nms_block: dict = {
        "agentId": nms["agentId"],
        "ipAddress": nms["ipAddress"],
        "snmp": snmp_block,
    }
    # Omitting st clears streaming-telemetry config (lab confirmed)
    if st := nms.get("st"):
        nms_block["st"] = st

    return {
        "nms": nms_block,
        "monitoringTemplateId": device["monitoringTemplateId"],
    }


def print_candidate_table(devices: list[dict], new_credential: str) -> None:
    table = Table(title="Migration candidates", show_lines=True)
    table.add_column("Device Name", style="cyan")
    table.add_column("Site")
    table.add_column("Vendor")
    table.add_column("Labels", style="dim")
    table.add_column("Agent ID")
    table.add_column("Current Credential", style="yellow")
    table.add_column("New Credential", style="green")

    for d in devices:
        nms = d.get("nms") or {}
        label_names = ", ".join(
            lbl["name"] for lbl in d.get("labels", []) if lbl.get("name")
        )
        table.add_row(
            d.get("deviceName") or d.get("id", "?"),
            (d.get("site") or {}).get("siteName", ""),
            d.get("deviceVendorType", ""),
            label_names,
            nms.get("agentId", ""),
            (nms.get("snmp") or {}).get("credentialName", ""),
            new_credential,
        )

    _console.print(table)


def run_migration(
    client: KentikClient,
    candidates: list[dict],
    new_credential: str,
    dry_run: bool,
    debug: bool = False,
) -> None:
    if dry_run:
        _console.print(
            f"\n[bold yellow]Dry-run:[/] re-fetching {len(candidates)} "
            "device(s) to build exact payloads…"
        )
        fresh = [client.get_device(d["id"]) for d in candidates]
        for d in fresh:
            payload = {"device": build_update_payload(d, new_credential)}
            _console.print(
                f"\n  [cyan]{d.get('deviceName') or d['id']}[/] "
                f"— would PUT:"
            )
            _console.print_json(json.dumps(payload))
        _console.print(
            f"\n[bold yellow]Dry-run complete.[/] "
            f"{len(candidates)} device(s) shown. No changes made."
        )
        return

    _console.print(f"\nUpdating {len(candidates)} device(s)…")

    fresh = [client.get_device(d["id"]) for d in candidates]
    # name_map lets the summary table show a human name instead of a raw ID
    name_map = {d["id"]: d.get("deviceName") or d["id"] for d in fresh}
    batch_items = [
        {"id": d["id"], **build_update_payload(d, new_credential)}
        for d in fresh
    ]

    # results[device_id] = None (success) | str (error message)
    results: dict[str, str | None] = {}

    for i in range(0, len(batch_items), _BATCH_SIZE):
        chunk = batch_items[i : i + _BATCH_SIZE]
        batch_num = i // _BATCH_SIZE + 1
        _console.print(f"  Batch {batch_num}: {len(chunk)} device(s)…", end=" ")
        if debug:
            _console.print()
            _console.print_json(json.dumps({"devices": chunk}))
        try:
            client.update_devices_batch(chunk)
            _console.print("[green]OK[/]")
            for item in chunk:
                results[item["id"]] = None
        except RuntimeError as exc:
            # Batch may reject minimal payloads; fall back to per-device calls
            _console.print(f"[yellow]batch failed ({exc}); falling back…[/]")
            for item in chunk:
                device_id = item["id"]
                payload = {k: v for k, v in item.items() if k != "id"}
                if debug:
                    _console.print_json(json.dumps({"device": payload}))
                try:
                    client.update_device(device_id, {"device": payload})
                    results[device_id] = None
                except RuntimeError as dev_exc:
                    results[device_id] = str(dev_exc)
                    _console.print(f"    [red]✗[/] {name_map[device_id]}: {dev_exc}")
                    break  # stop on first error per design

        if any(v is not None for v in results.values()):
            break  # a device-level failure already halted the inner loop

    _print_results_table(results, name_map, new_credential)


def _print_results_table(
    results: dict[str, str | None],
    name_map: dict[str, str],
    new_credential: str,
) -> None:
    table = Table(title="Migration results", show_lines=True)
    table.add_column("Device Name", style="cyan")
    table.add_column("New Credential")
    table.add_column("Status")

    succeeded = 0
    for device_id, error in results.items():
        if error is None:
            table.add_row(
                name_map.get(device_id, device_id),
                new_credential,
                "[bold green]✓ Updated[/]",
            )
            succeeded += 1
        else:
            table.add_row(
                name_map.get(device_id, device_id),
                new_credential,
                f"[bold red]✗ Failed[/]: {error}",
            )

    _console.print()
    _console.print(table)
    color = "green" if succeeded == len(results) else "yellow"
    _console.print(
        f"\n[bold {color}]{succeeded}/{len(results)} "
        "device(s) updated successfully.[/]"
    )
