from __future__ import annotations

import httpx

_API_VERSION = "v202504beta2"
_CRED_API_VERSION = "v202407alpha1"
_LABEL_API_VERSION = "v202210"
_SNMP_V3_TYPE = "SECRET_TYPE_SNMP_V3"
_SNMP_V2_TYPES = {"SECRET_TYPE_SNMP_V1", "SECRET_TYPE_SNMP_V2C"}


class KentikClient:
    def __init__(self, email: str, token: str, base_url: str) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "X-CH-Auth-Email": email,
                "X-CH-Auth-API-Token": token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def list_devices(self) -> list[dict]:
        response = self._client.get(f"/device/{_API_VERSION}/device")
        _raise_for_status(response)
        return response.json().get("devices", [])

    def get_device(self, device_id: str) -> dict:
        response = self._client.get(
            f"/device/{_API_VERSION}/device/{device_id}"
        )
        _raise_for_status(response)
        return response.json()["device"]

    def update_device(self, device_id: str, payload: dict) -> dict:
        response = self._client.put(
            f"/device/{_API_VERSION}/device/{device_id}",
            json=payload,
        )
        _raise_for_status(response)
        return response.json()["device"]

    def update_devices_batch(self, payloads: list[dict]) -> list[dict]:
        response = self._client.put(
            f"/device/{_API_VERSION}/device/batch_update",
            json={"devices": payloads},
        )
        _raise_for_status(response)
        data = response.json()
        if failed := data.get("failed_devices"):
            raise RuntimeError(
                f"Batch update reported failures: "
                f"{', '.join(str(f) for f in failed)}"
            )
        return data.get("devices", [])

    def list_snmpv3_credentials(self) -> list[str]:
        """Returns names of vault credential groups with type SNMP v3."""
        response = self._client.get(
            f"/credential/{_CRED_API_VERSION}/group"
        )
        _raise_for_status(response)
        groups = response.json().get("groups", [])
        return sorted(
            g["name"]
            for g in groups
            if g.get("type") == _SNMP_V3_TYPE and g.get("name")
        )

    def list_snmpv2_credentials(self) -> list[str]:
        """Returns names of vault credential groups with type SNMP v1 or v2c."""
        response = self._client.get(
            f"/credential/{_CRED_API_VERSION}/group"
        )
        _raise_for_status(response)
        groups = response.json().get("groups", [])
        return sorted(
            g["name"]
            for g in groups
            if g.get("type") in _SNMP_V2_TYPES and g.get("name")
        )

    def list_labels(self) -> list[dict]:
        """Returns all labels configured in the org."""
        response = self._client.get(f"/label/{_LABEL_API_VERSION}/labels")
        _raise_for_status(response)
        return response.json().get("labels", [])

    def close(self) -> None:
        self._client.close()


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        raise RuntimeError(
            f"API error {response.status_code}: {response.text[:500]}"
        )
