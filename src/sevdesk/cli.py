from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .api import (
    create_voucher,
    fetch_all_accounting_types,
    fetch_all_check_accounts,
    load_env_fallback,
    read_token,
    request_accounting_types,
    request_voucher_by_id,
    request_vouchers,
)
from .booking import book_voucher_to_check_account
from .constants import (
    DEFAULT_BASE_URL,
    DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH,
    DEFAULT_TEMPLATE_PATH,
    DEFAULT_ZAHLUNGSKONTEN_EXPORT_PATH,
)
from .voucher import (
    apply_account_assignment_to_payload,
    first_object_from_response,
    known_buchunggskonto_ids,
    load_buchunggskonten,
    load_create_input,
    load_zahlungskonten,
    normalize_create_payload,
    print_create_result,
    print_rows,
    select_buchunggskonto,
    select_zahlungskonto,
    validate_create_payload,
    write_json,
    write_template,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Work with sevDesk "Belege" (Voucher).')
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="sevDesk API base URL",
    )

    subparsers = parser.add_subparsers(dest="command")

    latest_parser = subparsers.add_parser("latest", help="Show Belegverwaltung")
    latest_parser.add_argument("--limit", type=int, default=10, help="Number of Belege to show")

    accounting_types_parser = subparsers.add_parser(
        "accounting-types",
        help="List available sevDesk accounting types",
    )
    accounting_types_parser.add_argument("--limit", type=int, default=100, help="Max rows to fetch")
    accounting_types_parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    accounting_types_parser.add_argument("--sort", default="id", help="Sort field passed to API")
    accounting_types_parser.add_argument(
        "--name-contains",
        default="",
        help="Optional case-insensitive name filter",
    )
    accounting_types_parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive accounting types",
    )

    export_buchunggskonten_parser = subparsers.add_parser(
        "export-buchunggskonten",
        help="Export sevDesk accounting types to a German-named JSON file",
    )
    export_buchunggskonten_parser.add_argument(
        "--output",
        default=str(DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH),
        help='Output JSON path (default: data/sevdesk/informationen/buchunggskonten.json)',
    )
    export_buchunggskonten_parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive accounting types in export",
    )
    export_buchunggskonten_parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Page size for API pagination",
    )

    export_zahlungskonten_parser = subparsers.add_parser(
        "export-zahlungskonten",
        help="Export sevDesk payment accounts (Zahlungskonten) to a German-named JSON file",
    )
    export_zahlungskonten_parser.add_argument(
        "--output",
        default=str(DEFAULT_ZAHLUNGSKONTEN_EXPORT_PATH),
        help="Output JSON path (default: data/sevdesk/informationen/zahlungskonten.json)",
    )
    export_zahlungskonten_parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive payment accounts in export (status != 100)",
    )
    export_zahlungskonten_parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Page size for API pagination",
    )

    template_parser = subparsers.add_parser(
        "template",
        help="Write a complete dummy input JSON for Beleg creation",
    )
    template_parser.add_argument(
        "--output",
        default=str(DEFAULT_TEMPLATE_PATH),
        help="Where to write the dummy input JSON",
    )
    template_parser.add_argument(
        "--input-template",
        default="",
        help="Optional existing template JSON to reuse as base and only re-assign accounts",
    )
    template_parser.add_argument(
        "--buchunggskonten-file",
        default=str(DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH),
        help="Path to exported buchunggskonten JSON used to choose a valid accountingType.id",
    )
    template_parser.add_argument(
        "--zahlungskonten-file",
        default=str(DEFAULT_ZAHLUNGSKONTEN_EXPORT_PATH),
        help="Path to exported zahlungskonten JSON used to choose checkAccount.id",
    )
    template_parser.add_argument(
        "--accounting-type-id",
        default="",
        help="Explicit accountingType.id to force into template",
    )
    template_parser.add_argument(
        "--accounting-type-name-contains",
        default="",
        help="Case-insensitive name fragment to select accounting type (e.g. 'Verrechnungskonto')",
    )
    template_parser.add_argument(
        "--check-account-id",
        default="",
        help="Explicit checkAccount.id to force into template",
    )
    template_parser.add_argument(
        "--check-account-name",
        default="",
        help="Case-insensitive payment account name to select (e.g. 'APITEST')",
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Create a Beleg from JSON input compatible with Voucher/Factory/saveVoucher",
    )
    create_parser.add_argument(
        "--input",
        default=str(DEFAULT_TEMPLATE_PATH),
        help="Path to JSON input file",
    )
    create_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print payload only, do not call sevDesk",
    )

    assign_latest_parser = subparsers.add_parser(
        "assign-from-latest",
        help=(
            "Find matching Beleg in latest list and either update booking account "
            "(accountingType) or book it onto a payment account"
        ),
    )
    assign_latest_parser.add_argument(
        "--input",
        default=str(DEFAULT_TEMPLATE_PATH),
        help="Template JSON used as base for payload fields and matching (description)",
    )
    assign_latest_parser.add_argument(
        "--latest-limit",
        type=int,
        default=25,
        help="Number of Belegverwaltung to inspect when identifying the correct one",
    )
    assign_latest_parser.add_argument(
        "--match-description",
        default="",
        help="Explicit description to match in Belegverwaltung. Defaults to template voucher.description",
    )
    assign_latest_parser.add_argument(
        "--buchunggskonten-file",
        default=str(DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH),
        help="Path to exported buchunggskonten JSON",
    )
    assign_latest_parser.add_argument(
        "--zahlungskonten-file",
        default=str(DEFAULT_ZAHLUNGSKONTEN_EXPORT_PATH),
        help="Path to exported zahlungskonten JSON",
    )
    assign_latest_parser.add_argument(
        "--accounting-type-id",
        default="",
        help="Explicit accountingType.id to set during assignment",
    )
    assign_latest_parser.add_argument(
        "--accounting-type-name-contains",
        default="",
        help="Case-insensitive accounting type fragment to set (optional)",
    )
    assign_latest_parser.add_argument(
        "--check-account-id",
        default="",
        help="Explicit checkAccount.id to set during assignment",
    )
    assign_latest_parser.add_argument(
        "--check-account-name",
        default="APITEST",
        help="Case-insensitive checkAccount name to set (default: APITEST)",
    )
    assign_latest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print identified ID and payload only, do not call sevDesk write endpoints",
    )

    parser.set_defaults(command="latest")
    return parser.parse_args()


