"""Explainable vehicle trust score, anomaly detection and fleet statistics.

The score is deliberately transparent: every deduction is listed with a reason, so a buyer
(or an examiner) can see exactly why a vehicle scored what it did. It is a decision aid,
not a guarantee: it can only reason about records that exist on the ledger.
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict

SERVICE_GAP_KM = 15_000          # recommended interval is usually 10,000 to 15,000 km
MAX_KM_PER_DAY = 600             # above this between two records is physically implausible
SEVERITY_PENALTY = {"minor": 6, "major": 15, "total loss": 35}

GRADES = [(85, "A", "Excellent"), (70, "B", "Good"), (55, "C", "Fair"), (40, "D", "Caution"), (0, "E", "High risk")]


def grade_for(score: int) -> tuple[str, str]:
    for floor, g, label in GRADES:
        if score >= floor:
            return g, label
    return "E", "High risk"


def anomalies(history: list[dict]) -> list[dict]:
    """Service gaps and implausible usage between consecutive records."""
    out, last_service_odo, prev = [], None, None
    for h in history:
        if prev is not None:
            days = max((h["timestamp"] - prev["timestamp"]) / 86400, 1.0)
            km = h["odometer"] - prev["odometer"]
            if km / days > MAX_KM_PER_DAY and km > 2000:
                out.append({"index": h["index"], "kind": "usage",
                            "text": f"{km:,} km in {days:.0f} day(s) is implausible ({km / days:,.0f} km/day)"})
        if h["type"] in ("REGISTRATION", "SERVICE"):
            if h["type"] == "SERVICE" and last_service_odo is not None and h["odometer"] - last_service_odo > SERVICE_GAP_KM:
                out.append({"index": h["index"], "kind": "gap",
                            "text": f"{h['odometer'] - last_service_odo:,} km since the previous service"})
            last_service_odo = h["odometer"]
        prev = h
    return out


def trust_score(r: dict) -> dict:
    """r is the dict built by LedgerService.vehicle_report (before the score is attached)."""
    factors, score = [], 100

    def hit(label, delta, note):
        nonlocal score
        score += delta
        factors.append({"label": label, "delta": delta, "note": note})

    if not r["chain_ok"]:
        hit("Chain integrity", -100, "The blockchain failed its integrity check")
    bad = [h for h in r["history"] if h["status"] != "verified"]
    if bad:
        hit("Off-chain evidence", -60, f"{len(bad)} record(s) do not match their on-chain hash")
    if r["stolen"]:
        hit("Stolen flag", -60, "Registrar has flagged this vehicle as stolen")
    if r["lien_holder"]:
        hit("Active lien", -8, f"Loan outstanding with {r['lien_holder_name']}; sale needs lender release")
    for h in r["history"]:
        if h["type"] == "ACCIDENT":
            sev = (h["details"] or {}).get("severity", "")
            hit("Accident", -SEVERITY_PENALTY.get(sev, 10), f"{sev or 'unknown severity'} accident at {h['odometer']:,} km")
    if r["owners"] > 3:
        hit("Many owners", -min(12, 4 * (r["owners"] - 3)), f"{r['owners']} owners in total")
    anoms = anomalies(r["history"])
    for a in anoms[:4]:
        hit("Usage gap" if a["kind"] == "gap" else "Implausible usage", -5 if a["kind"] == "gap" else -10, a["text"])
    if r["services"] == 0 and r["odometer"] > 10_000:
        hit("No service history", -10, "More than 10,000 km driven with no service record")
    if r["services"] >= 2 and not any(f["label"] in ("Usage gap",) for f in factors):
        factors.append({"label": "Regular servicing", "delta": 0, "note": f"{r['services']} services with no long gaps"})
    if r["owners"] <= 2 and not r["stolen"]:
        factors.append({"label": "Ownership", "delta": 0, "note": f"{r['owners']} owner(s): stable ownership"})
    score = max(0, min(100, score))
    g, label = grade_for(score)
    return {"score": score, "grade": g, "label": label, "factors": factors, "anomalies": anoms}


def category_of(reason: str) -> str:
    s = reason.lower()
    for key, cat in (("odometer", "Odometer rollback"), ("lien", "Lien conflict"), ("stolen", "Stolen vehicle"),
                     ("lacks role", "Wrong role"), ("only the current owner", "Not the owner"),
                     ("only admin", "Wrong role"), ("only the lender", "Wrong role"), ("duplicate", "Duplicate VIN"),
                     ("signature", "Forged signature"), ("public key", "Forged signature"), ("nonce", "Replay attack")):
        if key in s:
            return cat
    return "Other rule violation"


def fleet_stats(svc) -> dict:
    types, months, scores, grades = Counter(), defaultdict(int), [], Counter()
    vehicles = svc.chain.state.vehicles
    for vh in vehicles:
        for rec in svc.chain.state.history[vh]:
            types[rec["type"]] += 1
            months[time.strftime("%Y-%m", time.gmtime(rec["timestamp"]))] += 1
    for v in svc.all_vehicles():
        scores.append(v["trust"]["score"]); grades[v["trust"]["grade"]] += 1
    rej = svc.rejection_summary()
    return {"vehicles": len(vehicles), "events": sum(types.values()), "blocks": svc.chain.height,
            "rejected": sum(rej.values()), "avg_score": round(sum(scores) / len(scores)) if scores else 0,
            "types": dict(types), "months": dict(sorted(months.items())), "grades": dict(grades),
            "rejections": rej, "stolen": sum(1 for v in vehicles.values() if v["stolen"]),
            "liens": sum(1 for v in vehicles.values() if v["lien"])}
