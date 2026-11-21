"""Measure the prototype: crypto cost, ledger throughput, validation time, storage footprint.

Usage:  python scripts/benchmark.py [--out docs/benchmark_results.json]
Numbers depend on the machine; they show the *shape* of the cost (linear, quadratic), not absolute speed.
"""
import argparse
import json
import os
import platform
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ledger import Blockchain, Wallet, canonical_json, sha256_hex          # noqa: E402
from app.ledger.chain import make_tx                                            # noqa: E402
from app.ledger.crypto import verify_signature                                  # noqa: E402


def make_chain(path=None):
    admin = Wallet.generate("admin"); vals = [Wallet.generate(f"v{i}") for i in range(3)]
    c = Blockchain(path, admin.address, [{"name": w.name, "address": w.address, "pubkey": w.public_hex} for w in vals],
                   clock=lambda: 1_700_000_000)
    c.register_validator_wallets(vals)
    return c, admin


def fill(c, admin, n_blocks):
    """One registration then n-1 service records, one transaction per block (worst case for block count)."""
    for w_i in range(1):
        pass
    reg = make_tx(admin, "register_vehicle", {"vin_hash": "v" * 64, "owner": admin.address, "odometer": 0, "data_hash": "0" * 64},
                  c.next_nonce(admin.address), 1_700_000_000)
    c.submit(reg); c.mine()
    for i in range(1, n_blocks):
        tx = make_tx(admin, "add_service", {"vin_hash": "v" * 64, "odometer": i * 100, "data_hash": sha256_hex(str(i))},
                     c.next_nonce(admin.address), 1_700_000_000 + i)
        c.submit(tx); c.mine()


def timed(fn, repeat=1):
    t = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - t) / repeat


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="docs/benchmark_results.json"); a = ap.parse_args()
    res = {"machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"}

    w = Wallet.generate("w"); msg = "x" * 200; sig = w.sign(msg)
    res["crypto_ms"] = {
        "sha256_1kb": round(timed(lambda: sha256_hex(b"x" * 1024), 2000) * 1000, 4),
        "ed25519_sign": round(timed(lambda: w.sign(msg), 500) * 1000, 4),
        "ed25519_verify": round(timed(lambda: verify_signature(w.public_hex, msg, sig), 500) * 1000, 4)}

    sizes, mem, disk = [50, 100, 200, 400, 800], [], []
    for n in sizes:
        c, admin = make_chain(None)
        t_fill = timed(lambda: fill(c, admin, n))
        t_val = timed(lambda: c.validate_chain(), 3)
        t_rebuild = timed(lambda: c.rebuild_state(), 3)
        mem.append({"blocks": n, "tx_per_s": round(n / t_fill, 1), "validate_ms": round(t_val * 1000, 1),
                    "rebuild_ms": round(t_rebuild * 1000, 1)})
    for n in [50, 100, 200, 400]:
        d = tempfile.mkdtemp(); c, admin = make_chain(os.path.join(d, "chain.json"))
        t_fill = timed(lambda: fill(c, admin, n))
        disk.append({"blocks": n, "tx_per_s": round(n / t_fill, 1), "file_kb": round(os.path.getsize(os.path.join(d, "chain.json")) / 1024, 1)})
    res["in_memory"], res["with_json_persistence"] = mem, disk

    # storage footprint: details kept off-chain vs the fixed bytes anchored on-chain
    payloads = [{"description": "Oil, filter, brake pads", "cost_inr": 6800, "workshop": "QuickFix Service Centre", "recorded_at": 1700000000},
                {"severity": "major", "description": "Rear-end collision, boot panel replaced", "claim_id": "CLM-2025-052",
                 "reported_by": "SafeDrive Insurance", "recorded_at": 1700000000},
                {"sale_price_inr": 540000, "buyer_name": "Rohan Mehta (owner)", "seller_name": "Asha Patil (owner)", "recorded_at": 1700000000}]
    avg = sum(len(canonical_json(p).encode()) for p in payloads) / len(payloads)
    res["storage_bytes"] = {"avg_offchain_json": round(avg), "onchain_record_solidity": 32 + 20 + 8 + 4 + 1,
                            "onchain_hash_only": 32, "saving_pct": round(100 * (1 - 32 / avg))}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
