from __future__ import annotations

import questionary
from rich.console import Console

from .client import KentikClient
from .filter import filter_devices
from .migrate import print_candidate_table, run_migration

_console = Console()
_QUIT = "__quit__"
_NO_FILTER = "__no_filter__"
_QUIT_CHOICE = questionary.Choice("← Quit", value=_QUIT)
_STYLE = questionary.Style([
    ("highlighted", "fg:#ffffff bg:#ff6600 bold"),
    ("pointer",     "fg:#ff6600 bold"),
    ("question",    "bold"),
    ("answer",      "fg:#ff6600 bold"),
])


def _aborted(value: object) -> bool:
    """True when questionary returns None (Ctrl+C) or the Quit sentinel."""
    return value is None or value == _QUIT


def prompt_run(client: KentikClient, debug: bool = False) -> None:
    _console.print("Fetching devices from Kentik API…")
    devices = client.list_devices()

    if not devices:
        _console.print("[red]No devices found.[/]")
        return

    # Names in use across all devices
    device_cred_names = {
        cred
        for d in devices
        if (cred := (d.get("nms") or {}).get("snmp", {}).get("credentialName"))
    }

    # Intersect with vault v1/v2c entries so only true v2 creds appear
    try:
        v2_vault = set(client.list_snmpv2_credentials())
        cred_names = sorted(device_cred_names & v2_vault) if v2_vault \
            else sorted(device_cred_names)
    except RuntimeError:
        cred_names = sorted(device_cred_names)

    if not cred_names:
        _console.print("[red]No devices with NMS SNMP credentials found.[/]")
        return

    old_credential = questionary.select(
        "Select the current (v2) SNMP credential to migrate from:",
        choices=[*cred_names, questionary.Separator(), _QUIT_CHOICE],
        style=_STYLE,
    ).ask()
    if _aborted(old_credential):
        _console.print("Aborted.")
        return

    try:
        v3_creds = client.list_snmpv3_credentials()
    except RuntimeError:
        v3_creds = []

    if v3_creds:
        new_credential = questionary.select(
            "Select the new (v3) SNMP credential:",
            choices=[*v3_creds, questionary.Separator(), _QUIT_CHOICE],
            style=_STYLE,
        ).ask()
        if _aborted(new_credential):
            _console.print("Aborted.")
            return
    else:
        # Fall back to text when credential API is unavailable or returns nothing
        _console.print(
            "[yellow]No SNMPv3 credentials found in vault "
            "(or admin.credential:read scope missing). "
            "Enter the name manually.[/]"
        )
        new_credential = questionary.text(
            "Enter the new (v3) credential name (empty to quit):",
        ).ask()
        if not (new_credential and new_credential.strip()):
            _console.print("Aborted.")
            return
        new_credential = new_credential.strip()

    candidates = filter_devices(devices, old_credential)
    if not candidates:
        _console.print(
            f"[yellow]No devices found with credential '{old_credential}'.[/]"
        )
        return

    # Optional site filter derived from current candidate set
    site_names = sorted({
        name
        for d in candidates
        if (name := (d.get("site") or {}).get("siteName"))
    })
    if site_names:
        site_answer = questionary.select(
            f"Filter by site? ({len(site_names)} available)",
            choices=[
                questionary.Choice("No filter — include all sites", value=_NO_FILTER),
                *site_names,
                questionary.Separator(),
                _QUIT_CHOICE,
            ],
            style=_STYLE,
        ).ask()
        if _aborted(site_answer):
            _console.print("Aborted.")
            return
        if site_answer != _NO_FILTER:
            candidates = [
                d for d in candidates
                if (d.get("site") or {}).get("siteName") == site_answer
            ]

    # Optional vendor filter derived from current candidate set
    vendor_names = sorted({
        v for d in candidates if (v := d.get("deviceVendorType"))
    })
    if vendor_names:
        vendor_answer = questionary.select(
            f"Filter by vendor? ({len(vendor_names)} available)",
            choices=[
                questionary.Choice("No filter — include all vendors", value=_NO_FILTER),
                *vendor_names,
                questionary.Separator(),
                _QUIT_CHOICE,
            ],
            style=_STYLE,
        ).ask()
        if _aborted(vendor_answer):
            _console.print("Aborted.")
            return
        if vendor_answer != _NO_FILTER:
            candidates = [
                d for d in candidates
                if d.get("deviceVendorType") == vendor_answer
            ]

    # Optional label filter derived from current candidate set
    label_names = sorted({
        lbl["name"]
        for d in candidates
        for lbl in d.get("labels", [])
        if lbl.get("name")
    })
    if label_names:
        label_answer = questionary.select(
            f"Filter by label? ({len(label_names)} available)",
            choices=[
                questionary.Choice("No filter — include all labels", value=None),
                *label_names,
                questionary.Separator(),
                _QUIT_CHOICE,
            ],
            style=_STYLE,
        ).ask()
        if _aborted(label_answer):
            _console.print("Aborted.")
            return
        if label_answer != _NO_FILTER:
            candidates = [
                d for d in candidates
                if any(lbl.get("name") == label_answer
                       for lbl in d.get("labels", []))
            ]

    if not candidates:
        _console.print("[yellow]No candidates after filtering.[/]")
        return

    print_candidate_table(candidates, new_credential)
    _console.print()

    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Dry-run (preview only, no changes)", value="dry"),
            questionary.Choice("Apply changes", value="apply"),
            questionary.Separator(),
            _QUIT_CHOICE,
        ],
        style=_STYLE,
    ).ask()

    if _aborted(action):
        _console.print("Aborted.")
        return

    run_migration(
        client=client,
        candidates=candidates,
        new_credential=new_credential,
        dry_run=(action == "dry"),
        debug=debug,
    )
