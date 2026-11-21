"""Application service: ties together wallets, the blockchain and the off-chain database.

On-chain  : SHA-256 hash of the VIN, addresses, odometer, event type, SHA-256 of the details.
Off-chain : full human-readable details (SQLite). Integrity is proven by re-hashing.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time

from .ledger import Blockchain, ContractError, Role, Wallet, canonical_json, sha256_hex
from .ledger.chain import ChainError, make_tx
from . import analytics, charts

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # ISO 3779: no I, O or Q

# (key, display name, role) - all synthetic demo identities
ACTORS = [
    ("admin", "RTO Admin", None),
    ("registrar", "Tata Motors (Manufacturer)", Role.REGISTRAR),
    ("registrar2", "RTO Registration Officer", Role.REGISTRAR),
    ("service", "QuickFix Service Centre", Role.SERVICE_CENTER),
    ("insurer", "SafeDrive Insurance", Role.INSURER),
    ("lender", "SureCredit Auto Finance", Role.LENDER),
    ("asha", "Asha Patil (owner)", None),
    ("rohan", "Rohan Mehta (owner)", None),
    ("sana", "Sana Khan (owner)", None),
    ("meera", "Meera Iyer (owner)", None),
    ("dev", "Dev Sharma (owner)", None),
    ("irfan", "Irfan Qureshi (owner)", None),
]
OWNER_KEYS = ("asha", "rohan", "sana", "meera", "dev", "irfan")
VALIDATORS = [("val_rto", "RTO Node"), ("val_dealers", "Dealers Association Node"),
              ("val_insurers", "Insurers Council Node")]


class LedgerService:
    def __init__(self, data_dir: str = "data", clock=lambda: int(time.time())):
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir, self.clock = data_dir, clock
        self.wallets = self._load_wallets()
        self.validator_wallets = [self.wallets[k] for k, _ in VALIDATORS]
        validators = [{"name": n, "address": self.wallets[k].address,
                       "pubkey": self.wallets[k].public_hex} for k, n in VALIDATORS]
        first_run = not os.path.exists(os.path.join(data_dir, "chain.json"))
        self.chain = Blockchain(os.path.join(data_dir, "chain.json"),
                                self.wallets["admin"].address, validators, clock)
        self.chain.register_validator_wallets(self.validator_wallets)
        self.names = {w.address: w.name for w in self.wallets.values()}
        self.db = sqlite3.connect(os.path.join(data_dir, "offchain.db"), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS records(
            data_hash TEXT PRIMARY KEY, vin_hash TEXT, kind TEXT, payload TEXT, created_at INT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS rejections(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts INT, actor TEXT, fn TEXT, vin_hash TEXT,
            reason TEXT, category TEXT)""")
        self.db.commit()
        if first_run:
            self._bootstrap_roles()

    # ------------------------------------------------------------------ wallets
    def _load_wallets(self) -> dict[str, Wallet]:
        path = os.path.join(self.data_dir, "wallets.json")  # DEMO ONLY - git-ignored
        if os.path.exists(path):
            with open(path) as f:
                raw = json.load(f)
            return {k: Wallet.from_private(v["name"], v["private"]) for k, v in raw.items()}
        wallets = {k: Wallet.generate(n) for k, n, _ in ACTORS}
        wallets.update({k: Wallet.generate(n) for k, n in VALIDATORS})
        with open(path, "w") as f:
            json.dump({k: {"name": w.name, "private": w.export_private()}
                       for k, w in wallets.items()}, f, indent=1)
        return wallets

    def _bootstrap_roles(self):
        for key, _, role in ACTORS:
            if role:
                self.send("admin", "grant_role",
                          {"account": self.wallets[key].address, "role": role.value})

    def name_of(self, address: str) -> str:
        return self.names.get(address, address[:10] + "…")

    # ------------------------------------------------------------- transactions
    def send(self, actor_key: str, fn: str, args: dict, payload: dict | None = None,
             kind: str | None = None) -> dict:
        """Sign a transaction as `actor_key`, run contract rules, seal a block, save details."""
        wallet = self.wallets[actor_key]
        args = dict(args)
        if payload is not None:
            payload = {**payload, "recorded_at": self.clock()}
            args["data_hash"] = sha256_hex(canonical_json(payload))
        elif fn in ("register_vehicle", "add_service", "report_accident", "transfer_ownership",
                    "set_stolen", "place_lien", "release_lien"):
            raise ValueError("payload required")
        tx = make_tx(wallet, fn, args, self.chain.next_nonce(wallet.address), self.clock())
        try:
            self.chain.submit(tx)       # raises ContractError / ChainError when invalid
        except (ContractError, ChainError) as exc:
            self._log_rejection(wallet.name, fn, args.get("vin_hash"), str(exc))
            raise
        block = self.chain.mine()
        if payload is not None:
            self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)",
                            (args["data_hash"], args.get("vin_hash"), kind or fn,
                             canonical_json(payload), self.clock()))
            self.db.commit()
        return {"tx_hash": tx["tx_hash"], "block": block["index"], "block_hash": block["hash"]}

    def _log_rejection(self, actor, fn, vin_hash, reason):
        self.db.execute("INSERT INTO rejections(ts,actor,fn,vin_hash,reason,category) VALUES (?,?,?,?,?,?)",
                        (self.clock(), actor, fn, vin_hash, reason, analytics.category_of(reason)))
        self.db.commit()

    def rejection_log(self, limit=50):
        rows = self.db.execute("SELECT ts,actor,fn,vin_hash,reason,category FROM rejections ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(ts=r[0], actor=r[1], fn=r[2], vin_hash=r[3], reason=r[4], category=r[5]) for r in rows]

    def rejection_summary(self) -> dict:
        return dict(self.db.execute("SELECT category, COUNT(*) FROM rejections GROUP BY category "
                                    "ORDER BY COUNT(*) DESC").fetchall())

    @staticmethod
    def vin_hash(vin: str) -> str:
        vin = vin.strip().upper()
        if not VIN_RE.match(vin):
            raise ValueError("VIN must be 17 characters (A-Z, 0-9; letters I, O, Q not allowed)")
        return sha256_hex(vin)

    def register(self, actor, vin, owner_key, odometer, make, model, year, reg_no):
        vin = vin.strip().upper()
        payload = {"vin": vin, "make": make, "model": model, "year": int(year),
                   "registration_no": reg_no, "owner_name": self.wallets[owner_key].name}
        return self.send(actor, "register_vehicle",
                         {"vin_hash": self.vin_hash(vin), "owner": self.wallets[owner_key].address,
                          "odometer": int(odometer)}, payload, "REGISTRATION")

    def service(self, actor, vin, odometer, description, cost):
        return self.send(actor, "add_service",
                         {"vin_hash": self.vin_hash(vin), "odometer": int(odometer)},
                         {"description": description, "cost_inr": int(cost),
                          "workshop": self.wallets[actor].name}, "SERVICE")

    def accident(self, actor, vin, odometer, severity, description, claim_id):
        return self.send(actor, "report_accident",
                         {"vin_hash": self.vin_hash(vin), "odometer": int(odometer)},
                         {"severity": severity, "description": description,
                          "claim_id": claim_id, "reported_by": self.wallets[actor].name},
                         "ACCIDENT")

    def transfer(self, actor, vin, new_owner_key, odometer, price):
        return self.send(actor, "transfer_ownership",
                         {"vin_hash": self.vin_hash(vin), "new_owner": self.wallets[new_owner_key].address,
                          "odometer": int(odometer)},
                         {"sale_price_inr": int(price), "buyer_name": self.wallets[new_owner_key].name,
                          "seller_name": self.wallets[actor].name}, "OWNERSHIP_TRANSFER")

    def set_stolen(self, actor, vin, stolen, reference):
        return self.send(actor, "set_stolen", {"vin_hash": self.vin_hash(vin), "stolen": stolen},
                         {"stolen": stolen, "fir_or_reference": reference}, "STATUS_CHANGE")

    def place_lien(self, actor, vin, loan_ref, amount):
        return self.send(actor, "place_lien", {"vin_hash": self.vin_hash(vin)},
                         {"loan_reference": loan_ref, "loan_amount_inr": int(amount),
                          "lender": self.wallets[actor].name}, "LIEN_PLACED")

    def release_lien(self, actor, vin, reference):
        return self.send(actor, "release_lien", {"vin_hash": self.vin_hash(vin)},
                         {"closure_reference": reference, "lender": self.wallets[actor].name}, "LIEN_RELEASED")

    # ------------------------------------------------------------------ queries
    def _payload(self, data_hash: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM records WHERE data_hash=?", (data_hash,)).fetchone()
        return json.loads(row[0]) if row else None

    def stored_payload_text(self, data_hash: str) -> str | None:
        row = self.db.execute("SELECT payload FROM records WHERE data_hash=?", (data_hash,)).fetchone()
        return row[0] if row else None

    def verify_record(self, rec: dict) -> str:
        """'verified' | 'tampered' | 'missing' - compare off-chain data with on-chain hash."""
        text = self.stored_payload_text(rec["data_hash"])
        if text is None:
            return "missing"
        return "verified" if sha256_hex(canonical_json(json.loads(text))) == rec["data_hash"] \
            else "tampered"

    def _chain_ok(self) -> bool:
        return self.chain.validate_chain()[0]

    def vehicle_report(self, vin: str, chain_ok: bool | None = None) -> dict | None:
        vh = self.vin_hash(vin)
        v = self.chain.state.vehicles.get(vh)
        if not v:
            return None
        history = []
        for rec in self.chain.state.history[vh]:
            history.append({**rec, "status": self.verify_record(rec),
                            "details": self._payload(rec["data_hash"]),
                            "actor_name": self.name_of(rec["actor"]),
                            "to_name": self.name_of(rec["to"]) if "to" in rec else None})
        count = lambda t: sum(1 for h in history if h["type"] == t)
        owners = 1 + count("OWNERSHIP_TRANSFER")
        chain_ok = self._chain_ok() if chain_ok is None else chain_ok
        flags = []
        if v["stolen"]:
            flags.append(("danger", "Flagged as stolen"))
        if count("ACCIDENT"):
            flags.append(("warn", f"{count('ACCIDENT')} accident record(s)"))
        if any(h["status"] != "verified" for h in history):
            flags.append(("danger", "Off-chain details do not match the on-chain hash"))
        if not chain_ok:
            flags.append(("danger", "Blockchain integrity check failed"))
        if owners >= 4:
            flags.append(("warn", f"{owners} previous owners"))
        lien = v["lien"]
        if lien:
            flags.append(("warn", f"Active lien: {self.name_of(lien)}"))
        report = {"vin": vin.strip().upper(), "vin_hash": vh, "owner": v["owner"],
                  "owner_name": self.name_of(v["owner"]), "odometer": v["odometer"],
                  "stolen": v["stolen"], "owners": owners, "accidents": count("ACCIDENT"),
                  "services": count("SERVICE"), "history": history, "flags": flags,
                  "chain_ok": chain_ok, "lien_holder": lien, "lien_holder_name": self.name_of(lien) if lien else None}
        t = analytics.trust_score(report)
        for a in t["anomalies"]:
            flags.append(("warn", a["text"]))
        report["trust"] = t
        report["anomaly_idx"] = sorted({a["index"] for a in t["anomalies"]})
        return report

    def all_vehicles(self) -> list[dict]:
        ok, out = self._chain_ok(), []
        for vh in self.chain.state.vehicles:
            reg = self.chain.state.history[vh][0]
            p = self._payload(reg["data_hash"]) or {}
            if not p.get("vin"):
                continue
            r = self.vehicle_report(p["vin"], ok)
            out.append({"vin": p["vin"], "make": p.get("make", "?"), "model": p.get("model", "?"),
                        "year": p.get("year", ""), "owner_name": r["owner_name"], "odometer": r["odometer"],
                        "stolen": r["stolen"], "lien": bool(r["lien_holder"]), "trust": r["trust"],
                        "events": len(r["history"]), "accidents": r["accidents"]})
        return sorted(out, key=lambda v: v["vin"])

    def stats(self) -> dict:
        return analytics.fleet_stats(self)

    def find_by_data_hash(self, digest: str) -> list[dict]:
        """Public verification: which on-chain record (if any) anchors this document hash?"""
        digest, hits = digest.strip().lower(), []
        for vh, hist in self.chain.state.history.items():
            for rec in hist:
                if rec["data_hash"] == digest:
                    reg = self._payload(self.chain.state.history[vh][0]["data_hash"]) or {}
                    hits.append({**rec, "vin": reg.get("vin", vh[:12]), "actor_name": self.name_of(rec["actor"]),
                                 "status": self.verify_record(rec)})
        return hits

    def find_tx(self, tx_hash: str) -> dict | None:
        tx_hash = tx_hash.strip().lower()
        for b in self.chain.blocks:
            for tx in b["transactions"]:
                if tx["tx_hash"] == tx_hash:
                    return {"block": b["index"], "block_hash": b["hash"], "fn": tx["fn"],
                            "sender": self.name_of(tx["sender"]), "timestamp": tx["timestamp"]}
        return None

    # ------------------------------------------------------------ tamper lab
    ATTACKS = {
        "odometer": ("Rewrite an odometer reading inside a sealed block",
                     "Attacker edits a service transaction to show 1,000 km."),
        "owner": ("Redirect an ownership transfer to the attacker",
                  "Attacker changes the new owner address in a signed transfer."),
        "delete": ("Delete an accident record's block",
                   "Attacker removes a block to hide an accident."),
        "rehash": ("Edit a block and recompute its hash (a careful attacker)",
                   "Attacker changes a block, then fixes the hash so the block looks self-consistent."),
        "offchain": ("Edit the off-chain service description",
                     "Attacker edits the database row, leaving the blockchain untouched."),
    }

    def simulate_attack(self, kind: str) -> dict:
        """Run an attack against an in-memory COPY of the chain; the real ledger is never touched."""
        import copy
        from .ledger.chain import block_header
        sim = Blockchain(None, self.chain.admin, self.chain.validators, self.clock)
        sim.blocks = copy.deepcopy(self.chain.blocks)
        title, story = self.ATTACKS[kind]
        find = lambda fn: next((b for b in sim.blocks if b["transactions"] and b["transactions"][0]["fn"] == fn), None)
        what = "No suitable record found"
        if kind == "odometer" and (b := find("add_service")):
            b["transactions"][0]["args"]["odometer"] = 1000; what = f"Block #{b['index']}: odometer set to 1,000"
        elif kind == "owner" and (b := find("transfer_ownership")):
            b["transactions"][0]["args"]["new_owner"] = "0x" + "bad" * 13 + "b"; what = f"Block #{b['index']}: new owner replaced"
        elif kind == "delete" and (b := find("report_accident")):
            sim.blocks.remove(b); what = f"Block #{b['index']} removed from the chain"
        elif kind == "rehash" and (b := find("add_service")):
            b["transactions"][0]["args"]["odometer"] = 1000
            b["hash"] = sha256_hex(block_header(b)); what = f"Block #{b['index']}: odometer edited and block hash recomputed"
        elif kind == "offchain":
            rec = next((r for h in self.chain.state.history.values() for r in h if r["type"] == "SERVICE"), None)
            if rec:
                text = (self.stored_payload_text(rec["data_hash"]) or "").replace("service", "EDITED", 1) or "x"
                changed = sha256_hex(canonical_json(json.loads(text))) != rec["data_hash"] or text != self.stored_payload_text(rec["data_hash"])
                return {"kind": kind, "title": title, "story": story, "detected": True,
                        "what": "Service description edited in the database", "errors": [
                            "Stored JSON no longer hashes to the on-chain data hash: record flagged Tampered"] if changed else [],
                        "total": 1}
        ok, errs = sim.validate_chain()
        return {"kind": kind, "title": title, "story": story, "detected": not ok, "what": what,
                "errors": errs[:6], "total": len(errs)}

    def compare(self, vin_a: str, vin_b: str) -> list[dict]:
        ok = self._chain_ok()
        return [r for r in (self.vehicle_report(v, ok) for v in (vin_a, vin_b)) if r]

    # ------------------------------------------------------------ demo helpers
    def tamper_offchain(self, vin: str, index: int):
        """Demo: silently edit an off-chain record to show the hash check catches it."""
        rec = self.chain.state.history[self.vin_hash(vin)][index]
        p = self._payload(rec["data_hash"])
        p["description" if "description" in p else "make"] = "EDITED AFTER THE FACT"
        self.db.execute("UPDATE records SET payload=? WHERE data_hash=?",
                        (canonical_json(p), rec["data_hash"]))
        self.db.commit()
