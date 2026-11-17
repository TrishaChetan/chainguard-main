"""The standalone verify_proof.py must accept genuine proofs and reject edits."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import verify_proof  # noqa: E402
from backend.core.blockchain.audit_log import hash_record  # noqa: E402
from backend.core.blockchain.merkle import build_merkle_tree, generate_merkle_proof  # noqa: E402

RECORDS = [{"action_id": f"act-{i}", "agent_id": f"agent-{i % 3}", "operation": "transfer",
            "verdict": ["ALLOW", "REVIEW", "BLOCK"][i % 3], "timestamp": f"2026-11-17T10:00:0{i}Z"}
           for i in range(5)]  # odd count exercises the duplicated last node


def api_proof(i, records=RECORDS):
    leaves = [hash_record(r) for r in records]
    root = build_merkle_tree(leaves)["root"]
    return {"action_id": records[i]["action_id"], "record": records[i], "record_hash": leaves[i],
            "proof_siblings": generate_merkle_proof(leaves, i), "batch_id": f"batch:{root}",
            "result": "VERIFIED", "blockchain": {"contract_address": "0x" + "11" * 20}}


@pytest.mark.parametrize("i", range(5))
def test_every_leaf_verifies_offline(i):
    verdict, steps = verify_proof.verify(api_proof(i))
    assert verdict == "UNCHECKED", steps.render()
    assert all(ok is not False for ok, _ in steps.steps)


def test_single_record_batch():
    verdict, _ = verify_proof.verify(api_proof(0, RECORDS[:1]))
    assert verdict == "UNCHECKED"


def test_edited_record_is_rejected():
    proof = api_proof(2)
    edited = dict(proof["record"], verdict="ALLOW")
    assert verify_proof.verify(proof, record=edited)[0] == "TAMPERED"
    proof["record"]["operation"] = "withdraw"
    assert verify_proof.verify(proof)[0] == "TAMPERED"


def test_wrong_proof_is_rejected():
    proof = api_proof(1)
    proof["proof_siblings"][0]["position"] = "left" if proof["proof_siblings"][0]["position"] == "right" else "right"
    assert verify_proof.verify(proof)[0] == "TAMPERED"
    proof = api_proof(1)
    proof["batch_id"] = "batch:0x" + "ab" * 32
    assert verify_proof.verify(proof)[0] == "TAMPERED"
    proof = api_proof(1)
    proof["record"] = RECORDS[3]
    assert verify_proof.verify(proof)[0] == "TAMPERED"


def test_pending_record():
    proof = {"action_id": "act-9", "record": {"action_id": "act-9"}, "proof_siblings": [], "batch_id": None}
    assert verify_proof.verify(proof)[0] == "PENDING"


def test_onchain_root_compared(monkeypatch):
    proof = api_proof(4)
    root = proof["batch_id"].split(":", 1)[1]
    monkeypatch.setattr(verify_proof, "onchain_root", lambda rpc, addr, bid: root)
    assert verify_proof.verify(proof, rpc="http://chain")[0] == "VERIFIED"
    monkeypatch.setattr(verify_proof, "onchain_root", lambda rpc, addr, bid: "0x" + "00" * 32)
    assert verify_proof.verify(proof, rpc="http://chain")[0] == "TAMPERED"
    monkeypatch.setattr(verify_proof, "onchain_root", lambda rpc, addr, bid: None)
    assert verify_proof.verify(proof, rpc="http://chain")[0] == "TAMPERED"

    def down(*a):
        raise ConnectionError("refused")
    monkeypatch.setattr(verify_proof, "onchain_root", down)
    assert verify_proof.verify(proof, rpc="http://chain")[0] == "UNCHECKED"   # outage is not tampering


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "proof.json"
    good.write_text(json.dumps(api_proof(0)))
    assert verify_proof.main([str(good)]) == 0
    assert "Merkle proof valid" in capsys.readouterr().out
    bad = tmp_path / "record.json"
    bad.write_text(json.dumps(dict(RECORDS[0], verdict="BLOCK")))
    assert verify_proof.main([str(good), "--record", str(bad), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "TAMPERED"
    assert verify_proof.main([str(tmp_path / "missing.json")]) == 2
