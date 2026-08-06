from __future__ import annotations

import argparse
import sys

from pydantic import BaseModel, EmailStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich.console import Console

from .client import KentikClient
from .filter import filter_devices
from .migrate import print_candidate_table, run_migration

_console = Console()


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    kentik_email: EmailStr
    kentik_api_token: str
    kentik_api_base_url: str = "https://grpc.api.kentik.com"

    @field_validator("kentik_api_token")
    @classmethod
    def token_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cannot be empty")
        return v


class _CredentialArgs(BaseModel):
    old_credential: str
    new_credential: str

    @field_validator("old_credential", "new_credential", mode="before")
    @classmethod
    def not_blank(cls, v: object) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("credential name cannot be empty or whitespace")
        return s


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kentik-snmp-migrate",
        description="Migrate Kentik device SNMP credentials from v2 to v3.",
    )
    parser.add_argument(
        "--old-credential", metavar="NAME",
        help="Current (v2) credential name to match",
    )
    parser.add_argument(
        "--new-credential", metavar="NAME",
        help="New (v3) credential name to set",
    )
    parser.add_argument("--site", metavar="SITE_NAME", help="Exact site name filter")
    parser.add_argument(
        "--vendor", metavar="VENDOR_TYPE", help="Exact device_vendor_type filter"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without writing to the API",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print the exact JSON body before each API write",
    )
    args = parser.parse_args()

    try:
        settings = _Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        _console.print(
            "[red]Configuration error — check your .env file:[/]\n"
            f"{exc}"
        )
        sys.exit(1)

    client = KentikClient(
        email=str(settings.kentik_email),
        token=settings.kentik_api_token,
        base_url=settings.kentik_api_base_url,
    )
    try:
        if args.old_credential and args.new_credential:
            try:
                creds = _CredentialArgs(
                    old_credential=args.old_credential,
                    new_credential=args.new_credential,
                )
            except ValidationError as exc:
                _console.print(f"[red]Invalid credential argument:[/]\n{exc}")
                sys.exit(1)
            args.old_credential = creds.old_credential
            args.new_credential = creds.new_credential
            _run_noninteractive(client, args)
        else:
            from .interactive import prompt_run
            prompt_run(client, debug=args.debug)
    finally:
        client.close()


def _run_noninteractive(client: KentikClient, args: argparse.Namespace) -> None:
    _console.print("Fetching devices…")
    devices = client.list_devices()
    candidates = filter_devices(
        devices,
        old_credential=args.old_credential,
        site=args.site,
        vendor=args.vendor,
    )

    if not candidates:
        _console.print(
            f"[yellow]No devices found with credential "
            f"'{args.old_credential}'.[/]"
        )
        return

    print_candidate_table(candidates, args.new_credential)
    run_migration(
        client=client,
        candidates=candidates,
        new_credential=args.new_credential,
        dry_run=args.dry_run,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
