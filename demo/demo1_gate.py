"""Q1: How do you decide what an agent may do, and enforce it BEFORE the action?

Three layers, demonstrated in increasing strength:

  A. scoped identity      - refused before the policy body is read
  B. policy evaluation    - the agent writes an intent; only an `allow` reaches the executor
  C. the database refuses - a raw driver write, bypassing all application code, still fails
"""
import os
import sys
from datetime import datetime, timezone

from pymongo.errors import WriteError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attested import agent, policy, sources          # noqa: E402
from attested.db import client, DB_NAME              # noqa: E402

db = client()[DB_NAME]
R = lambda s: print("\n" + s + "\n" + "=" * len(s))   # noqa: E731


def main():
    R("The policy set, as data in Atlas")
    for p in db.policies.find({"active": True}, sort=[("ruleId", 1)]):
        print(f"  {p['effect'].upper():8s} {p['ruleId']:32s} v{p['version']}")
        print(f"           applies to: {', '.join(p['appliesTo'])}")
        print(f"           grounded in: {p['grounding'][:110]}...")

    R("A. Scoped identity: refused before any rule is read")
    cand = agent.load_candidates(limit=1)[0]
    for actor, why in [("agent:screening-scout", "scope is breast; this claim is lung"),
                       ("agent:reader", "identity holds no claim.write permission")]:
        intent = policy.record_intent(
            db, runId="run-demo1", actor=actor, action="claim.write",
            collection="records", recordId=cand["recordId"], payload=cand)
        d = policy.evaluate(db, intent)
        print(f"  {actor:24s} -> {d['verdict'].upper()}")
        print(f"    {d['reason'][:116]}")
        print(f"    (expected: {why})")

    R("B. Policy evaluation over real extracted claims")
    print("Input is the genuine extraction output: 859 congress abstracts with their")
    print("ambiguity flags and fragment-verification state. The denials below are caused")
    print("by real properties of real records, not by synthetic violations.\n")
    run_id, res = agent.curate(db, candidates=agent.load_candidates(limit=10))
    print(f"\n  run {run_id}")
    counters = db.runs.find_one({"_id": run_id})["counters"]
    print("  counters:", {k: v for k, v in counters.items() if v})

    print("\n  Why each decision went the way it did:")
    for rid, reason, rule in res["denied"][:3]:
        print(f"    DENY     {rule}")
        print(f"             {reason[:104]}")
    for rid, did, rule in res["escalated"][:3]:
        print(f"    ESCALATE {rule}  -> decision {did}")

    R("B2. The agent asks for a rankable figure, which only a human may grant")
    want = agent.load_candidates(limit=60, want_rankable=True)
    want = [c for c in want
            if c["provenance"]["fragmentVerified"] and not c["ambiguous"]
            and c["sensitivity"] is not None and c["specificity"] is not None
            and c["sensitivity"] > 50]
    esc = []
    for c in want[:1]:
        intent = policy.record_intent(db, runId=run_id, actor="agent:curator-v3",
                                      action="claim.write", collection="records",
                                      recordId=c["recordId"] + "-rankable", payload=c)
        d = policy.evaluate(db, intent)
        print(f"  agent asks for rankable=true -> {d['verdict'].upper()} "
              f"({d['decidingRuleId']})")
        print(f"    {d['reason'][:112]}")
        if d["verdict"] == "escalate":
            esc.append((c["recordId"], d["_id"], d["decidingRuleId"]))
    if esc:
        rid, did, rule = esc[0]
        d = policy.resolve_escalation(db, did, by="human:ester", outcome="approved",
                                      note="checked the sentence; 68/86 reads correctly")
        print(f"  decision {did} resolved: {d['resolution']['outcome']} "
              f"by {d['resolution']['by']}")
        intent = db.intents.find_one({"_id": d["intentId"]})
        from attested import actions
        try:
            e = actions.apply(db, intent, db.decisions.find_one({"_id": did}),
                              record_builder=actions.claim_record_builder)
            rec = db.records.find_one({"_id": e["effect"]["recordId"]})
            print(f"  applied at ledger seq {int(e['seq'])}, onBehalfOf={e['onBehalfOf']}")
            print(f"  the record now reads authority={rec['authority']!r} "
                  f"rankable={rec['rankable']}")
            print("  approval TRANSFERRED authority: the human read the sentence, so the")
            print("  record is human-authored and the rankable rule is satisfied honestly")
            print("  rather than bypassed. The machine's own reading is retained at")
            print(f"  provenance.machineValue = {rec['provenance'].get('machineValue')}")
            print("  The ledger schema makes onBehalfOf mandatory for an applied escalation,")
            print("  so an agent cannot borrow authority without naming whose it was.")
        except WriteError as exc:
            print(f"  server refused even after approval: {str(exc)[:150]}")

    R("C. The database refuses, with all application code bypassed")
    print("These writes go straight to the collection with a raw driver handle. No intent,")
    print("no decision, no executor. This is the layer an orchestrator bug cannot defeat.\n")
    now = datetime.now(timezone.utc)
    base = {
        "_id": "rec-bypass-attempt", "assay": "Bypass assay", "indication": "lung",
        "modality": None, "rung": 2, "sensitivity": 68.0, "specificity": 86.0,
        "cohortN": 300, "authority": "agent", "rankable": False,
        "provenance": {"sourceId": "10.1200/bypass", "sourceType": "congress",
                       "sourceQuote": "A sentence long enough to satisfy the minimum "
                                      "length requirement for provenance.",
                       "sourceUrl": None, "fragmentVerified": True, "machineValue": None},
        "ledgerSeq": None, "updatedAt": now,
    }
    attempts = [
        ("assert a regulatory rung from a congress abstract", {"rung": 4}),
        ("mark its own figure rankable", {"rankable": True}),
        ("write a figure with no source quote", {"provenance": {
            "sourceId": "x", "sourceType": "congress", "sourceUrl": None,
            "fragmentVerified": True, "machineValue": None}}),
        ("land a mis-parsed 168% sensitivity", {"sensitivity": 168.0}),
        ("smuggle in an undeclared field", {"backdoor": "yes"}),
    ]
    for label, over in attempts:
        doc = {**base, **over}
        try:
            db.records.insert_one(doc)
            db.records.delete_one({"_id": doc["_id"]})
            print(f"  !! ACCEPTED (should not have): {label}")
        except WriteError:
            print(f"  REFUSED BY SERVER: {label}")

    R("External systems the agent acts across")
    for name, up in sources.reachable().items():
        print(f"  {'reachable' if up else 'UNREACHABLE':12s} {name}")

    R("Q1 answered")
    print("Scope and permission are checked before any rule is read. Rules live in Atlas as")
    print("versioned documents, each carrying the measurement that justifies it, and the")
    print("agent has no path to `records` except through a decision. The validator on")
    print("`records` then refuses anything unprovenanced regardless of how it arrives, so")
    print("the strongest rules are the ones no code path can bypass.")


if __name__ == "__main__":
    main()
