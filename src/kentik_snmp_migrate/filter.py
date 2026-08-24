from __future__ import annotations


def filter_devices(
    devices: list[dict],
    old_credential: str,
    site: str | None = None,
    vendor: str | None = None,
    label: str | None = None,
) -> list[dict]:
    results = []
    for device in devices:
        nms = device.get("nms") or {}
        snmp = nms.get("snmp") or {}
        if snmp.get("credentialName") != old_credential:
            continue
        if site is not None:
            if (device.get("site") or {}).get("siteName") != site:
                continue
        if vendor is not None:
            if device.get("deviceVendorType") != vendor:
                continue
        if label is not None:
            device_labels = {lbl.get("name") for lbl in device.get("labels", [])}
            if label not in device_labels:
                continue
        results.append(device)
    return results
