"""Unit tests: smart-contract rules, blockchain integrity, off-chain verification.

Run:  python -m unittest discover -s tests -v
"""
import copy
import itertools
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ledger import ContractError, Wallet            # noqa: E402
from app.ledger.chain import ChainError, make_tx        # noqa: E402
from app.service import LedgerService                   # noqa: E402

VIN = "MA3ERLF1S00123456"


def fresh_service():
    tmp = tempfile.mkdtemp()
    counter = itertools.count(1_700_000_000)
    return LedgerService(tmp, clock=lambda: next(counter))


class ContractRules(unittest.TestCase):
    def setUp(self):
        self.s = fresh_service()
        self.s.register("registrar", VIN, "asha", 10, "Tata", "Nexon", 2022, "MH01AB1234")

    def test_register_and_duplicate_vin_rejected(self):
        self.assertEqual(self.s.vehicle_report(VIN)["owner_name"], "Asha Patil (owner)")
        with self.assertRaisesRegex(ContractError, "duplicate VIN"):
            self.s.register("registrar2", VIN, "rohan", 0, "Tata", "Nexon", 2022, "X")

    def test_unauthorized_roles_rejected(self):
        with self.assertRaisesRegex(ContractError, "lacks role"):
            self.s.service("insurer", VIN, 500, "Oil change", 2000)       # insurer cannot service
        with self.assertRaisesRegex(ContractError, "lacks role"):
            self.s.accident("service", VIN, 500, "minor", "bumper", "CL1")  # workshop cannot report
        with self.assertRaisesRegex(ContractError, "lacks role"):
            self.s.register("asha", "MA3ERLF1S00999999", "asha", 0, "X", "Y", 2020, "Z")

    def test_odometer_rollback_rejected(self):
        self.s.service("service", VIN, 12000, "First service", 3500)
        with self.assertRaisesRegex(ContractError, "rollback"):
            self.s.service("service", VIN, 8000, "Fake low reading", 100)
        self.assertEqual(self.s.vehicle_report(VIN)["odometer"], 12000)

    def test_only_owner_can_transfer(self):
        with self.assertRaisesRegex(ContractError, "only the current owner"):
            self.s.transfer("rohan", VIN, "sana", 100, 500000)
        self.s.transfer("asha", VIN, "rohan", 15000, 650000)
        r = self.s.vehicle_report(VIN)
        self.assertEqual((r["owner_name"], r["owners"]), ("Rohan Mehta (owner)", 2))
        with self.assertRaisesRegex(ContractError, "only the current owner"):
            self.s.transfer("asha", VIN, "sana", 16000, 1)   # previous owner has lost rights

    def test_stolen_vehicle_cannot_be_sold(self):
        self.s.set_stolen("registrar2", VIN, True, "FIR-2026-0042")
        with self.assertRaisesRegex(ContractError, "stolen"):
            self.s.transfer("asha", VIN, "rohan", 100, 1)
        self.s.set_stolen("registrar2", VIN, False, "Recovered")
        self.s.transfer("asha", VIN, "rohan", 100, 1)

    def test_invalid_vin_format(self):
        with self.assertRaises(ValueError):
            self.s.vin_hash("SHORT")
        with self.assertRaises(ValueError):
            self.s.vin_hash("MA3ERLF1S0012345O")   # letter O not allowed

    def test_failed_transaction_leaves_no_trace(self):
        height = self.s.chain.height
        with self.assertRaises(ContractError):
            self.s.service("service", VIN, 1, "x", 1)     # 1 < 10 km registered
        self.assertEqual(self.s.chain.height, height)
        self.assertEqual(len(self.s.vehicle_report(VIN)["history"]), 1)


class ChainIntegrity(unittest.TestCase):
    def setUp(self):
        self.s = fresh_service()
        self.s.register("registrar", VIN, "asha", 10, "Tata", "Nexon", 2022, "MH01AB1234")
        self.s.service("service", VIN, 9000, "Service", 3000)
        self.s.accident("insurer", VIN, 9500, "minor", "Rear bumper", "CL-77")

    def test_valid_chain(self):
        ok, errs = self.s.chain.validate_chain()
        self.assertTrue(ok, errs)

    def test_tampering_with_transaction_data_detected(self):
        c = self.s.chain
        c.blocks[-1]["transactions"][0]["args"]["odometer"] = 1
        ok, errs = c.validate_chain()
        self.assertFalse(ok)
        self.assertTrue(any("signature" in e or "Merkle" in e for e in errs))

    def test_tampering_with_block_hash_chain_detected(self):
        c = self.s.chain
        c.blocks[2]["timestamp"] += 1
        ok, errs = c.validate_chain()
        self.assertFalse(ok)
        self.assertTrue(any("hash mismatch" in e for e in errs))

    def test_deleting_a_block_detected(self):
        c = self.s.chain
        del c.blocks[3]
        ok, errs = c.validate_chain()
        self.assertFalse(ok)

    def test_forged_signature_rejected(self):
        mallory = Wallet.generate("mallory")
        tx = make_tx(mallory, "set_stolen", {"vin_hash": self.s.vin_hash(VIN), "stolen": True,
                                             "data_hash": "00"}, 0)
        tx["sender"] = self.s.wallets["registrar"].address     # impersonation attempt
        with self.assertRaisesRegex(ChainError, "does not match public key"):
            self.s.chain.submit(tx)

    def test_replay_attack_rejected(self):
        tx = copy.deepcopy(self.s.chain.blocks[1]["transactions"][0])
        with self.assertRaisesRegex(ChainError, "nonce"):
            self.s.chain.submit(tx)

    def test_offchain_tampering_detected(self):
        self.s.tamper_offchain(VIN, 1)
        r = self.s.vehicle_report(VIN)
        self.assertEqual(r["history"][1]["status"], "tampered")
        self.assertTrue(any("do not match" in f[1] for f in r["flags"]))
        self.assertEqual(r["history"][0]["status"], "verified")

    def test_state_survives_restart(self):
        again = LedgerService(self.s.data_dir)
        self.assertEqual(again.vehicle_report(VIN)["odometer"], 9500)
        self.assertTrue(again.chain.validate_chain()[0])

    def test_round_robin_validators(self):
        vals = [b["validator"] for b in self.s.chain.blocks[1:7]]
        self.assertTrue(len(set(vals)) == 3)


if __name__ == "__main__":
    unittest.main()
