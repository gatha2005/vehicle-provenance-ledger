"""Tests for liens, trust score, fraud log, document verification, tamper lab, QR and new pages."""
import calendar
import itertools
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import analytics, qr                      # noqa: E402
from app.ledger import ContractError               # noqa: E402
from app.main import create_app                    # noqa: E402
from app.service import LedgerService              # noqa: E402

VIN = "MA3ERLF1S00123456"
DAY = 86400
T0 = calendar.timegm((2024, 1, 1, 0, 0, 0))


def svc_with_days():
    """Service whose clock advances 10 days per call, so timestamps look realistic."""
    c = itertools.count(T0, 10 * DAY // 6)
    return LedgerService(tempfile.mkdtemp(), clock=lambda: next(c))


class Liens(unittest.TestCase):
    def setUp(self):
        self.s = svc_with_days()
        self.s.register("registrar", VIN, "asha", 10, "Tata", "Nexon", 2022, "MH01AB1234")

    def test_lien_blocks_transfer_until_released(self):
        self.s.place_lien("lender", VIN, "LN-1", 500000)
        with self.assertRaisesRegex(ContractError, "Active lien"):
            self.s.transfer("asha", VIN, "rohan", 100, 1)
        self.s.release_lien("lender", VIN, "NOC-1")
        self.s.transfer("asha", VIN, "rohan", 100, 1)

    def test_lien_roles_and_single_lien(self):
        with self.assertRaisesRegex(ContractError, "lacks role LENDER"):
            self.s.place_lien("service", VIN, "X", 1)
        self.s.place_lien("lender", VIN, "LN-1", 1)
        with self.assertRaisesRegex(ContractError, "already active"):
            self.s.place_lien("lender", VIN, "LN-2", 1)
        with self.assertRaisesRegex(ContractError, "only the lender"):
            self.s.release_lien("asha", VIN, "NOC")
        self.s.release_lien("lender", VIN, "NOC")
        with self.assertRaisesRegex(ContractError, "No active lien"):
            self.s.release_lien("lender", VIN, "NOC-2")

    def test_lien_appears_in_report_and_score(self):
        self.s.place_lien("lender", VIN, "LN-1", 1)
        r = self.s.vehicle_report(VIN)
        self.assertEqual(r["lien_holder_name"], "SureCredit Auto Finance")
        self.assertTrue(any(f["label"] == "Active lien" for f in r["trust"]["factors"]))


class TrustScore(unittest.TestCase):
    def setUp(self):
        self.s = svc_with_days()
        self.s.register("registrar", VIN, "asha", 10, "Tata", "Nexon", 2022, "MH01AB1234")
        self.s.service("service", VIN, 9000, "Service", 100)

    def test_clean_vehicle_scores_high(self):
        t = self.s.vehicle_report(VIN)["trust"]
        self.assertGreaterEqual(t["score"], 85)
        self.assertEqual(t["grade"], "A")

    def test_accident_and_stolen_reduce_score(self):
        before = self.s.vehicle_report(VIN)["trust"]["score"]
        self.s.accident("insurer", VIN, 9500, "major", "Collision", "C1")
        mid = self.s.vehicle_report(VIN)["trust"]["score"]
        self.s.set_stolen("registrar", VIN, True, "FIR")
        after = self.s.vehicle_report(VIN)["trust"]["score"]
        self.assertEqual(before - mid, 15)
        self.assertLess(after, mid)
        self.assertEqual(self.s.vehicle_report(VIN)["trust"]["grade"], "E")

    def test_offchain_tampering_crushes_score(self):
        self.s.tamper_offchain(VIN, 1)
        self.assertLessEqual(self.s.vehicle_report(VIN)["trust"]["score"], 40)

    def test_service_gap_and_usage_anomalies(self):
        self.s.service("service", VIN, 30000, "Big gap", 100)      # 21,000 km in ~10 days
        kinds = {a["kind"] for a in self.s.vehicle_report(VIN)["trust"]["anomalies"]}
        self.assertEqual(kinds, {"gap", "usage"})

    def test_grade_boundaries(self):
        self.assertEqual([analytics.grade_for(x)[0] for x in (100, 85, 84, 70, 55, 40, 39, 0)],
                         list("AABBCDEE"))


class FraudLogAndVerify(unittest.TestCase):
    def setUp(self):
        self.s = svc_with_days()
        self.s.register("registrar", VIN, "asha", 10, "Tata", "Nexon", 2022, "MH01AB1234")

    def test_rejections_are_logged_with_category(self):
        for _ in range(2):
            with self.assertRaises(ContractError):
                self.s.service("insurer", VIN, 50, "x", 1)
        with self.assertRaises(ContractError):
            self.s.service("service", VIN, 1, "x", 1)
        summary = self.s.rejection_summary()
        self.assertEqual(summary["Wrong role"], 2)
        self.assertEqual(summary["Odometer rollback"], 1)
        self.assertEqual(self.s.rejection_log()[0]["category"], "Odometer rollback")

    def test_accepted_transactions_are_not_logged(self):
        self.assertEqual(self.s.rejection_summary(), {})

    def test_find_by_data_hash_and_tx(self):
        rec = self.s.vehicle_report(VIN)["history"][0]
        hits = self.s.find_by_data_hash(rec["data_hash"])
        self.assertEqual(hits[0]["vin"], VIN)
        self.assertEqual(hits[0]["status"], "verified")
        self.assertEqual(self.s.find_by_data_hash("0" * 64), [])
        tx = self.s.chain.blocks[-1]["transactions"][0]["tx_hash"]
        self.assertEqual(self.s.find_tx(tx)["fn"], "register_vehicle")
        self.assertIsNone(self.s.find_tx("f" * 64))


class TamperLab(unittest.TestCase):
    def test_every_attack_is_detected_and_real_chain_untouched(self):
        s = svc_with_days()
        s.register("registrar", VIN, "asha", 10, "Tata", "Nexon", 2022, "MH01AB1234")
        s.service("service", VIN, 9000, "Service", 100)
        s.accident("insurer", VIN, 9500, "minor", "x", "C1")
        s.transfer("asha", VIN, "rohan", 9600, 1)
        for kind in s.ATTACKS:
            res = s.simulate_attack(kind)
            self.assertTrue(res["detected"], kind)
            self.assertTrue(s.chain.validate_chain()[0], f"{kind} modified the real chain")


class QrCode(unittest.TestCase):
    def test_qr_svg_and_size_scaling(self):
        small, big = qr.encode("http://a.io/x"), qr.encode("http://127.0.0.1:5000/vehicle/" + VIN + "?" + "x" * 90)
        self.assertEqual(len(small) % 4, 1)
        self.assertGreater(len(big), len(small))
        self.assertTrue(qr.svg("hello").startswith("<svg"))
        with self.assertRaises(ValueError):
            qr.encode("x" * 500)


class NewPages(unittest.TestCase):
    def setUp(self):
        self.app = create_app(tempfile.mkdtemp())
        self.c = self.app.test_client()
        self.c.post("/record", data=dict(kind="register", actor="registrar", vin=VIN, make="Tata", model="Nexon",
                                          year="2022", reg_no="MH01AB1234", owner="asha", odometer="10"))

    def test_all_pages_render(self):
        for p in ("/", "/verify", "/compare", "/explorer", "/security", "/tamper-lab", f"/vehicle/{VIN}",
                  "/api/stats", f"/compare?a={VIN}&b={VIN}"):
            self.assertEqual(self.c.get(p).status_code, 200, p)

    def test_vehicle_page_has_score_qr_and_chart(self):
        h = self.c.get(f"/vehicle/{VIN}").data.decode()
        self.assertIn("Trust score", h)
        self.assertIn('class="qr"', h)
        self.assertIn("Why this score", h)

    def test_lien_flow_through_ui(self):
        self.c.post("/record", data=dict(kind="lien", actor="lender", vin=VIN, loan_ref="L1", amount="1000"))
        r = self.c.post("/record", data=dict(kind="transfer", actor="asha", vin=VIN, new_owner="rohan",
                                              odometer="20", price="1"), follow_redirects=True)
        self.assertIn(b"Active lien", r.data)
        self.assertIn(b"Active lien", self.c.get("/security").data)

    def test_verify_page_finds_hash_and_json(self):
        rec = self.app.config["svc"].vehicle_report(VIN)["history"][0]
        r = self.c.get(f"/verify?hash={rec['data_hash']}")
        self.assertIn(b"Match found", r.data)
        r = self.c.post("/verify", data=dict(text="not on the ledger"))
        self.assertIn(b"No match", r.data)

    def test_tamper_lab_page_reports_detection(self):
        self.c.post("/record", data=dict(kind="service", actor="service", vin=VIN, odometer="500",
                                          description="s", cost="1"))
        self.assertIn(b"Attack detected", self.c.get("/tamper-lab?attack=rehash").data)

    def test_actor_session_preselects_role(self):
        self.c.post("/set-actor", data=dict(actor="insurer", next="/record"))
        self.assertIn(b'value="insurer" selected', self.c.get("/record").data.replace(b'\n', b' '))


if __name__ == "__main__":
    unittest.main()