def run_latest(args: argparse.Namespace, token: str) -> int:
    vouchers = request_vouchers(args.base_url, token, args.limit)
    print_rows(vouchers)
    return 0


def run_accounting_types(args: argparse.Namespace, token: str) -> int:
    rows = request_accounting_types(args.base_url, token, args.limit, args.offset, args.sort)

    name_filter = args.name_contains.strip().lower()
    if name_filter:
        rows = [row for row in rows if name_filter in str(row.get("name", "")).lower()]

    if not args.include_inactive:
        rows = [row for row in rows if str(row.get("active", "1")) == "1"]

    if not rows:
        print("No accounting types found.")
        return 0

    print(f"Found {len(rows)} accounting types:")
    print("-" * 125)
    print(f"{'ID':<10} {'Name':<52} {'Type':<8} {'SKR03':<10} {'SKR04':<10} {'Active':<8} {'Status':<8}")
    print("-" * 125)
    for row in rows:
        id_value = str(row.get("id", "-"))
        name = str(row.get("name", "-")).replace("\n", " ").strip()
        type_value = str(row.get("type", "-"))
        skr03 = str(row.get("skr03", "-"))
        skr04 = str(row.get("skr04", "-"))
        active = str(row.get("active", "-"))
        status = str(row.get("status", "-"))
        print(f"{id_value:<10} {name[:52]:<52} {type_value:<8} {skr03:<10} {skr04:<10} {active:<8} {status:<8}")

    return 0


def run_export_buchunggskonten(args: argparse.Namespace, token: str) -> int:
    rows = fetch_all_accounting_types(args.base_url, token, args.page_size, "id")
    if not args.include_inactive:
        rows = [row for row in rows if str(row.get("active", "1")) == "1"]

    essential_rows: list[dict[str, object]] = []
    for row in rows:
        essential_rows.append(
            {
                "id": str(row.get("id", "")),
                "name": str(row.get("name", "")).strip(),
                "type": str(row.get("type", "")),
                "skr03": row.get("skr03"),
                "skr04": row.get("skr04"),
                "active": str(row.get("active", "0")) == "1",
                "status": str(row.get("status", "")),
            }
        )

    payload = {
        "informationsart": "buchunggskonten",
        "quelle": "sevdesk",
        "quelle_endpoint": "/AccountingType",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "filter": {
            "include_inactive": bool(args.include_inactive),
        },
        "feldschema": [
            "id",
            "name",
            "type",
            "skr03",
            "skr04",
            "active",
            "status",
        ],
        "anzahl": len(essential_rows),
        "daten": essential_rows,
    }

    output_path = Path(args.output)
    write_json(output_path, payload)

    print(f"Export written: {output_path}")
    print(f"Datensaetze: {len(essential_rows)}")
    return 0


