"""Python mirror of contracts/VehicleLedger.sol.

The same business rules are enforced here so the full workflow can be demonstrated
without an Ethereum node. Every rule maps 1:1 to a `require`/custom error in Solidity.
"""
from __future__ import annotations

from enum import Enum


class ContractError(Exception):
    """Raised when a transaction violates a smart-contract rule (a 'revert')."""


class Role(str, Enum):
    ADMIN = "ADMIN"
    REGISTRAR = "REGISTRAR"          # RTO / manufacturer: registers vehicles, flags theft
    SERVICE_CENTER = "SERVICE_CENTER"
    INSURER = "INSURER"              # insurers / police: report accidents
    LENDER = "LENDER"                # banks / finance companies: place and release liens


class EventType(str, Enum):
    REGISTRATION = "REGISTRATION"
    SERVICE = "SERVICE"
    ACCIDENT = "ACCIDENT"
    OWNERSHIP_TRANSFER = "OWNERSHIP_TRANSFER"
    STATUS_CHANGE = "STATUS_CHANGE"
    LIEN_PLACED = "LIEN_PLACED"
    LIEN_RELEASED = "LIEN_RELEASED"


class VehicleContract:
    """World state, rebuilt by replaying transactions from the chain."""

    def __init__(self, admin: str):
        self.admin = admin
        self.roles: dict[str, str] = {}
        self.vehicles: dict[str, dict] = {}
        self.history: dict[str, list[dict]] = {}

    # ---------------------------------------------------------------- helpers
    def has_role(self, addr: str, role: Role) -> bool:
        return addr == self.admin or self.roles.get(addr) == role.value

    def _require_role(self, sender: str, role: Role):
        if not self.has_role(sender, role):
            raise ContractError(f"Unauthorized: sender lacks role {role.value}")

    def _vehicle(self, vin_hash: str) -> dict:
        if vin_hash not in self.vehicles:
            raise ContractError("Vehicle not registered")
        return self.vehicles[vin_hash]

    def _check_odometer(self, v: dict, odometer: int):
        if odometer < v["odometer"]:
            raise ContractError(
                f"Odometer rollback detected: {odometer} km < last recorded {v['odometer']} km")

    def _append(self, vin_hash, etype, actor, ts, odometer, data_hash, extra=None):
        self.history[vin_hash].append({
            "index": len(self.history[vin_hash]), "type": etype.value, "actor": actor,
            "timestamp": ts, "odometer": odometer, "data_hash": data_hash, **(extra or {})})

    # ------------------------------------------------------------ entry point
    def execute(self, sender: str, fn: str, args: dict, ts: int):
        handler = getattr(self, f"fn_{fn}", None)
        if handler is None:
            raise ContractError(f"Unknown function '{fn}'")
        handler(sender, ts, **args)

    # -------------------------------------------------------------- functions
    def fn_grant_role(self, sender, ts, account, role):
        if sender != self.admin:
            raise ContractError("Unauthorized: only admin can grant roles")
        Role(role)  # validates
        self.roles[account] = role

    def fn_register_vehicle(self, sender, ts, vin_hash, owner, odometer, data_hash):
        self._require_role(sender, Role.REGISTRAR)
        if vin_hash in self.vehicles:
            raise ContractError("Vehicle already registered (duplicate VIN)")
        self.vehicles[vin_hash] = {"owner": owner, "odometer": odometer,
                                   "stolen": False, "registered_at": ts, "lien": None}
        self.history[vin_hash] = []
        self._append(vin_hash, EventType.REGISTRATION, sender, ts, odometer, data_hash,
                     {"to": owner})

    def fn_add_service(self, sender, ts, vin_hash, odometer, data_hash):
        self._require_role(sender, Role.SERVICE_CENTER)
        v = self._vehicle(vin_hash)
        self._check_odometer(v, odometer)
        v["odometer"] = odometer
        self._append(vin_hash, EventType.SERVICE, sender, ts, odometer, data_hash)

    def fn_report_accident(self, sender, ts, vin_hash, odometer, data_hash):
        self._require_role(sender, Role.INSURER)
        v = self._vehicle(vin_hash)
        self._check_odometer(v, odometer)
        v["odometer"] = odometer
        self._append(vin_hash, EventType.ACCIDENT, sender, ts, odometer, data_hash)

    def fn_transfer_ownership(self, sender, ts, vin_hash, new_owner, odometer, data_hash):
        v = self._vehicle(vin_hash)
        if sender != v["owner"]:
            raise ContractError("Unauthorized: only the current owner can transfer")
        if v["stolen"]:
            raise ContractError("Vehicle is flagged stolen; transfer blocked")
        if v["lien"]:
            raise ContractError("Active lien: transfer blocked until the lender releases it")
        if new_owner == sender:
            raise ContractError("New owner must differ from current owner")
        self._check_odometer(v, odometer)
        old, v["owner"], v["odometer"] = v["owner"], new_owner, odometer
        self._append(vin_hash, EventType.OWNERSHIP_TRANSFER, sender, ts, odometer, data_hash,
                     {"from": old, "to": new_owner})

    def fn_set_stolen(self, sender, ts, vin_hash, stolen, data_hash):
        self._require_role(sender, Role.REGISTRAR)
        v = self._vehicle(vin_hash)
        v["stolen"] = bool(stolen)
        self._append(vin_hash, EventType.STATUS_CHANGE, sender, ts, v["odometer"], data_hash,
                     {"stolen": bool(stolen)})

    def fn_place_lien(self, sender, ts, vin_hash, data_hash):
        self._require_role(sender, Role.LENDER)
        v = self._vehicle(vin_hash)
        if v["lien"]:
            raise ContractError("A lien is already active on this vehicle")
        v["lien"] = sender
        self._append(vin_hash, EventType.LIEN_PLACED, sender, ts, v["odometer"], data_hash)

    def fn_release_lien(self, sender, ts, vin_hash, data_hash):
        v = self._vehicle(vin_hash)
        if not v["lien"]:
            raise ContractError("No active lien on this vehicle")
        if sender != v["lien"] and sender != self.admin:
            raise ContractError("Unauthorized: only the lender that placed the lien can release it")
        v["lien"] = None
        self._append(vin_hash, EventType.LIEN_RELEASED, sender, ts, v["odometer"], data_hash)
