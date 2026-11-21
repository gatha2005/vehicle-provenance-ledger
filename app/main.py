"""Flask web application for the Vehicle Provenance Ledger."""
from __future__ import annotations

import json
import os
import re
import time

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from . import charts, qr
from .ledger import ContractError, canonical_json, sha256_hex
from .ledger.chain import ChainError
from .service import ACTORS, OWNER_KEYS, LedgerService

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def create_app(data_dir: str | None = None, clock=lambda: int(time.time())) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    svc = LedgerService(data_dir or os.environ.get("LEDGER_DATA_DIR", "data"), clock)
    app.config["svc"] = svc
    actor_choices = [(k, n) for k, n, _ in ACTORS]
    owner_choices = [(k, n) for k, n, _ in ACTORS if k in OWNER_KEYS]

    @app.template_filter("dt")
    def fmt_dt(ts):
        return time.strftime("%d %b %Y, %H:%M", time.gmtime(ts))

    @app.template_filter("day")
    def fmt_day(ts):
        return time.strftime("%d %b %Y", time.gmtime(ts))

    @app.template_filter("short")
    def short(h, n=10):
        return f"{h[:n]}…{h[-4:]}" if h and len(h) > n + 4 else h

    @app.template_filter("inr")
    def inr(v):
        return f"₹{int(v):,}"

    @app.context_processor
    def inject():
        return {"height": svc.chain.height, "actors": actor_choices, "owners": owner_choices,
                "current_actor": session.get("actor", ""), "pending": len(svc.chain.pending),
                "charts": charts, "TYPE_LABEL": charts.TYPE_LABEL, "TYPE_CLASS": charts.TYPE_CLASS}

    # ---------------------------------------------------------------- dashboard
    @app.get("/")
    def index():
        st = svc.stats()
        events = [(charts.TYPE_LABEL[k], v, charts.TYPE_CLASS[k]) for k, v in sorted(st["types"].items(), key=lambda kv: -kv[1])]
        months = [(time.strftime("%b %y", time.strptime(m, "%Y-%m")), v) for m, v in st["months"].items()]
        return render_template("index.html", st=st, vehicles=svc.all_vehicles(), ok=svc.chain.validate_chain()[0],
                               events=events, months=months,
                               rejections=list(st["rejections"].items()),
                               grades=[(g, st["grades"].get(g, 0)) for g in "ABCDE"])

    @app.get("/vehicle")
    def vehicle_lookup():
        return redirect(url_for("vehicle", vin=request.args.get("vin", "").strip().upper()))

    @app.get("/vehicle/<vin>")
    def vehicle(vin):
        try:
            report = svc.vehicle_report(vin)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("index"))
        if report is None:
            flash("No vehicle with that VIN on the ledger.", "error")
            return redirect(url_for("index"))
        link = request.host_url.rstrip("/") + url_for("vehicle", vin=report["vin"])
        return render_template("vehicle.html", r=report, qr_svg=qr.svg(link), link=link,
                               odo_chart=charts.odometer(report["history"], set(report["anomaly_idx"])))

    @app.get("/compare")
    def compare():
        vins = [v["vin"] for v in svc.all_vehicles()]
        a, b = request.args.get("a", ""), request.args.get("b", "")
        reports = []
        if a and b:
            try:
                reports = svc.compare(a, b)
            except ValueError as e:
                flash(str(e), "error")
        return render_template("compare.html", vins=vins, a=a, b=b, reports=reports, vehicles=svc.all_vehicles())

    # ------------------------------------------------------------------- record
    @app.post("/set-actor")
    def set_actor():
        session["actor"] = request.form.get("actor", "")
        return redirect(request.form.get("next") or url_for("record"))

    @app.route("/record", methods=["GET", "POST"])
    def record():
        if request.method == "POST":
            f, kind = request.form, request.form["kind"]
            actor = f["actor"]
            try:
                if kind == "register":
                    res = svc.register(actor, f["vin"], f["owner"], f["odometer"], f["make"],
                                       f["model"], f["year"], f["reg_no"])
                elif kind == "service":
                    res = svc.service(actor, f["vin"], f["odometer"], f["description"], f["cost"])
                elif kind == "accident":
                    res = svc.accident(actor, f["vin"], f["odometer"], f["severity"],
                                       f["description"], f["claim_id"])
                elif kind == "transfer":
                    res = svc.transfer(actor, f["vin"], f["new_owner"], f["odometer"], f["price"])
                elif kind == "stolen":
                    res = svc.set_stolen(actor, f["vin"], f.get("stolen") == "1", f["reference"])
                elif kind == "lien":
                    res = svc.place_lien(actor, f["vin"], f["loan_ref"], f["amount"])
                elif kind == "unlien":
                    res = svc.release_lien(actor, f["vin"], f["reference"])
                else:
                    raise ValueError("Unknown action")
                flash(f"Confirmed in block #{res['block']} · tx {res['tx_hash'][:12]}…", "ok")
                return redirect(url_for("vehicle", vin=f["vin"].strip().upper()))
            except ContractError as e:
                flash(f"Rejected by smart contract: {e}", "error")
            except (ChainError, ValueError, KeyError) as e:
                flash(f"Rejected: {e}", "error")
        return render_template("record.html", prefill=request.args.get("vin", ""))

    # ------------------------------------------------------------------- verify
    @app.route("/verify", methods=["GET", "POST"])
    def verify():
        result = None
        pre = request.args.get("hash", "").strip()
        if request.method == "POST" or pre:
            digests, source = [], ""
            f = request.files.get("file") if request.method == "POST" else None
            text = request.form.get("text", "").strip() if request.method == "POST" else pre
            tx = request.form.get("tx", "").strip() if request.method == "POST" else ""
            if f and f.filename:
                raw = f.read(2_000_000)
                digests.append(("SHA-256 of the uploaded file", sha256_hex(raw))); source = f.filename
                try:
                    digests.append(("SHA-256 of the canonical JSON", sha256_hex(canonical_json(json.loads(raw)))))
                except ValueError:
                    pass
            elif text:
                if HEX64.match(text):
                    digests.append(("Hash you entered", text.lower()))
                else:
                    digests.append(("SHA-256 of the text", sha256_hex(text)))
                    try:
                        digests.append(("SHA-256 of the canonical JSON", sha256_hex(canonical_json(json.loads(text)))))
                    except ValueError:
                        pass
            matches = []
            for label, d in digests:
                for hit in svc.find_by_data_hash(d):
                    matches.append({**hit, "label": label, "digest": d})
            result = {"digests": digests, "matches": matches, "source": source,
                      "tx": svc.find_tx(tx) if tx else None, "tx_query": tx}
        return render_template("verify.html", result=result, pre=pre)

    # ----------------------------------------------------- explorer and security
    @app.get("/explorer")
    def explorer():
        ok, errs = svc.chain.validate_chain()
        return render_template("explorer.html", blocks=list(reversed(svc.chain.blocks)),
                               ok=ok, errs=errs, names=svc.names, validators=svc.chain.validators)

    @app.get("/security")
    def security():
        ok, errs = svc.chain.validate_chain()
        st = svc.stats()
        checks = [("Block hashes recomputed", ok), ("Hash links intact", ok), ("Merkle roots match", ok),
                  ("Validator turns and signatures valid", ok), ("Transaction signatures valid", ok),
                  ("Every transaction replays through the contract rules", ok),
                  ("Off-chain records match on-chain hashes", all(
                      svc.verify_record(r) == "verified" for h in svc.chain.state.history.values() for r in h))]
        roles = [(svc.name_of(a), r) for a, r in svc.chain.state.roles.items()]
        return render_template("security.html", ok=ok, errs=errs, checks=checks, log=svc.rejection_log(),
                               roles=roles, st=st, validators=svc.chain.validators,
                               rej=list(st["rejections"].items()))

    @app.get("/tamper-lab")
    def tamper_lab():
        kind = request.args.get("attack")
        result = svc.simulate_attack(kind) if kind in svc.ATTACKS else None
        return render_template("tamper.html", attacks=svc.ATTACKS, result=result, kind=kind)

    # ---------------------------------------------------------------------- api
    @app.get("/api/vehicle/<vin>")
    def api_vehicle(vin):
        try:
            r = svc.vehicle_report(vin)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return (jsonify(r), 200) if r else (jsonify(error="not found"), 404)

    @app.get("/api/stats")
    def api_stats():
        return jsonify(svc.stats())

    @app.get("/api/chain/validate")
    def api_validate():
        ok, errs = svc.chain.validate_chain()
        return jsonify(valid=ok, errors=errs, height=svc.chain.height)

    return app
