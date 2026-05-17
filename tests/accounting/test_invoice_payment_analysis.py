from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.accounting.invoice_payment_analysis import analyze_invoice_payment_csv


def test_analyze_invoice_payment_csv_splits_payment_methods_and_sumup_stornos() -> None:
    csv_text = "\n".join(
        [
            '"Rechnungsnummer";"Rechnungsdatum";"Zahlungsarten";"Bezahlt am";"Storniert am";"Retourgebucht wegen";"Interne Rechnungsreferenz"',
            '"RG1";"2026-04-01 10:00:00";"SumUp: 10,5";"2026-04-01";;""',
            '"RG2";"2026-04-01 11:00:00";"Gutschein: 5; SumUp: 6,1";"2026-04-01";;""',
            '"RG3";"2026-04-01 12:00:00";"SumUp: 20,6";"storniert";"2026-04-01";"Korrektur";"RG4"',
            '"RG4";"2026-04-01 12:01:00";"SumUp: -6,5";"storniert";;"Korrektur";"RG3"',
            '"RG5";"2026-04-01 13:00:00";"Barzahlung: 7,4";"2026-04-01";;""',
        ]
    )

    analysis = analyze_invoice_payment_csv(csv_text)

    assert analysis.row_count == 5
    assert analysis.payment_totals == {
        "Barzahlung": Decimal("7.40"),
        "Gutschein": Decimal("5.00"),
        "SumUp": Decimal("30.70"),
    }
    assert analysis.all_payment_total == Decimal("43.10")
    assert analysis.cash_total == Decimal("7.40")
    assert analysis.corrected_cash_total == Decimal("0.90")
    assert analysis.sumup_total == Decimal("30.70")
    assert analysis.corrected_sumup_total == Decimal("37.20")
    assert analysis.sumup_storno_correction_total == Decimal("6.50")
    assert analysis.corrected_payment_totals == {
        "Barzahlung": Decimal("0.90"),
        "Gutschein": Decimal("5.00"),
        "SumUp": Decimal("37.20"),
    }
    assert [row["Rechnungsnummer"] for row in analysis.sumup_storno_correction_rows] == ["RG4"]
    assert [row["Rechnungsnummer"] for row in analysis.sumup_storno_rows] == ["RG3", "RG4"]


def test_analyze_invoice_payment_csv_reproduces_workspace_sumup_total() -> None:
    path = Path("workspace/rechnungen_suedseite-coffee2_2026-04-01_2026-04-30.csv")
    analysis = analyze_invoice_payment_csv(path.read_bytes())

    assert analysis.payment_totals["SumUp"] == Decimal("39834.47")
    assert analysis.payment_totals["Barzahlung"] == Decimal("11580.98")
    assert analysis.corrected_payment_totals["Barzahlung"] == Decimal("11545.21")
    assert analysis.corrected_payment_totals["SumUp"] == Decimal("39870.24")
    assert analysis.corrected_cash_total == Decimal("11545.21")
    assert analysis.corrected_sumup_total == Decimal("39870.24")
    assert analysis.sumup_storno_correction_total == Decimal("35.77")
