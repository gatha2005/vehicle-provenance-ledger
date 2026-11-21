"""Cryptographic primitives: SHA-256 hashing, Merkle roots, Ed25519 signatures."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)


def canonical_json(obj) -> str:
    """Deterministic JSON so the same data always produces the same hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data) -> str:
    if not isinstance(data, (bytes, bytearray)):
        data = str(data).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def merkle_root(hashes: list[str]) -> str:
    """Merkle root of a list of hex hashes (duplicates the last node on odd levels)."""
    if not hashes:
        return sha256_hex(b"")
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def address_from_pubkey(pub_hex: str) -> str:
    """Account address = first 20 bytes of SHA-256(public key), like an Ethereum address."""
    return "0x" + sha256_hex(bytes.fromhex(pub_hex))[:40]


def verify_signature(pub_hex: str, message: str, sig_hex: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(
            bytes.fromhex(sig_hex), message.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


@dataclass
class Wallet:
    """A demo wallet (stands in for MetaMask). Keys are synthetic, never real."""
    name: str
    private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls, name: str) -> "Wallet":
        return cls(name, Ed25519PrivateKey.generate())

    @property
    def public_hex(self) -> str:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    @property
    def address(self) -> str:
        return address_from_pubkey(self.public_hex)

    def sign(self, message: str) -> str:
        return self.private_key.sign(message.encode()).hex()

    def export_private(self) -> str:
        return self.private_key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption()).hex()

    @classmethod
    def from_private(cls, name: str, priv_hex: str) -> "Wallet":
        return cls(name, Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex)))
