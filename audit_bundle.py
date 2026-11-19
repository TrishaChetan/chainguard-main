#!/usr/bin/env python3
"""Export every audit record with its proof, and verify a whole export offline.

An auditor wants *all* the evidence in one file they can keep, not a live
server to poke. This tool builds that file and checks it.

    # 1. export: every record + Merkle proof from a running ChainGuard API
    python audit_bundle.py export http://127.0.0.1:8001 -o evidence.json

    # 2. verify: re-hash every record and re-check every proof locally
    python audit_bundle.py verify evidence.json
    python audit_bundle.py verify evidence.json --rpc http://127.0.0.1:8545   # + anchored roots on-chain

Verification uses the same checks as verify_proof.py, record by record, and
reports per batch. Each batch's on-chain root is read once.
Exit codes: 0 everything verified · 1 something does not verify · 2 bad input.
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

import verify_proof

BUNDLE_VERSION = "1.0"


def _get(url: str, timeout: float = 20) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def export_bundle(api: str, page: int = 500, fetch=_get) -> dict:
    """Pull every record id from /api/audit/logs, then each proof."""
    api = api.rstrip("/")
    ids, offset = [], 0
    while True:
        data = fetch(f"{api}/api/audit/logs?limit={page}&offset={offset}")
        recs = data.get("records", [])
        ids += [r["action_id"] for r in recs if r.get("action_id")]
        offset += len(recs)
        if not recs or offset >= data.get("total", 0):
            break
    proofs = [fetch(f"{api}/api/audit/actions/{urllib.parse.quote(a, safe='')}/proof") for a in dict.fromkeys(ids)]
    return {"bundle_version": BUNDLE_VERSION, "source": api,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "record_count": len(proofs), "proofs": proofs}


def verify_bundle(bundle: dict, rpc: str | None = None, contract: str | None = None) -> dict:
    proofs = bundle.get("proofs")
    if not isinstance(proofs, list):
        raise ValueError("not a ChainGuard evidence bundle (no 'proofs' list)")
    # one RPC call per batch, not per record
    original = verify_proof.onchain_root
    verify_proof.onchain_root = functools.lru_cache(maxsize=None)(original)
    try:
        results = []
        for p in proofs:
            verdict, steps = verify_proof.verify(p, rpc=rpc, contract=contract)
            problem = next((t for ok, t in steps.steps if ok is False), None)
            results.append({"action_id": p.get("action_id"), "batch_id": p.get("batch_id"),
                            "verdict": verdict, "problem": problem})
    finally:
        verify_proof.onchain_root = original

    # The same action id twice with *different* contents means one copy was edited.
    versions: dict[str, set] = defaultdict(set)
    for p in proofs:
        if isinstance(p.get("record"), dict):
            versions[p.get("action_id")].add(verify_proof.hash_record(p["record"]))
    for r in results:
        if len(versions.get(r["action_id"], ())) > 1 and r["verdict"] != "TAMPERED":
            r["verdict"], r["problem"] = "TAMPERED", "two different versions of this record are in the bundle"

    batches: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        batches[r["batch_id"] or "(not batched yet)"][r["verdict"]] += 1
    totals = Counter(r["verdict"] for r in results)
    return {"records": len(results), "totals": dict(totals), "batches": {b: dict(c) for b, c in batches.items()},
            "tampered": [r for r in results if r["verdict"] == "TAMPERED"],
            "ok": totals["TAMPERED"] == 0 and len(results) > 0,
            "chain_checked": bool(rpc) and totals["UNCHECKED"] == 0}


def format_report(res: dict, bundle: dict) -> str:
    t = res["totals"]
    head = "✅ ALL RECORDS VERIFY" if res["ok"] else "⛔ TAMPERING DETECTED" if res["tampered"] else "⚠️ NOTHING TO VERIFY"
    lines = [f"{head} — {res['records']} record(s) from {bundle.get('source', '?')} "
             f"(exported {bundle.get('exported_at', '?')})",
             f"  verified on-chain {t.get('VERIFIED', 0)} · Merkle-only {t.get('UNCHECKED', 0)} · "
             f"pending {t.get('PENDING', 0)} · tampered {t.get('TAMPERED', 0)}", "", "  batch" + " " * 68 + "records  status"]
    for b, c in sorted(res["batches"].items()):
        n = sum(c.values())
        status = "TAMPERED" if c.get("TAMPERED") else "pending" if c.get("PENDING") == n else \
            "verified" if c.get("VERIFIED") == n else "merkle ok"
        lines.append(f"  {b:<73}{n:>7}  {status}")
    if res["tampered"]:
        lines += ["", "  tampered records:"]
        lines += [f"    ✗ {r['action_id']}: {r['problem']}" for r in res["tampered"]]
    if not res["chain_checked"] and res["records"]:
        lines += ["", "  (on-chain roots not checked — add --rpc to confirm against the blockchain)"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="download all records + proofs into one JSON file")
    e.add_argument("api", help="ChainGuard API base URL, e.g. http://127.0.0.1:8001")
    e.add_argument("-o", "--output", required=True)
    v = sub.add_parser("verify", help="check every record in an exported bundle")
    v.add_argument("bundle")
    v.add_argument("--rpc", help="Ethereum JSON-RPC URL to compare roots with the anchored ones")
    v.add_argument("--contract", help="AgentTrustRegistry address (default: from each proof)")
    v.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "export":
            bundle = export_bundle(args.api)
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, indent=2)
            batched = sum(1 for p in bundle["proofs"] if p.get("batch_id"))
            print(f"{bundle['record_count']} record(s) ({batched} in anchored batches) -> {args.output}")
            return 0
        with open(args.bundle, encoding="utf-8") as fh:
            bundle = json.load(fh)
        res = verify_bundle(bundle, args.rpc, args.contract)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if args.json else format_report(res, bundle))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
