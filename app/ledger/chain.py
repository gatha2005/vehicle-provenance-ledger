"""Permissioned blockchain: signed transactions, hash-linked blocks, Proof-of-Authority."""
from __future__ import annotations

import json
import os
import time
from typing import Callable

from .contract import ContractError, VehicleContract
from .crypto import (Wallet, address_from_pubkey, canonical_json, merkle_root,
                     sha256_hex, verify_signature)


class ChainError(Exception):
    """Raised for invalid transactions (bad signature, replayed nonce, ...)."""


def tx_payload(tx: dict) -> str:
    return canonical_json({k: tx[k] for k in ("sender", "fn", "args", "nonce", "timestamp")})


def make_tx(wallet: Wallet, fn: str, args: dict, nonce: int, ts: int | None = None) -> dict:
    tx = {"sender": wallet.address, "pubkey": wallet.public_hex, "fn": fn, "args": args,
          "nonce": nonce, "timestamp": ts or int(time.time())}
    tx["signature"] = wallet.sign(tx_payload(tx))
    tx["tx_hash"] = sha256_hex(tx_payload(tx) + tx["signature"])
    return tx


def block_header(b: dict) -> str:
    return canonical_json({k: b[k] for k in
                           ("index", "timestamp", "prev_hash", "merkle_root", "validator")})


class Blockchain:
    """Ledger of blocks. Validators take turns sealing blocks (round-robin PoA)."""

    def __init__(self, path: str | None, admin: str, validators: list[dict],
                 clock: Callable[[], int] = lambda: int(time.time())):
        self.path, self.admin, self.validators, self.clock = path, admin, validators, clock
        self.blocks: list[dict] = []
        self.pending: list[dict] = []
        self.state = VehicleContract(admin)
        self.nonces: dict[str, int] = {}
        self.validator_wallets: dict[str, Wallet] = {}
        if path and os.path.exists(path):
            self._load()
        else:
            self._genesis()

    # ------------------------------------------------------------ construction
    def _genesis(self):
        g = {"index": 0, "timestamp": self.clock(), "prev_hash": "0" * 64,
             "merkle_root": merkle_root([]), "validator": "GENESIS", "transactions": []}
        g["hash"] = sha256_hex(block_header(g))
        g["signature"] = ""
        self.blocks = [g]
        self.save()

    def register_validator_wallets(self, wallets: list[Wallet]):
        self.validator_wallets = {w.address: w for w in wallets}

    # ------------------------------------------------------------ persistence
    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"admin": self.admin, "validators": self.validators,
                       "blocks": self.blocks, "pending": self.pending}, f, indent=1)

    def _load(self):
        with open(self.path) as f:
            d = json.load(f)
        self.admin, self.validators = d["admin"], d["validators"]
        self.blocks, self.pending = d["blocks"], d.get("pending", [])
        self.rebuild_state()

    def rebuild_state(self):
        """Replay every transaction on the chain to recompute world state."""
        self.state, self.nonces, self.replay_errors = VehicleContract(self.admin), {}, []
        for tx in [t for b in self.blocks for t in b["transactions"]] + self.pending:
            try:
                self.state.execute(tx["sender"], tx["fn"], tx["args"], tx["timestamp"])
            except (ContractError, KeyError, TypeError) as e:
                self.replay_errors.append(f"{tx.get('tx_hash', '?')[:10]}: {e}")
            self.nonces[tx["sender"]] = tx["nonce"] + 1

    # -------------------------------------------------------------- transactions
    def next_nonce(self, address: str) -> int:
        return self.nonces.get(address, 0)

    def submit(self, tx: dict) -> str:
        """Validate (signature, nonce, contract rules) and add to the pending pool."""
        if address_from_pubkey(tx["pubkey"]) != tx["sender"]:
            raise ChainError("Sender address does not match public key")
        if not verify_signature(tx["pubkey"], tx_payload(tx), tx["signature"]):
            raise ChainError("Invalid digital signature")
        if tx["nonce"] != self.next_nonce(tx["sender"]):
            raise ChainError(f"Bad nonce {tx['nonce']} (expected {self.next_nonce(tx['sender'])}); "
                             "possible replay")
        self.state.execute(tx["sender"], tx["fn"], tx["args"], tx["timestamp"])  # may raise
        self.nonces[tx["sender"]] = tx["nonce"] + 1
        self.pending.append(tx)
        self.save()
        return tx["tx_hash"]

    # ------------------------------------------------------------------ consensus
    def expected_validator(self, height: int) -> dict:
        return self.validators[height % len(self.validators)]

    def mine(self) -> dict | None:
        """Seal pending transactions into a block signed by the scheduled validator."""
        if not self.pending:
            return None
        height = len(self.blocks)
        val = self.expected_validator(height)
        wallet = self.validator_wallets.get(val["address"])
        if wallet is None:
            raise ChainError(f"No key available for scheduled validator {val['name']}")
        block = {"index": height, "timestamp": self.clock(), "prev_hash": self.blocks[-1]["hash"],
                 "merkle_root": merkle_root([t["tx_hash"] for t in self.pending]),
                 "validator": val["address"], "transactions": self.pending}
        block["hash"] = sha256_hex(block_header(block))
        block["signature"] = wallet.sign(block["hash"])
        self.blocks.append(block)
        self.pending = []
        self.save()
        return block

    # ----------------------------------------------------------------- validation
    def validate_chain(self) -> tuple[bool, list[str]]:
        """Independently re-verify hashes, links, Merkle roots, signatures and contract rules."""
        errs: list[str] = []
        state, nonces = VehicleContract(self.admin), {}
        for i, b in enumerate(self.blocks):
            if sha256_hex(block_header(b)) != b["hash"]:
                errs.append(f"Block {i}: header hash mismatch (block contents were altered)")
            if i > 0:
                if b["prev_hash"] != self.blocks[i - 1]["hash"]:
                    errs.append(f"Block {i}: broken link to previous block")
                val = self.expected_validator(i)
                if b["validator"] != val["address"]:
                    errs.append(f"Block {i}: sealed by wrong validator (not their turn)")
                elif not verify_signature(val["pubkey"], b["hash"], b["signature"]):
                    errs.append(f"Block {i}: invalid validator signature")
            if merkle_root([t["tx_hash"] for t in b["transactions"]]) != b["merkle_root"]:
                errs.append(f"Block {i}: Merkle root mismatch (a transaction was altered)")
            for tx in b["transactions"]:
                label = f"Block {i} tx {tx['tx_hash'][:10]}"
                if not verify_signature(tx["pubkey"], tx_payload(tx), tx["signature"]):
                    errs.append(f"{label}: invalid signature")
                if nonces.get(tx["sender"], 0) != tx["nonce"]:
                    errs.append(f"{label}: nonce out of order")
                nonces[tx["sender"]] = tx["nonce"] + 1
                try:
                    state.execute(tx["sender"], tx["fn"], tx["args"], tx["timestamp"])
                except (ContractError, KeyError, TypeError) as e:
                    errs.append(f"{label}: violates contract rules ({e})")
        return (not errs), errs

    @property
    def height(self) -> int:
        return len(self.blocks) - 1