def run_export_zahlungskonten(args: argparse.Namespace, token: str) -> int:
    rows = fetch_all_check_accounts(args.base_url, token, args.page_size, "id")
    if not args.include_inactive:
        rows = [row for row in rows if str(row.get("status", "")) == "100"]

    essential_rows: list[dict[str, object]] = []
    for row in rows:
        essential_rows.append(
            {
                "id": str(row.get("id", "")),
                "name": str(row.get("name", "")).strip(),
                "type": str(row.get("type", "")),
                "currency": row.get("currency"),
                "defaultAccount": str(row.get("defaultAccount", "0")) == "1",
                "status": str(row.get("status", "")),
                "accountingNumber": row.get("accountingNumber"),
                "iban": row.get("iban"),
                "bic": row.get("bic"),
                "bankServer": row.get("bankServer"),
                "lastSync": row.get("lastSync"),
            }
        )

    payload = {
        "informationsart": "zahlungskonten",
        "quelle": "sevdesk",
        "quelle_endpoint": "/CheckAccount",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "filter": {
            "include_inactive": bool(args.include_inactive),
        },
        "feldschema": [
            "id",
            "name",
            "type",
            "currency",
            "defaultAccount",
            "status",
            "accountingNumber",
            "iban",
            "bic",
            "bankServer",
            "lastSync",
        ],
        "anzahl": len(essential_rows),
        "daten": essential_rows,
    }

    output_path = Path(args.output)
    write_json(output_path, payload)

    print(f"Export written: {output_path}")
    print(f"Datensaetze: {len(essential_rows)}")
    return 0


def run_template(args: argparse.Namespace) -> int:
    output = Path(args.output)
    input_template = Path(args.input_template) if args.input_template.strip() else None
    buchunggskonten_file = Path(args.buchunggskonten_file)
    zahlungskonten_file = Path(args.zahlungskonten_file)
    buchunggskonten = load_buchunggskonten(buchunggskonten_file)
    zahlungskonten = load_zahlungskonten(zahlungskonten_file)

    has_accounting_override = bool(
        args.accounting_type_id.strip() or args.accounting_type_name_contains.strip()
    )
    selected_buchunggskonto = None
    if has_accounting_override:
        selected_buchunggskonto = select_buchunggskonto(
            buchunggskonten,
            accounting_type_id=args.accounting_type_id,
            accounting_type_name_contains=args.accounting_type_name_contains,
        )
    if has_accounting_override and selected_buchunggskonto is None:
        raise RuntimeError(
            "Requested accounting type not found. "
            f"accounting_type_id={args.accounting_type_id!r} "
            f"accounting_type_name_contains={args.accounting_type_name_contains!r}"
        )

    selected_zahlungskonto = select_zahlungskonto(
        zahlungskonten,
        check_account_id=args.check_account_id,
        check_account_name=args.check_account_name,
    )
    if (args.check_account_id.strip() or args.check_account_name.strip()) and selected_zahlungskonto is None:
        raise RuntimeError(
            "Requested check account not found. "
            f"check_account_id={args.check_account_id!r} "
            f"check_account_name={args.check_account_name!r}"
        )

    base_payload = None
    if input_template is not None:
        if input_template.exists():
            base_payload = load_create_input(input_template)
        else:
            print(
                f"input-template not found ({input_template}), falling back to default dummy template",
                file=sys.stderr,
            )

    write_template(output, selected_buchunggskonto, selected_zahlungskonto, base_payload=base_payload)

    print(f"Template written: {output}")
    if selected_buchunggskonto is not None:
        print(
            "Using buchunggskonto "
            f"id={selected_buchunggskonto.get('id')} "
            f"name={str(selected_buchunggskonto.get('name', '-')).strip()}"
        )
    elif has_accounting_override:
        print("No matching buchunggskonto selected.", file=sys.stderr)
    else:
        print("No buchunggskonto override requested - existing accountingType is kept.")

    if selected_zahlungskonto is not None:
        print(
            "Using zahlungskonto "
            f"id={selected_zahlungskonto.get('id')} "
            f"name={str(selected_zahlungskonto.get('name', '-')).strip()}"
        )
    elif args.check_account_id.strip() or args.check_account_name.strip():
        print("No matching zahlungskonto selected.", file=sys.stderr)
    return 0


