"""Fictitious test PDFs for the setup test in CI (no real data).

    python installer/make_test_pdfs.py build/testpdfs
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.sample_docs import make_bank_letter_scan, make_digital_pdf, make_scanned_pdf  # noqa: E402
from tests.signature_docs import make_signed_scan  # noqa: E402

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "testpdfs")
    out.mkdir(parents=True, exist_ok=True)
    make_digital_pdf(out / "Klageschrift_digital.pdf")
    make_scanned_pdf(out / "Klageschrift_Scan.pdf")
    make_bank_letter_scan(out / "Bankbrief_Scan.pdf")
    make_signed_scan(out / "Brief_unterschrieben.pdf", 201, "mittel", True)
    for f in sorted(out.glob("*.pdf")):
        print(f)
