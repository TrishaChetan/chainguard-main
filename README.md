# 🛡️ ChainGuard

> **Trust, delegation and audit for autonomous AI agents — anchored on Ethereum.**

![ci](https://github.com/TrishaChetan/chainguard-main/actions/workflows/ci.yml/badge.svg)

AI agents are starting to act on their own: placing orders, moving money, delegating work to other agents. That raises a simple but hard question — *should this agent be allowed to do this, right now?*

ChainGuard answers it. Every agent operation passes through a pipeline that checks **who** the agent is, **what** it has been granted, **where** that authority came from, and **whether** its behaviour looks normal. The result is one of three verdicts:

| Verdict | Meaning |
|:---:|---|
| ✅ **ALLOW** | Valid identity, valid authority, normal behaviour |
| ⚠️ **REVIEW** | Authority is valid, but the behaviour is unusual |
| ⛔ **BLOCK** | Missing, forged, escalated or revoked authority |

Every decision is written to a tamper-evident audit log whose Merkle roots are committed to an Ethereum-compatible smart contract, so anyone can later prove a record was not altered.

> ⚠️ ChainGuard is a research & engineering prototype. It runs fully on a disposable local Hardhat chain and is not a production identity provider.

---

## ✨ Highlights

- 🔏 **Signed requests** — EIP-191 signatures bind actor, operation, payload digest, nonce and expiry. Claiming to be `ROOT_AUTHORIZER` is not enough; you must prove it.
- 🔗 **Scoped delegation** — multi-hop delegation chains with scope and time limits, cycle rejection and full provenance tracing.
- ✂️ **Cascading revocation** — revoke an agent and every grant derived from it falls away, while independent valid paths survive.
- 🤖 **Drift detection** — an Isolation Forest model flags agents that behave oddly even inside their nominal permissions.
- 🧾 **Merkle-batched audit** — audit records are batched off-chain and their roots anchored on-chain, with per-record inclusion proofs.
- 📊 **Live dashboard** — nine React pages including a trust graph, live governance stream, threat lab and tamper-simulation viewer.

## 🧠 How it works

```text
          ┌──────────────┐
 request ─▶  Signature   │  who is calling?
          │  & nonce     │
          └──────┬───────┘
                 ▼
          ┌──────────────┐
          │  Delegation  │  where did this authority come from?
          │   tracing    │
          └──────┬───────┘
                 ▼
          ┌──────────────┐
          │  Revocation  │  is any link in the chain revoked?
          └──────┬───────┘
                 ▼
          ┌──────────────┐
          │    Drift     │  does this look like normal behaviour?
          │  detection   │
          └──────┬───────┘
                 ▼
          ┌──────────────┐        ┌────────────────────┐
          │  Governance  ├──────▶ │ Audit log → Merkle  │──▶ AgentTrustRegistry.sol
          │   verdict    │        │ batch → on-chain   │
          └──────────────┘        └────────────────────┘
               ALLOW / REVIEW / BLOCK
```

## 🧰 Tech stack

| Layer | Tools |
|---|---|
| Backend | Python 3.14, FastAPI, Uvicorn, Pydantic |
| Blockchain | Solidity 0.8.28, Hardhat, Web3.py, ethers |
| ML | scikit-learn (Isolation Forest), NumPy, joblib |
| Frontend | React 19, Vite, Tailwind CSS, React Flow, Recharts, Framer Motion |
| Testing | unittest, Node test runner, Playwright |
| Storage | JSON / JSONL files — no database required |

## 🚀 Getting started

**Prerequisites:** Git, Python 3.14, Node.js + npm.

### 1. Install

```bash
git clone https://github.com/TrishaChetan/chainguard-main.git
cd chainguard-main

python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

(cd blockchain && npm ci)
(cd frontend && npm ci)
```

> You do **not** need a `.env` file for the local demo. `.env.example` is only for Sepolia.

### 2. Run — one command

```bash
python run_local.py --open
```

This compiles the contracts on first run, starts the Hardhat chain, starts the backend (which deploys the registry and seeds the demo), starts the dashboard, and opens http://localhost:5173. Each step waits for the previous one to be healthy, and **Ctrl+C stops everything**. Use `--no-ui` for chain + backend only.

### 2b. Or run each part yourself (three terminals)

**① Local blockchain**
```bash
cd blockchain
export CHAINGUARD_NETWORK=hardhat        # PowerShell: $env:CHAINGUARD_NETWORK="hardhat"
npm run compile
npm run node
```

**② Backend + demo seeding**
```bash
export CHAINGUARD_NETWORK=hardhat
export CHAINGUARD_RPC_URL=http://127.0.0.1:8545
python -m backend.api.demo_server
```
This deploys a fresh registry, registers demo agents, evaluates five scenarios and anchors their audit batch. Wait for `Uvicorn running on http://127.0.0.1:8001`.

**③ Frontend**
```bash
cd frontend
export VITE_API_BASE_URL=http://127.0.0.1:8001
npm run dev
```

Open **http://localhost:5173** 🎉

### 3. Check it's healthy

```bash
curl http://127.0.0.1:8001/api/health
```
Look for `"status": "ONLINE"`, `"chain_id": 31337` and `"contract_available": true`.

## 🎬 Demo scenarios

| Scenario | Verdict | What's happening |
|---|:---:|---|
| `normal` | ✅ ALLOW | Legitimate delegated action |
| `self_escalation` | ⛔ BLOCK | Agent claims authority it never received |
| `unauthorized_delegation` | ⛔ BLOCK | Delegator didn't hold the authority it passed on |
| `scope_creep` | ⚠️ REVIEW | Valid permissions, suspicious behaviour |
| `post_decommission` | ⛔ BLOCK | Agent acts after being revoked |

Run them from the **Live Governance** page (try *Auto Demo*), then head to **Blockchain Audit** and hit **Simulate Tampering** to watch a modified record fail its Merkle proof.

## 🗺️ Dashboard pages

`Dashboard` · `Agents` · `Trust Graph` · `Live Governance` · `Threat Lab` · `ML Analytics` · `Revocation` · `Evaluation` · `Blockchain Audit`

## 🧪 Tests

Every push runs on GitHub Actions (`.github/workflows/ci.yml`): the isolated audit-log / Merkle batching tests, a frontend production build, and a chain job that compiles the contracts, starts a local Hardhat node, and runs the contract tests plus the full blockchain module test suite (including on-chain anchoring).

```bash
# Python regression (needs a running Hardhat node on 8545)
python -m backend.testing.run_regression

# Smart contracts
cd blockchain && npm run test:contract

# Frontend build + browser tests
cd frontend && npm run build
npx playwright test --config playwright.local-qa.config.js
```

## 📁 Project layout

```text
chainguard-main/
├── backend/
│   ├── api/            FastAPI app, demo server, schemas
│   ├── core/
│   │   ├── authorization/   signed requests & grants
│   │   ├── blockchain/      RPC, hashing, Merkle batching
│   │   ├── tracing/         delegation provenance
│   │   ├── drift_detection/ Isolation Forest model
│   │   ├── revocation/      cascading revocation
│   │   └── governance/      final verdict engine
│   ├── research/       experiment scripts
│   └── testing/        signed test fixtures
├── blockchain/
│   ├── contracts/      AgentTrustRegistry.sol, GrantAuthority.sol
│   ├── scripts/        node & deploy helpers
│   └── test/
├── frontend/           React + Vite dashboard
└── docs/               handbook, architecture, research & QA reports
```

## 🌐 Sepolia

A reference deployment of `AgentTrustRegistry` lives on Ethereum Sepolia at
[`0x61c74D0525f4f5A1a7b20f2eA561e485E71dd243`](https://sepolia.etherscan.io/address/0x61c74D0525f4f5A1a7b20f2eA561e485E71dd243).
Read-only verification needs only `CHAINGUARD_NETWORK=sepolia` and a `SEPOLIA_RPC_URL` — no private key. See [`docs/research-evaluation/REPRODUCTION_GUIDE.md`](docs/research-evaluation/REPRODUCTION_GUIDE.md).

## 🔬 Research results (summary)

- **Audit integrity:** blockchain-anchored Merkle batches and hash-chain logs both detected all 60 tested tampering transformations; the blockchain variant adds an independently queryable commitment at the cost of extra latency/gas.
- **Revocation:** the corrected cascade removed all 60 wrongly retained downstream permissions and preserved 40/40 independent paths.
- **Security:** across 180 controlled operations — 30 legitimate allowed, 140 attacks blocked, 10 anomalies sent to review.

Full write-ups: [`docs/research-evaluation/`](docs/research-evaluation/) · Deep dive: [`docs/COMPLETE_PROJECT_EXPLANATION.md`](docs/COMPLETE_PROJECT_EXPLANATION.md)

## ⚠️ Limitations

- Local Hardhat wallets are unlocked — fine for testing, not for production key custody.
- The ML model is trained on synthetic data; false positives/negatives are possible.
- On-chain integrity proves a record wasn't changed, not that the original event was truthful.
- Tested on a single machine with a local filesystem, not distributed deployments.

## 🔐 Security notes

- Never commit `.env` or private keys (they're git-ignored).
- Never put secrets in `VITE_*` variables — they're visible in the browser.
- Don't reuse Hardhat development accounts on public networks.