def run_assign_from_latest(args: argparse.Namespace, token: str) -> int:
    input_path = Path(args.input)
    base_template = load_create_input(input_path)

    latest_vouchers = request_vouchers(args.base_url, token, args.latest_limit)
    if not latest_vouchers:
        raise RuntimeError("No Belegverwaltung found to identify target voucher.")

    print(f"Checked Belegverwaltung: {len(latest_vouchers)} candidates")

    template_voucher = base_template.get("voucher")
    template_description = ""
    if isinstance(template_voucher, dict):
        template_description = str(template_voucher.get("description", "")).strip()
    match_description = args.match_description.strip() or template_description
    if not match_description:
        raise RuntimeError(
            "Could not identify voucher: no --match-description provided and template voucher.description is empty."
        )

    matches = [
        row
        for row in latest_vouchers
        if str(row.get("description", "")).strip().lower() == match_description.lower()
    ]
    if not matches:
        raise RuntimeError(
            f"No Beleg found in latest {len(latest_vouchers)} with description={match_description!r}."
        )
    if len(matches) > 1:
        ids = ", ".join(str(row.get("id", "-")) for row in matches)
        raise RuntimeError(
            f"Ambiguous match for description={match_description!r}. Matching ids: {ids}. "
            "Please refine with --match-description."
        )

    target_voucher_id = str(matches[0].get("id", "")).strip()
    if not target_voucher_id:
        raise RuntimeError("Matched Beleg has no id.")
    print(f"Identified target Beleg id={target_voucher_id} by description={match_description!r}")

    buchunggskonten = load_buchunggskonten(Path(args.buchunggskonten_file))
    zahlungskonten = load_zahlungskonten(Path(args.zahlungskonten_file))

    has_accounting_override = bool(
        args.accounting_type_id.strip() or args.accounting_type_name_contains.strip()
    )
    selected_buchunggskonto = None
    if has_accounting_override:
        selected_buchunggskonto = select_buchunggskonto(
            buchunggskonten,
            accounting_type_id=args.accounting_type_id,
            accounting_type_name_contains=args.accounting_type_name_contains,
        )
    if has_accounting_override and selected_buchunggskonto is None:
        raise RuntimeError(
            "Requested accounting type not found. "
            f"accounting_type_id={args.accounting_type_id!r} "
            f"accounting_type_name_contains={args.accounting_type_name_contains!r}"
        )

    selected_zahlungskonto = select_zahlungskonto(
        zahlungskonten,
        check_account_id=args.check_account_id,
        check_account_name=args.check_account_name,
    )
    if selected_zahlungskonto is None:
        raise RuntimeError(
            "Requested check account not found. "
            f"check_account_id={args.check_account_id!r} "
            f"check_account_name={args.check_account_name!r}"
        )

    if has_accounting_override:
        payload = apply_account_assignment_to_payload(
            base_template,
            selected_buchunggskonto,
            selected_zahlungskonto,
        )
        voucher = payload.get("voucher")
        if not isinstance(voucher, dict):
            raise RuntimeError("Template payload has invalid voucher object.")
        voucher["id"] = target_voucher_id

        known_accounting_type_ids = known_buchunggskonto_ids(
            load_buchunggskonten(DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH)
        )
        errors = validate_create_payload(
            payload,
            known_accounting_type_ids if known_accounting_type_ids else None,
        )
        if errors:
            raise RuntimeError("Input validation failed: " + "; ".join(errors))

        if args.dry_run:
            print("Dry-run successful. Payload to be sent:")
            print(json.dumps(payload, indent=2, ensure_ascii=True))
            return 0

        existing = request_voucher_by_id(args.base_url, token, target_voucher_id)
        if existing is None:
            raise RuntimeError(
                f"Safety check failed: target Beleg id={target_voucher_id} not found before update."
            )
        before_update = str(existing.get("update", "")).strip()

        response_payload = create_voucher(args.base_url, token, payload)
        first_object = first_object_from_response(response_payload)
        response_id = str(first_object.get("id", "")).strip() if isinstance(first_object, dict) else ""
        if response_id and response_id != target_voucher_id:
            raise RuntimeError(
                f"Safety check failed: expected id={target_voucher_id} but API returned id={response_id}. "
                "Aborting because this may have created a different Beleg."
            )

        updated = request_voucher_by_id(args.base_url, token, target_voucher_id)
        if updated is None:
            raise RuntimeError(
                f"Post-update verification failed: could not load Beleg id={target_voucher_id}."
            )
        after_update = str(updated.get("update", "")).strip()
        if before_update and after_update and before_update == after_update:
            raise RuntimeError(
                "Post-update verification failed: voucher update timestamp did not change "
                f"(still {after_update})."
            )

        print_create_result(response_payload, operation="updated")
        print(
            "Verified target Beleg update via timestamp change "
            f"{before_update or '-'} -> {after_update or '-'}."
        )
        return 0

    selected_check_account_id = str(selected_zahlungskonto.get("id", "")).strip()
    if not selected_check_account_id:
        raise RuntimeError("Selected zahlungskonto has no id.")

    if args.dry_run:
        dry_run_result = book_voucher_to_check_account(
            args.base_url,
            token,
            target_voucher_id,
            selected_check_account_id,
            dry_run=True,
        )
        print("Dry-run successful. Booking payload to be sent:")
        print(json.dumps(dry_run_result["booking_payload"], indent=2, ensure_ascii=True))
        return 0

    booking_result = book_voucher_to_check_account(
        args.base_url,
        token,
        target_voucher_id,
        selected_check_account_id,
    )
    first_object = booking_result.get("response_object")
    print("Beleg booked successfully.")
    if isinstance(first_object, dict):
        print(f"id: {first_object.get('id', '-')}")
        print(f"fromStatus: {first_object.get('fromStatus', '-')}")
        print(f"toStatus: {first_object.get('toStatus', '-')}")
        print(f"amountPayed: {first_object.get('amountPayed', '-')}")
        print(f"bookingDate: {first_object.get('bookingDate', '-')}")
    print(
        f"Verified target Beleg id={booking_result['voucher_id']}: "
        f"status {booking_result['before_status'] or '-'} -> {booking_result['after_status'] or '-'}, "
        f"paidAmount {booking_result['before_paid_amount']} -> {booking_result['after_paid_amount']}, "
        f"payDate={booking_result['pay_date'] or '-'}."
    )
    return 0


