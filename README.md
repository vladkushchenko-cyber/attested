# Attested

**Hackathon problem statement 1: Agents You Can Audit.**

An evidence-curation agent for Medical Affairs. MongoDB is the identity, policy and audit
layer. The agent takes real actions across real external systems, every action is tied to an
identity and checked against policy before it runs, and the whole history is reconstructible
afterwards.

Built and verified against **MongoDB 9.0.8** as a single-node replica set, so
`$jsonSchema` validation, change streams and multi-document transactions are all genuinely
exercised. Point `MONGODB_URI` at Atlas and nothing else changes.

---

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# a replica set is required: change streams and transactions both need one
mongod --dbpath ./data --bind_ip 127.0.0.1 --port 27019 --replSet rs0 &
python -c "from pymongo import MongoClient; \
  MongoClient('mongodb://127.0.0.1:27019/?directConnection=true').admin.command( \
  'replSetInitiate', {'_id':'rs0','members':[{'_id':0,'host':'127.0.0.1:27019'}]})"

python -m attested.bootstrap --drop     # collections, validators, indexes, seed policy
python tests/test_validators.py         # 23 cases: prove the server refuses
python demo/demo1_gate.py               # Q1: enforcement before the action
python demo/demo2_audit.py              # Q2: reconstruction and chain integrity
python demo/demo3_halt.py               # Q3: halt in flight, then compensating revert
python demo/demo4_crosssystem.py        # real actions across real systems
```

Against Atlas:

```bash
export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
mongosh "$MONGODB_URI" attested atlas/provision.js   # same validators, generated from code
python demo/demo1_gate.py
```

---

## Why the governance is not hypothetical

Every policy rule exists because a measurement made it necessary. Measured on 859 real
congress abstracts pulled from Crossref, plus live openFDA and Health Canada records:

| Measurement | Value | The rule it forces |
|---|---|---|
| Machine-read sensitivity/specificity accuracy | **50%** (hand audit of 20 pairs: 10 correct, 7 wrong, 3 should have been refused) | `require_human_for_rankable` |
| Regulatory vocabulary in abstracts that is false positive | **~85%** ("FDA-approved" describes the therapy, not the assay) | `deny_rung4_from_literature` |
| Records whose quoted fragment is not verifiable in the retained text | **94 of 859** | `reject_unverifiable_fragment` |
| Records with more than one candidate figure | **75 of 859** | `escalate_ambiguous_claim` |
| openFDA supplements that are administrative, not approvals | **12 of 25** | `deny_administrative_as_approval` |

The demo's denials are therefore caused by genuine properties of genuine records, not by
synthetic violations planted to make a gate look busy.

---

## Q1. Enforcement before the action

Three layers, each stronger than the one above.

**Scoped identity.** `identities` holds agent and human principals with `allowedActions`
and a `scope`. `agent:screening-scout` is scoped to breast and is refused a lung claim
before any rule is read. `agent:reader` holds no write permission at all.

**Policy evaluation.** The agent has no write path to `records`. It writes an **intent**;
the engine evaluates it against versioned documents in `policies` and writes a **decision**
naming the rule that fired and every rule it considered. Only an `allow`, or an `escalate`
with an approved human resolution, reaches the executor.

**The database refuses.** `records` carries a `$jsonSchema` validator with
`validationAction: "error"`. These are refused by the server even with a raw driver handle
and no application code in the path:

```
REFUSED BY SERVER: assert a regulatory rung from a congress abstract
REFUSED BY SERVER: mark its own figure rankable
REFUSED BY SERVER: write a figure with no source quote
REFUSED BY SERVER: land a mis-parsed 168% sensitivity
REFUSED BY SERVER: smuggle in an undeclared field
```

That is the distinction worth the most: the first two layers are rules the agent is asked to
respect, and the third is one it cannot get around. `tests/test_validators.py` covers 23
such cases, including the two conditional rules expressed as `anyOf` branches (a rung of 4
or 5 requires a regulator or HTA source; a rankable figure requires a human author and a
verified fragment).

**Approval transfers authority.** When a human approves an escalated claim, the record is
written with `authority: "human"` and the machine's original reading is retained at
`provenance.machineValue`. The rankable rule is then satisfied honestly rather than
bypassed, and the correction is visible rather than overwritten.

## Q2. What, why, and under whose authority

`ledger` is append-only and hash-chained. Each entry hashes its own canonical content
together with the previous entry's hash, so an edit, reorder or deletion breaks the chain at
a identifiable point.

Entries record `actor`, `onBehalfOf`, `action`, `intentId`, `decisionId`, `policyId` and
version, `reason`, the verbatim source sentence, and `effect.before` / `effect.after`. The
schema makes `onBehalfOf` **mandatory** for any applied action whose verdict was
`escalate`, so an agent cannot borrow a human's authority without naming whose it was.

One query answers the reviewer's question:

```
seq 2  claim.write  allow     actor: agent:curator-v3
                              sensitivity: None -> 80.0   specificity: None -> 75.0
