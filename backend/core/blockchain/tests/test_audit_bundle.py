"""audit_bundle.py: export everything, verify everything offline."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_bundle  # noqa: E402
import verify_proof  # noqa: E402
from test_verify_proof import RECORDS, api_proof  # noqa: E402


def bundle(proofs):
    return {"bundle_version": "1.0", "source": "test", "exported_at": "now", "proofs": proofs}


def test_whole_batch_verifies_and_one_edit_is_pinpointed():
    proofs = [api_proof(i) for i in range(5)]
    res = audit_bundle.verify_bundle(bundle(proofs))
    assert res["ok"] and res["totals"] == {"UNCHECKED": 5} and len(res["batches"]) == 1
    proofs = json.loads(json.dumps(proofs))             # don't mutate the shared fixtures
    proofs[3]["record"]["verdict"] = "BLOCK"            # someone edits one record (was ALLOW)
    res = audit_bundle.verify_bundle(bundle(proofs))
    assert not res["ok"] and [r["action_id"] for r in res["tampered"]] == ["act-3"]
    assert "TAMPERING DETECTED" in audit_bundle.format_report(res, bundle(proofs))


def test_conflicting_duplicate_is_tampering_but_identical_copy_is_not():
    a, b = api_proof(1), api_proof(1)
    assert audit_bundle.verify_bundle(bundle([a, b]))["ok"]
    forged = json.loads(json.dumps(a))
    forged["record"]["operation"] = "withdraw"
    res = audit_bundle.verify_bundle(bundle([a, forged]))
    assert not res["ok"] and len(res["tampered"]) == 2


def test_pending_records_and_chain_checked_once_per_batch(monkeypatch):
    calls = []
    root = api_proof(0)["batch_id"].split(":", 1)[1]

    def fake_root(rpc, addr, bid):
        calls.append(bid)
        return root
    monkeypatch.setattr(verify_proof, "onchain_root", fake_root)
    pending = {"action_id": "act-9", "record": {"action_id": "act-9"}, "proof_siblings": [], "batch_id": None}
    res = audit_bundle.verify_bundle(bundle([api_proof(i) for i in range(5)] + [pending]), rpc="http://chain")
    assert res["totals"] == {"VERIFIED": 5, "PENDING": 1} and res["ok"]
    assert len(calls) == 1                              # cached per batch
    assert verify_proof.onchain_root is fake_root       # cache removed afterwards


def test_export_pages_through_logs():
    proofs = {p["action_id"]: p for p in (api_proof(i) for i in range(5))}
    seen = []

    def fetch(url):
        seen.append(url)
        if "/api/audit/logs" in url:
            offset = int(url.split("offset=")[1])
            recs = [{"action_id": r["action_id"]} for r in RECORDS[offset:offset + 2]]
            return {"total": len(RECORDS), "records": recs}
        return proofs[url.rsplit("/", 2)[-2]]
    b = audit_bundle.export_bundle("http://api/", page=2, fetch=fetch)
    assert b["record_count"] == 5 and sum("/api/audit/logs" in u for u in seen) == 3
    assert audit_bundle.verify_bundle(b)["ok"]


def test_cli(tmp_path, capsys):
    f = tmp_path / "evidence.json"
    f.write_text(json.dumps(bundle([api_proof(i) for i in range(5)])))
    assert audit_bundle.main(["verify", str(f)]) == 0
    assert "ALL RECORDS VERIFY" in capsys.readouterr().out
    f.write_text(json.dumps({"nope": 1}))
    assert audit_bundle.main(["verify", str(f)]) == 2
    f.write_text(json.dumps(bundle([])))
    assert audit_bundle.main(["verify", str(f), "--json"]) == 1   # an empty bundle proves nothing