def run_create(args: argparse.Namespace, token: str) -> int:
    input_path = Path(args.input)
    raw_payload = load_create_input(input_path)
    payload = normalize_create_payload(raw_payload)

    known_accounting_type_ids = known_buchunggskonto_ids(
        load_buchunggskonten(DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH)
    )
    errors = validate_create_payload(
        payload,
        known_accounting_type_ids if known_accounting_type_ids else None,
    )
    if errors:
        print("Input validation failed:", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry-run successful. Payload to be sent:")
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0

    response_payload = create_voucher(args.base_url, token, payload)
    print_create_result(response_payload)
    return 0


def main() -> int:
    load_env_fallback()
    args = parse_args()

    if args.command in {
        "latest",
        "create",
        "accounting-types",
        "export-buchunggskonten",
        "export-zahlungskonten",
        "assign-from-latest",
    }:
        token = read_token()
        if not token:
            print(
                "Missing SEVDESK_KEY / SEVDEKS_KEY "
                "(or SEVDESK_API_TOKEN / SEVDESK_API_KEY) in environment/.env",
                file=sys.stderr,
            )
            return 1

    try:
        if args.command == "latest":
            return run_latest(args, token)
        if args.command == "accounting-types":
            return run_accounting_types(args, token)
        if args.command == "export-buchunggskonten":
            return run_export_buchunggskonten(args, token)
        if args.command == "export-zahlungskonten":
            return run_export_zahlungskonten(args, token)
        if args.command == "template":
            return run_template(args)
        if args.command == "create":
            return run_create(args, token)
        if args.command == "assign-from-latest":
            return run_assign_from_latest(args, token)
        print(f"Unsupported command: {args.command}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
