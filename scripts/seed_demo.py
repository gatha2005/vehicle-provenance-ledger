"""Load synthetic demo data (8 vehicles, ~3 years of dated history) and show blocked fraud.

Usage:  python scripts/seed_demo.py [--reset] [--data-dir data]
"""
import argparse
import calendar
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ledger import ContractError            # noqa: E402
from app.ledger.chain import ChainError         # noqa: E402
from app.service import LedgerService           # noqa: E402

V1, V2, V3, V4 = "MA3ERLF1S00123456", "MBHCZC63S0G200789", "MALA851CLGM300321", "MAT6240A1NP400112"
V5, V6, V7, V8 = "MA3FJEB1S00500233", "MALC381CLJM600445", "MBJAR3FS7LW700667", "MA1TA2YT5N2800778"
NOW = [calendar.timegm((2023, 1, 15, 9, 0, 0))]


def on(date: str):
    """Move the synthetic clock to a date (YYYY-MM-DD) so the history has realistic timestamps."""
    y, m, d = map(int, date.split("-"))
    NOW[0] = calendar.timegm((y, m, d, 10, 30, 0))


def attempt(label, fn):
    try:
        fn()
        print(f"  [ACCEPTED] {label}")
    except (ContractError, ChainError, ValueError) as e:
        print(f"  [REJECTED] {label}\n             -> {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete existing data first")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    if a.reset and os.path.isdir(a.data_dir):
        shutil.rmtree(a.data_dir)
    s = LedgerService(a.data_dir, clock=lambda: NOW[0])
    if s.chain.state.vehicles:
        print("Ledger already has data. Use --reset to start fresh.")
        return

    print("Valid transactions (replayed in date order, like a real ledger)")
    events = []
    ev = lambda date, fn, *args: events.append((date, len(events), fn, args))
    ev("2023-03-02", s.register, "registrar", V1, "asha", 12, "Tata", "Nexon", 2021, "MH01AB1234")
    ev("2023-06-10", s.service, "service", V1, 10250, "First free service", 0)
    ev("2023-11-20", s.service, "service", V1, 21400, "Oil, filter, brake pads", 6800)
    ev("2024-02-05", s.accident, "insurer", V1, 24100, "minor", "Rear bumper and tail light", "CLM-2024-118")
    ev("2024-06-18", s.transfer, "asha", V1, "rohan", 30500, 540000)
    ev("2025-01-22", s.service, "service", V1, 36200, "Clutch plate replacement", 9400)

    ev("2023-04-12", s.register, "registrar2", V2, "sana", 8, "Hyundai", "Creta", 2023, "MH02CD5678")
    ev("2023-09-15", s.service, "service", V2, 9800, "First free service", 0)
    ev("2024-03-20", s.service, "service", V2, 19500, "Oil, filter, wheel alignment", 7200)
    ev("2024-04-02", s.place_lien, "lender", V2, "LN-2024-5521", 850000)
    ev("2025-10-10", s.service, "service", V2, 29800, "Brake fluid and tyre rotation", 5100)

    ev("2023-05-05", s.register, "registrar", V3, "rohan", 5, "Maruti Suzuki", "Baleno", 2020, "MH03EF9012")
    ev("2025-11-03", s.accident, "insurer", V3, 41800, "major", "Front-end collision, airbags deployed", "CLM-2025-884")
    ev("2026-08-14", s.set_stolen, "registrar2", V3, True, "FIR-2026-0042")

    ev("2023-06-01", s.register, "registrar", V4, "sana", 20, "Mahindra", "XUV700", 2022, "MH04GH3456")
    for d, odo, w, c in (("2023-09-04", 8900, "First free service", 0), ("2024-03-11", 18200, "Oil and filters", 6400),
                         ("2024-09-09", 27600, "Brake pads, coolant", 9800), ("2025-03-10", 36500, "Oil, filters, wipers", 6900),
                         ("2025-09-15", 45100, "Timing belt inspection, oil", 8200), ("2026-03-16", 54000, "Full service", 11200)):
        ev(d, s.service, "service", V4, odo, w, c)

    ev("2023-07-10", s.register, "registrar", V5, "meera", 31000, "Maruti Suzuki", "Swift", 2019, "MH05JK7890")
    ev("2023-12-01", s.transfer, "meera", V5, "dev", 36000, 330000)
    ev("2024-01-15", s.accident, "insurer", V5, 38000, "minor", "Side mirror and door scratch", "CLM-2024-031")
    ev("2024-05-05", s.service, "service", V5, 47500, "Oil change and brake service", 5400)
    ev("2024-08-09", s.transfer, "dev", V5, "irfan", 52000, 290000)
    ev("2025-02-11", s.accident, "insurer", V5, 58000, "major", "Rear-end collision, boot panel replaced", "CLM-2025-052")
    ev("2025-06-30", s.transfer, "irfan", V5, "asha", 64000, 240000)
    ev("2025-09-12", s.service, "service", V5, 66000, "Suspension and brakes", 12800)

    ev("2023-08-01", s.register, "registrar2", V6, "irfan", 15, "Hyundai", "i20", 2021, "MH06LM1122")
    ev("2023-12-12", s.service, "service", V6, 9500, "First free service", 0)
    ev("2025-04-20", s.service, "service", V6, 38000, "Major service after long gap", 14500)
    ev("2025-09-18", s.accident, "insurer", V6, 41000, "major", "Side impact, door and pillar repair", "CLM-2025-640")

    ev("2023-05-20", s.register, "registrar", V7, "asha", 40, "Toyota", "Innova", 2020, "MH07NP3344")
    ev("2023-10-01", s.place_lien, "lender", V7, "LN-2023-1042", 600000)
    ev("2023-11-15", s.service, "service", V7, 9800, "First service", 0)
    ev("2024-06-03", s.service, "service", V7, 21000, "Oil, filters, battery check", 5800)
    ev("2025-01-10", s.release_lien, "lender", V7, "NOC-2025-0110")
    ev("2025-02-20", s.service, "service", V7, 32500, "Oil, filters, tyres", 14200)

    ev("2023-09-01", s.register, "registrar2", V8, "dev", 50, "Mahindra", "Thar", 2022, "MH08QR5566")
    ev("2023-09-12", s.service, "service", V8, 25000, "Service (reading unusually high for 11 days)", 4800)
    ev("2024-06-10", s.service, "service", V8, 33000, "Oil change", 3900)
    ev("2025-03-03", s.accident, "insurer", V8, 41000, "total loss", "Rollover, insurer declared total loss", "CLM-2025-210")

    for date, _, fn, args in sorted(events):
        on(date)
        fn(*args)
    on("2026-10-01")
    print(f"  {s.chain.height} blocks sealed.\n")

    print("Invalid transactions (the smart contract must reject every one)")
    attempt("Workshop writes a LOWER odometer on the Nexon (rollback fraud)",
            lambda: s.service("service", V1, 20000, "Fake reading", 100))
    on("2026-10-02")
    attempt("Previous owner tries to sell the Nexon again",
            lambda: s.transfer("asha", V1, "sana", 37000, 1))
    on("2026-10-03")
    attempt("Sell the stolen Baleno", lambda: s.transfer("rohan", V3, "sana", 42000, 1))
    on("2026-10-04")
    attempt("Insurer tries to log a service", lambda: s.service("insurer", V2, 30000, "Not allowed", 0))
    on("2026-10-05")
    attempt("Register the same VIN twice",
            lambda: s.register("registrar", V1, "sana", 0, "Tata", "Nexon", 2021, "DUP"))
    on("2026-10-06")
    attempt("Sell the Creta while the loan is outstanding (lien)", lambda: s.transfer("sana", V2, "meera", 30000, 1))
    on("2026-10-07")
    attempt("Workshop lowers the XUV700 odometer", lambda: s.service("service", V4, 40000, "Fake", 1))
    on("2026-10-08")
    attempt("Workshop tries to report an accident (wrong role)",
            lambda: s.accident("service", V5, 67000, "minor", "Not allowed", "X"))
    on("2026-10-09")
    attempt("Owner tries to place a lien on their own car (wrong role)",
            lambda: s.place_lien("asha", V1, "FAKE-LOAN", 1))
    attempt("Malformed VIN (contains letter O)", lambda: s.vin_hash("MA3ERLF1S0012345O"))

    ok, _ = s.chain.validate_chain()
    print(f"\nChain valid: {ok} | height: {s.chain.height} | vehicles: {len(s.chain.state.vehicles)}")
    print(f"Try the app:  python run.py   ->  http://127.0.0.1:5000/vehicle/{V1}")


if __name__ == "__main__":
    main()
