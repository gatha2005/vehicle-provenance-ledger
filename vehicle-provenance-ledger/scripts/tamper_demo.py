"""Demonstrate tamper evidence: edit stored data, then watch verification fail.

Usage:  python scripts/tamper_demo.py [--data-dir data]
Works on a COPY of the data directory so your real ledger stays intact.
"""
import argparse
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.service import LedgerService   # noqa: E402

VIN = "MA3ERLF1S00123456"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    tmp = os.path.join(tempfile.mkdtemp(), "data")
    shutil.copytree(a.data_dir, tmp)
    s = LedgerService(tmp)
    print("Before tampering:", s.chain.validate_chain()[0], "| off-chain:",
          {h["status"] for h in s.vehicle_report(VIN)["history"]})

    print("\nAttack 1: edit an off-chain service record in the database")
    s.tamper_offchain(VIN, 2)
    print("  statuses:", [h["status"] for h in s.vehicle_report(VIN)["history"]])

    print("\nAttack 2: lower the odometer inside a sealed block")
    target = next(b for b in s.chain.blocks
                  if b["transactions"] and b["transactions"][0]["fn"] == "add_service")
    target["transactions"][0]["args"]["odometer"] = 1000
    ok, errs = s.chain.validate_chain()
    print(f"  chain valid: {ok} (block {target['index']} was edited)")
    for e in errs[:3]:
        print("   -", e)

    print("\nAttack 3: delete a block")
    del s.chain.blocks[2]
    ok, errs = s.chain.validate_chain()
    print("  chain valid:", ok, f"({len(errs)} errors detected)")


if __name__ == "__main__":
    main()
