"""Q2: How do you record what the agent did, why, and under whose authority?

Shows:
  A. one reviewer query reconstructs a record's whole history
  B. the ledger is hash-chained, and verification walks it
  C. an out-of-band edit breaks the chain AND is caught live by a change stream
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attested import agent, control, ledger, policy, actions   # noqa: E402
from attested.db import client, DB_NAME                        # noqa: E402

db = client()[DB_NAME]
R = lambda s: print("\n" + s + "\n" + "=" * len(s))            # noqa: E731


def main():
    R("Setting up: one record with a contested history")
    # 1. agent records a machine-read figure (allowed, not rankable)
    cands = [c for c in agent.load_candidates(limit=60)
             if c["provenance"]["fragmentVerified"] and not c["ambiguous"]
             and c["sensitivity"] is not None and c["specificity"] is not None][:1]
    cand = cands[0]
    run_id, res = agent.curate(db, candidates=cands, verbose=False)
    rec_id = cand["recordId"]
    print(f"  agent wrote {rec_id}")
    print(f"    machine read sensitivity={cand['sensitivity']} "
          f"specificity={cand['specificity']}, marked not rankable")

    # 2. the agent then tries to promote it to rankable: escalated
    promote = dict(cand); promote["rankable"] = True
    intent = policy.record_intent(db, runId=run_id, actor="agent:curator-v3",
                                  action="claim.write", collection="records",
                                  recordId=rec_id, payload=promote)
    d = policy.evaluate(db, intent)
    print(f"  agent asked to promote it to rankable -> {d['verdict']} "
          f"({d['decidingRuleId']})")

    # 3. a human reads the sentence, corrects the specificity, and approves
    policy.resolve_escalation(db, d["_id"], by="human:ester", outcome="approved",
                              note="read the sentence; specificity is 86, not as parsed")
    corrected = dict(promote); corrected["specificity"] = 86.0
    intent2 = policy.record_intent(db, runId=run_id, actor="agent:curator-v3",
                                   action="claim.write", collection="records",
                                   recordId=rec_id, payload=corrected)
    d2 = policy.evaluate(db, intent2)
    policy.resolve_escalation(db, d2["_id"], by="human:ester", outcome="approved",
                              note="corrected specificity to 86 after reading the source")
    actions.apply(db, intent2, db.decisions.find_one({"_id": d2["_id"]}),
                  record_builder=actions.claim_record_builder)
    print("  human:ester corrected specificity to 86.0 and approved")

    R("A. One query reconstructs the record's history")
    print("The reviewer's question is 'how did this come to say what it says, and who let")
    print("it'. That is one query against the ledger, ordered, with the value before each")
    print("change and the authority for it.\n")
    for h in ledger.reconstruct(db, rec_id):
        who = h["actor"]
        if h["onBehalfOf"]:
            who += f"  (under authority of {h['onBehalfOf']})"
        print(f"  seq {h['seq']:<4} {h['action']:<14} {h['verdict']:<9} applied={h['applied']}")
        print(f"            actor: {who}")
        if h["rule"]:
            print(f"            rule : {h['rule']}")
        print(f"            why  : {h['reason'][:96]}")
        if h["before"] or h["after"]:
            b = (h["before"] or {}); a = (h["after"] or {})
            for f in ("sensitivity", "specificity", "rankable", "authority"):
                if b.get(f) != a.get(f):
                    print(f"            {f}: {b.get(f)!r} -> {a.get(f)!r}")
        print()

    rec = db.records.find_one({"_id": rec_id})
    print(f"  current state: sens={rec['sensitivity']} spec={rec['specificity']} "
          f"rankable={rec['rankable']} authority={rec['authority']}")
    print(f"  machine's own reading retained: {rec['provenance']['machineValue']}")

    R("A2. The same question across the whole run")
    counts = {}
    for e in db.ledger.find({"runId": run_id}):
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    print(f"  ledger entries for run {run_id}: {counts}")
    print("  Note that denials are ledgered too. An audit that records only what happened")
    print("  cannot show what was attempted and refused, which is usually the question.")

    R("B. The chain verifies")
    v = ledger.verify_chain(db)
    print(f"  {v}")

    R("C. An out-of-band edit is detected two ways")
    watcher = control.TamperWatcher(db).start()
    time.sleep(0.6)
    target = db.ledger.find_one({"runId": run_id, "applied": True,
                                 "effect": {"$ne": None}}, sort=[("seq", 1)])
    print(f"  editing ledger entry seq {int(target['seq'])} directly, "
          "as a hostile operator would")
    db.ledger.update_one({"_id": target["_id"]},
                         {"$set": {"reason": "nothing to see here"}})
    time.sleep(1.5)
    watcher.stop()

    print(f"\n  1. change stream caught it live: {watcher.events}")
    tamper = db.ledger.find_one({"verdict": "tamper"}, sort=[("seq", -1)])
    if tamper:
        print(f"     a tamper entry was appended at seq {int(tamper['seq'])}:")
        print(f"       {tamper['reason'][:150]}")
    v2 = ledger.verify_chain(db)
    print(f"\n  2. chain verification reports the break: {v2}")

    R("Q2 answered")
    print("Every entry names the actor, and for an applied escalation the schema makes the")
    print("approving human mandatory, so authority cannot be omitted. Entries carry the")
    print("rule and version that decided them, the verbatim source sentence, and the value")
    print("before and after. The chain makes the record evidence rather than a log: an edit")
    print("is detected by verification after the fact and by a change stream as it happens.")


if __name__ == "__main__":
    main()