seq 8  claim.write  escalate  actor: agent:curator-v3 (under authority of human:ester)
                              rule: require_human_for_rankable@v1
                              specificity: 75.0 -> 86.0   authority: 'agent' -> 'human'
```

Denials are ledgered too. An audit that records only what happened cannot show what was
attempted and refused, which is usually the question being asked.

Tampering is caught twice: `verify_chain()` reports the break after the fact, and a
**change stream** on `ledger` catches the modification as it happens and appends a `tamper`
entry, because the application never issues an update or delete against that collection.

## Q3. Stop and roll back mid-run

**Stop** is cooperative and change-stream driven. `HaltWatcher` tails `controls` filtered to
one run and sets an in-process event; the agent checks it between steps. A halt issued from
another process lands in flight:

```
[3] APPLIED   rec-10-1200_jco-2023-41-16_suppl-e21104 at ledger seq 6
>> human:compliance issues a halt from outside the agent process
[4] HALTED in flight: pausing the batch: policy review of the rankable rule
   stopped after 3 of 8 candidates
```

The process is never killed, so no record is left half-written and the halt is itself an
audited action with an identity attached. A `SIGKILL` leaves no ledger entry explaining why
a run ended.

**Rollback is compensation, not deletion.** Every applied effect recorded its prior value,
so `revert()` walks the run backwards and writes a new entry undoing each one, naming what
it reversed:

```
seq 8   revert.records   compensates led-00000006   by human:compliance
seq 9   revert.records   compensates led-00000004   by human:compliance
seq 10  revert.records   compensates led-00000002   by human:compliance
chain after revert: {'ok': True, 'entries': 12}
```

Downstream effects already dispatched are marked `compensated` rather than claimed as
undone, because the outside world was not rewound.

## Real actions across real systems

All four verified reachable at build time, via `sources.reachable()`:

| System | Role | `sourceType` |
|---|---|---|
| openFDA `device/pma` | FDA approvals and supplements | `regulator` — may assert rung 4 |
| Health Canada MDALL | Canadian device licences | `regulator` |
| Crossref | congress abstracts | `congress` — rung 4 refused |
| ClinicalTrials.gov v2 | trial records | `trial` |

`demo4` shows the contrast directly: the same rung-4 assertion is **allowed** from openFDA
and **denied** from a Crossref abstract, with the grounding measurement printed alongside
the refusal.

The `outbox` collection separates `sent` from `confirmed`. A downstream 2xx means a request
was accepted; only an independent read-back is evidence the effect took hold. Both
transitions are ledgered.

## Collections

| Collection | Role | Mechanism |
|---|---|---|
| `identities` | principals, scopes, permitted actions | `$jsonSchema`, unique index |
| `policies` | versioned rules with predicate, effect and grounding | `$jsonSchema` |
| `intents` | what the agent wants to do, before it may | `$jsonSchema` |
| `decisions` | the verdict and every rule considered | `$jsonSchema` |
| `ledger` | append-only hash-chained audit | `$jsonSchema` + change stream |
| `records` | the record of truth | **`$jsonSchema` as the gate** |
| `controls` | per-run kill switch | **change stream** |
| `runs` | run lifecycle and counters | `$jsonSchema` |
| `outbox` | downstream effects, sent vs confirmed | `$jsonSchema` |

## Implementation notes

- **Transactions.** A record write and its ledger entry commit together, so there is no
  window where state changed but the audit did not. This is why the deployment is a replica
  set rather than a standalone server.
- **Predicates are names, not code.** A policy document references a predicate by name,
  resolved in `policy.py`. A policy can therefore be reviewed by someone who is not reading
  Python, and cannot smuggle executable logic into the database.
- **The payload cannot retarget a write.** `apply()` forces `_id` to the intent's target,
  so a payload cannot redirect an approved write to a different record.
- **Hashing is precision-stable.** BSON stores datetimes at millisecond precision, so
  timestamps are truncated before hashing and nested datetimes inside `effect` are
  normalised recursively. Without that, every entry carrying an effect fails verification.
- **Transport.** `sources.py` shells out to `curl` rather than using `urllib`, because in
  this environment Python's TLS store does not trust the intercepting proxy's root CA while
  `curl` reads the system keychain. Verification is not disabled.
