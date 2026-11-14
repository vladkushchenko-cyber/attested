# Attested — an evidence-curation agent you can audit

**Hackathon problem statement 1: Agents You Can Audit.**

MongoDB Atlas is the identity, policy and audit layer. The agent curates oncology
diagnostics evidence into a Medical Affairs record of truth, taking real actions across
real external systems.

---

## Why this domain makes the governance problem concrete

This is not a hypothetical. Measured on 859 real congress abstracts pulled from Crossref:

| Measurement | Value | Consequence |
|---|---|---|
| Machine-read sensitivity/specificity accuracy | **50%** (hand audit, 20 pairs: 10 correct, 7 wrong, 3 should have been refused) | An agent cannot be allowed to write a performance figure unchecked |
| Regulatory vocabulary in abstracts that is a false positive | **~85%** ("FDA-approved" describes the therapy, not the assay) | An agent must be structurally barred from asserting a regulatory rung from literature |
| Records whose quoted fragment is not verifiable in the retained source text | **94 of 859** | "Show me the sentence" must be a precondition, not a feature |
| openFDA supplements that are administrative, not approval events | **12 of 25** (facility relocations, fermenter replacements) | An approval feed must refuse paperwork before it alerts anyone |

Every policy rule in this system exists because one of those numbers made it necessary.
That is the difference between a governance demo and a governance system.

---

## The three questions

### Q1. How do you decide what an agent may do, and enforce it *before* the action?

Three layers, each strictly stronger than the one above it.

**Layer 1 — scoped identity.** `identities` holds agent and human principals, each with
`allowedActions` and a `scope` (indication, jurisdiction). An action outside scope is
refused before the policy body is even read.

**Layer 2 — policy evaluation.** The agent cannot act directly. It writes an **intent** to
`intents` describing what it wants to do. The policy engine evaluates the intent against
versioned documents in `policies` and writes a **decision** of `allow`, `deny` or
`escalate`, naming the rule that fired. Only an `allow` decision, carrying the decision id,
can be presented to the action executor.

**Layer 3 — the database refuses.** This is the load-bearing layer. The `records`
collection carries a `$jsonSchema` validator with `validationAction: "error"`. A write that
lacks `provenance.sourceQuote`, lacks `authority`, carries a value outside 0–100, or asserts
`rung >= 4` without `provenance.sourceType` in `{regulator, hta}` **is rejected by the
server**, not by application code.

The distinction matters: layers 1 and 2 are things the agent is asked to respect. Layer 3 is
a thing the agent cannot get around, even with a raw driver connection and a bug in the
orchestrator. `demo1_gate.py` proves this by bypassing the agent entirely and watching the
server refuse the write.

Policy rules, each traceable to a measurement:

| Rule | Effect | Grounded in |
|---|---|---|
| `require_source_quote` | deny | nothing is written without the sentence it came from |
| `reject_unverifiable_fragment` | deny | 94 of 859 fragments not verifiable in retained text |
| `escalate_ambiguous_claim` | escalate | 46 records carry more than one candidate figure |
| `deny_rung4_from_literature` | deny | ~85% false positive rate on regulatory vocabulary |
| `require_human_for_rankable` | escalate | machine accuracy is 50%, so only a human may mark a figure rankable |
| `deny_administrative_as_approval` | deny | 12 of 25 openFDA supplements are facility paperwork |
| `scope_indication` | deny | identity scope, enforced before evaluation |
| `rate_limit_per_run` | deny | a runaway agent is a policy violation, not an incident |

### Q2. How do you record what the agent did, why, and under whose authority?

`ledger` is append-only and **hash-chained**: each entry carries `prevHash` and its own
`hash` over its canonical content. Re-ordering, editing or deleting an entry breaks the
chain, and `verify_chain()` reports exactly where.

Every entry records:

- `actor` — the identity that acted (agent or human)
- `onBehalfOf` — the human principal whose authority was borrowed, where an escalation was
  approved. This is the "under whose authority" answer, and the schema makes it mandatory
  for any action whose decision was `escalate -> approved`.
- `action`, `intentId`, `decisionId`, `policyId`, `policyVersion`, `reason`
- `inputs` — source document id and the verbatim quote
- `effect` — `before` and `after`, which is what makes rollback possible
- `runId`, `ts`, `seq`

Append-only is enforced two ways: the schema requires the chain fields, and a change-stream
watchdog on `ledger` raises on any `update` or `delete` operation and writes a tamper entry.

A reviewer reconstructs a figure with one query: `reconstruct(recordId)` walks the ledger
and returns the ordered chain of who did what, why, under whose authority, and what the
value was before each step.

### Q3. How do you stop or roll back an agent mid-run?

**Stop.** `controls` holds one document per run. The executor opens a **change stream** on
`controls` and sets an in-process event the moment a `halt` command lands. The agent checks
that event between steps, so a long run stops in flight without the process being killed and
without leaving a half-written record. The halt is itself ledgered, with the identity that
issued it.

**Roll back.** Not a delete. Every `effect` carries `before`, so `revert(runId)` walks the
ledger backwards and emits a **compensating action** for each applied effect. Each
compensation is itself policy-gated and ledgered, so the revert is as auditable as the run.
The ledger therefore contains both the original action and its compensation, which is what
an auditor needs: the history of what was believed, not a tidied-up end state.

---

## Collections

| Collection | Role | Mechanism exercised |
|---|---|---|
| `identities` | agent and human principals, scopes, allowed actions | `$jsonSchema`, unique index |
| `policies` | versioned rules with predicate and effect | `$jsonSchema` |
| `intents` | what the agent wants to do, written before it acts | `$jsonSchema` |
| `decisions` | the verdict, naming the rule that fired | `$jsonSchema` |
| `ledger` | append-only hash-chained audit | `$jsonSchema` + change-stream watchdog |
| `records` | the record of truth | **`$jsonSchema` as the unbypassable gate** |
| `controls` | per-run kill switch | **change stream** |
| `runs` | run lifecycle and counters | `$jsonSchema` |
| `outbox` | downstream effects, with sent/confirmed state | `$jsonSchema` |

## Real actions across real systems

Reads, all verified live in this environment:

- Crossref REST — congress abstracts (ASCO, AACR), 859 records already pulled
- openFDA `device/pma` — FDA approvals and supplements
- Health Canada MDALL — Canadian device licences
- ClinicalTrials.gov API v2 — trial records

Writes:

- `records` in Atlas — the record of truth, gated
- `outbox` — a downstream curated-dataset publication and an analyst alert, each with
  `sent` and `confirmed` state so an effect that was only *sent* is distinguishable from one
  that *took effect*

## Running it

    python -m attested.bootstrap          # collections, validators, indexes, seed policy
    python demo/demo1_gate.py             # Q1: pre-action enforcement, incl. bypass attempt
    python demo/demo2_audit.py            # Q2: reconstruct and verify the chain
    python demo/demo3_halt.py             # Q3: halt mid-run via change stream, then revert

Against Atlas: set `MONGODB_URI` to the cluster connection string. Nothing else changes;
the local deployment is a single-node replica set so change streams and transactions behave
identically.
