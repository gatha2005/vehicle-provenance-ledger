"""Integration tests for the Flask web interface."""
import itertools
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import create_app   # noqa: E402

VIN = "MA3ERLF1S00123456"


class WebApp(unittest.TestCase):
    def setUp(self):
        counter = itertools.count(1_700_000_000)
        self.app = create_app(tempfile.mkdtemp(), clock=lambda: next(counter))
        self.c = self.app.test_client()

    def post(self, **data):
        return self.c.post("/record", data=data, follow_redirects=True)

    def register(self):
        return self.post(kind="register", actor="registrar", vin=VIN, make="Tata", model="Nexon",
                         year="2022", reg_no="MH01AB1234", owner="asha", odometer="10")

    def test_pages_load_empty(self):
        for path in ("/", "/record", "/explorer", "/api/chain/validate"):
            self.assertEqual(self.c.get(path).status_code, 200, path)

    def test_register_then_view_history(self):
        r = self.register()
        self.assertIn(b"Confirmed in block", r.data)
        page = self.c.get(f"/vehicle/{VIN}")
        self.assertIn(b"Nexon", page.data)
        self.assertIn(b"Verified", page.data)

    def test_rejected_transaction_shows_reason(self):
        self.register()
        r = self.post(kind="service", actor="insurer", vin=VIN, odometer="500",
                      description="x", cost="1")
        self.assertIn(b"Rejected by smart contract", r.data)
        self.assertIn(b"lacks role", r.data)

    def test_odometer_rollback_blocked_in_ui(self):
        self.register()
        self.post(kind="service", actor="service", vin=VIN, odometer="9000",
                  description="svc", cost="1")
        r = self.post(kind="service", actor="service", vin=VIN, odometer="100",
                      description="fake", cost="1")
        self.assertIn(b"rollback", r.data)

    def test_api_and_unknown_vin(self):
        self.register()
        j = self.c.get(f"/api/vehicle/{VIN}").get_json()
        self.assertEqual(j["owners"], 1)
        self.assertEqual(self.c.get("/api/vehicle/MA3ERLF1S00999999").status_code, 404)
        self.assertEqual(self.c.get("/api/vehicle/BAD").status_code, 400)


if __name__ == "__main__":
    unittest.main()
