from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.accounting.invoice_payment_analysis import analyze_invoice_payment_csv


def _format_eur(value: object) -> str:
    return f"{value} EUR"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze invoice CSV payment methods and SumUp storno corrections."
    )
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    analysis = analyze_invoice_payment_csv(args.csv_path.read_bytes())

    print(f"Rows: {analysis.row_count}")
    print(f"SUMME aller Zahlungsarten: {_format_eur(analysis.all_payment_total)}")
    print(f"SUMME Barzahlung: {_format_eur(analysis.cash_total)}")
    print(f"SUMME Barzahlung korrigiert: {_format_eur(analysis.corrected_cash_total)}")
    print(f"Kartenzahlung SUMME: {_format_eur(analysis.sumup_total)}")
    print(f"Kartenzahlung SUMME korrigiert: {_format_eur(analysis.corrected_sumup_total)}")
    print(
        "SUMME negativer SUMUP Stornokorrekturen: "
        f"{_format_eur(analysis.sumup_storno_correction_total)}"
    )
    print()
    print("Zahlungsarten:")
    for payment_name, amount in analysis.payment_totals.items():
        corrected_amount = analysis.corrected_payment_totals.get(payment_name, amount)
        print(
            f"- {payment_name}: {_format_eur(amount)} "
            f"(korrigiert: {_format_eur(corrected_amount)})"
        )


if __name__ == "__main__":
    main()
