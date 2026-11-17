#!/usr/bin/env python3
"""Check a ChainGuard audit proof yourself — without trusting the ChainGuard server.

The dashboard's "Verify" button asks the server whether a record is intact.
This script does the maths locally instead, so an auditor, a customer or a
regulator can check a record they were handed:

  1. hash the audit record exactly as ChainGuard does (canonical JSON → Keccak-256)
  2. walk the Merkle siblings up to a root
  3. compare that root with the one the batch id commits to ("batch:0x<root>")
  4. optionally (--rpc) read the root anchored in AgentTrustRegistry on-chain

Usage
    # save a proof from the API, then check it offline
    curl -s http://127.0.0.1:8001/api/audit/actions/<ACTION_ID>/proof > proof.json
    python verify_proof.py proof.json

    # or fetch and check in one go, including the on-chain root
    python verify_proof.py --fetch http://127.0.0.1:8001 <ACTION_ID> --rpc http://127.0.0.1:8545

    # what if someone edited the record? (should fail)
    python verify_proof.py proof.json --record edited_record.json

Exit codes: 0 verified · 1 does not verify (tampered or wrong proof) · 2 pending / input problem.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web3 import Web3  # noqa: E402

from backend.core.blockchain.audit_log import hash_record  # noqa: E402
from backend.core.blockchain.merkle import _digest_bytes, _hex, hash_pair  # noqa: E402

# Only the getter we need: auditBatches(bytes32) -> AuditBatch struct
AUDIT_BATCHES_ABI = [{
    "name": "auditBatches", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "", "type": "bytes32"}],
    "outputs": [
        {"name": "merkleRoot", "type": "bytes32"}, {"name": "logCount", "type": "uint64"},
        {"name": "startTimestamp", "type": "uint64"}, {"name": "endTimestamp", "type": "uint64"},
        {"name": "anchoredBy", "type": "address"}, {"name": "anchoredAt", "type": "uint64"},
        {"name": "exists", "type": "bool"},
    ],
}]


class Check:
    def __init__(self):
        self.steps: list[tuple[bool | None, str]] = []

    def add(self, ok: bool | None, text: str) -> bool | None:
        self.steps.append((ok, text))
        return ok

    def render(self) -> str:
        mark = {True: "✓", False: "✗", None: "·"}
        return "\n".join(f"  {mark[ok]} {text}" for ok, text in self.steps)


def root_from_proof(leaf: str, siblings: list[dict]) -> str:
    """Fold the sibling list (leaf → root) into a Merkle root."""
    current = _hex(_digest_bytes(leaf))
    for i, s in enumerate(siblings):
        if not isinstance(s, dict) or s.get("position") not in ("left", "right"):
            raise ValueError(f"sibling {i} needs a 'hash' and a 'position' of left/right")
        sib = _hex(_digest_bytes(s["hash"]))
        current = hash_pair(sib, current) if s["position"] == "left" else hash_pair(current, sib)
    return current


def onchain_root(rpc_url: str, contract_address: str, batch_id: str) -> str | None:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=AUDIT_BATCHES_ABI)
    stored = contract.functions.auditBatches(Web3.keccak(text=batch_id)).call()
    return Web3.to_hex(stored[0]) if stored[6] else None


def verify(proof: dict, record: dict | None = None, rpc: str | None = None,
           contract: str | None = None) -> tuple[str, Check]:
    """Returns (verdict, steps) where verdict is VERIFIED, TAMPERED, PENDING or UNCHECKED."""
    c = Check()
    record = record if record is not None else proof.get("record")
    if not isinstance(record, dict):
        c.add(False, "proof has no audit record")
        return "TAMPERED", c
    action_id = proof.get("action_id") or record.get("action_id")
    if record.get("action_id") != action_id:
        c.add(False, f"record is for action {record.get('action_id')!r}, proof is for {action_id!r}")
        return "TAMPERED", c
    leaf = hash_record(record)
    c.add(None, f"record hash  {leaf}")

    batch_id = proof.get("batch_id")
    siblings = proof.get("proof_siblings", proof.get("siblings"))
    if not batch_id or siblings is None:
        c.add(None, "record is not in an anchored batch yet — try again after the next batch")
        return "PENDING", c
    if not str(batch_id).startswith("batch:0x"):
        c.add(False, f"unexpected batch id {batch_id!r}")
        return "TAMPERED", c
    claimed_root = _hex(_digest_bytes(batch_id.split(":", 1)[1]))

    try:
        root = root_from_proof(leaf, siblings)
    except (ValueError, TypeError, KeyError) as e:
        c.add(False, f"malformed proof: {e}")
        return "TAMPERED", c
    if not c.add(root == claimed_root, f"Merkle root {root} ({len(siblings)} sibling(s))"
                 + ("" if root == claimed_root else f" ≠ batch root {claimed_root}")):
        return "TAMPERED", c
    c.add(True, "root matches the batch id the record was filed under")

    if not rpc:
        c.add(None, "on-chain check skipped (pass --rpc to compare with the anchored root)")
        return "UNCHECKED", c
    address = contract or (proof.get("blockchain") or {}).get("contract_address")
    if not address:
        c.add(False, "no contract address in the proof — pass --contract")
        return "TAMPERED", c
    try:
        anchored = onchain_root(rpc, address, batch_id)
    except Exception as e:  # network / RPC errors are not evidence of tampering
        c.add(None, f"could not read the chain at {rpc}: {e}")
        return "UNCHECKED", c
    if anchored is None:
        c.add(False, f"batch {batch_id[:22]}… is not anchored in contract {address}")
        return "TAMPERED", c
    if not c.add(anchored.lower() == root.lower(), f"on-chain root {anchored} at {address}"):
        return "TAMPERED", c
    return "VERIFIED", c


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proof", nargs="?", help="proof JSON saved from /api/audit/actions/<id>/proof")
    ap.add_argument("--fetch", nargs=2, metavar=("API_URL", "ACTION_ID"), help="download the proof from a ChainGuard API")
    ap.add_argument("--record", help="check this record JSON instead of the one inside the proof")
    ap.add_argument("--rpc", help="Ethereum JSON-RPC URL to compare the root with the anchored one")
    ap.add_argument("--contract", help="AgentTrustRegistry address (default: the one in the proof)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        if args.fetch:
            base, action = args.fetch
            with urllib.request.urlopen(f"{base.rstrip('/')}/api/audit/actions/{action}/proof", timeout=15) as r:
                proof = json.load(r)
        elif args.proof:
            proof = _load(args.proof)
        else:
            ap.error("give a proof file or --fetch API_URL ACTION_ID")
        record = _load(args.record) if args.record else None
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    verdict, steps = verify(proof, record, args.rpc, args.contract)
    if args.json:
        print(json.dumps({"verdict": verdict, "steps": [{"ok": ok, "detail": t} for ok, t in steps.steps]}, indent=2))
    else:
        label = {"VERIFIED": "✅ VERIFIED", "UNCHECKED": "✅ Merkle proof valid (chain not checked)",
                 "TAMPERED": "⛔ DOES NOT VERIFY", "PENDING": "⏳ PENDING"}[verdict]
        print(f"{label} — action {proof.get('action_id') or (record or {}).get('action_id')}")
        print(steps.render())
    return {"VERIFIED": 0, "UNCHECKED": 0, "TAMPERED": 1, "PENDING": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
