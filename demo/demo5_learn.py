"""How the agent learns from its mistakes, and how we prove it learned.

The honest version of this demonstration needs ground truth, and we have some: 20 records
whose machine-read sensitivity/specificity pairs were hand-checked against their source
sentences. Ten were right, seven were wrong, three should have been refused.

Those 20 are split deterministically into TRAIN and TEST. The agent learns only from TRAIN.
It is then measured on TEST, which it has never seen. Without that split the demonstration
would only show memorisation.

The measurement reports both sides of the trade. Escalating a claim costs a human's time,
so a system that learns to escalate everything has not improved; it has just stopped
working. So we count wrong figures prevented AND correct figures needlessly escalated.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attested import actions, agent, learn, ledger, policy       # noqa: E402
from attested.db import client, DB_NAME                          # noqa: E402

db = client()[DB_NAME]
R = lambda s: print("\n" + s + "\n" + "=" * len(s))              # noqa: E731
CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "corpus.json")


def audited_records():
    """The 20 records with hand-checked ground truth, split train/test."""
    data = json.load(open(CORPUS))
    rows = []
    for e in data["evidence"]:
        if not e.get("claimAudit"):
            continue
        truth = e.get("claims") or {}
        machine = e.get("claimMachine") or truth        # 'correct' rows were never changed
        quote = " ".join((e.get("quote") or "").split())
        if len(quote) < 25:
            continue
        rows.append({
            "id": e["id"],
            "recordId": "rec-" + e["id"].replace("/", "_").replace(".", "-")[:56],
            "audit": e["claimAudit"],
            "machine": {"sens": machine.get("sens"), "spec": machine.get("spec")},
            "truth": {"sens": truth.get("sens"), "spec": truth.get("spec")},
            "quote": quote,
            "url": e.get("url"),
            "fragmentVerified": not bool(e.get("perfOutsideQuote")),
        })
    rows.sort(key=lambda r: hashlib.md5(r["id"].encode()).hexdigest())
    half = len(rows) // 2
    return rows[:half], rows[half:]


def payload_for(r, rankable=False):
    return {
        "recordId": r["recordId"], "assay": "(assay named in source)",
        "indication": "lung", "jurisdiction": "US", "modality": None,
        "rung": 2,
        "sensitivity": r["machine"]["sens"], "specificity": r["machine"]["spec"],
        "cohortN": None, "authority": "agent", "rankable": rankable,
        "ambiguous": False,
        "provenance": {
            "sourceId": r["id"], "sourceType": "congress",
            "sourceQuote": r["quote"][:600], "sourceUrl": r["url"],
            "fragmentVerified": r["fragmentVerified"],
            "machineValue": {"sens": r["machine"]["sens"],
                             "spec": r["machine"]["spec"]},
        },
    }


def is_wrong(r):
    return r["audit"] in ("wrong", "refuse")


def main():
    train, test = audited_records()
    R("The labelled set")
    print(f"  {len(train) + len(test)} records with hand-checked ground truth")
    print(f"    TRAIN {len(train):2d}  the agent learns from these")
    print(f"    TEST  {len(test):2d}  held out; the agent never sees them while learning")
    for name, rows in (("TRAIN", train), ("TEST", test)):
        w = sum(1 for r in rows if is_wrong(r))
        print(f"    {name}: {w} of {len(rows)} machine readings are wrong")

    # ---------------------------------------------------------------- phase 1
    R("Phase 1. The agent proposes; humans correct it. This fills the ledger.")
    run1 = agent.start_run(db, "agent:curator-v3", note="training batch")
    corrected_count = 0
    for r in train:
        intent = policy.record_intent(db, runId=run1, actor="agent:curator-v3",
                                      action="claim.write", collection="records",
                                      recordId=r["recordId"], payload=payload_for(r))
        d = policy.evaluate(db, intent)
        if d["verdict"] == "deny":
            print(f"  {r['recordId'][:42]:44s} DENY ({d['decidingRuleId']})")
            continue
        if d["verdict"] == "escalate":
            policy.resolve_escalation(db, d["_id"], by="human:ester", outcome="approved",
                                      note="read the sentence")
            d = db.decisions.find_one({"_id": d["_id"]})

        # the human writes the audited truth, not the machine's reading
        corrected = payload_for(r)
        corrected["sensitivity"] = r["truth"]["sens"]
        corrected["specificity"] = r["truth"]["spec"]
        i2 = policy.record_intent(db, runId=run1, actor="agent:curator-v3",
                                  action="claim.write", collection="records",
                                  recordId=r["recordId"], payload=corrected)
        d2 = policy.evaluate(db, i2)
        if d2["verdict"] == "escalate":
            policy.resolve_escalation(db, d2["_id"], by="human:ester",
                                      outcome="approved", note="corrected at source")
            d2 = db.decisions.find_one({"_id": d2["_id"]})
        if d2["verdict"] in ("allow", "escalate"):
            try:
                actions.apply(db, i2, d2, record_builder=actions.claim_record_builder)
                if is_wrong(r):
                    corrected_count += 1
                    print(f"  {r['recordId'][:42]:44s} CORRECTED "
                          f"{r['machine']['sens']}/{r['machine']['spec']} -> "
                          f"{r['truth']['sens']}/{r['truth']['spec']}")
            except Exception as exc:
                print(f"  {r['recordId'][:42]:44s} refused: {str(exc)[:60]}")
    print(f"\n  {corrected_count} corrections now sit in the audit ledger, each attributable")
    print("  to the human who made it. That is the training set: nothing extra was")
    print("  collected, and every example is already provenanced and tamper-evident.")

    # ---------------------------------------------------------------- phase 2
    R("Phase 2. The agent reads its own audit trail and calibrates")
    corrections = learn.mine_corrections(db, run_id=run1)
    changed = [c for c in corrections if c["changed"]]
    print(f"  mined {len(corrections)} applied writes, of which {len(changed)} were changed "
          "by a human\n")
    modes = {}
    for c in changed:
        modes[c["failureMode"]] = modes.get(c["failureMode"], 0) + 1
    print("  failure modes it diagnosed in itself:")
    for m, n in sorted(modes.items(), key=lambda kv: -kv[1]):
        print(f"    {n:2d}x {m}")
        print(f"        {learn.FAILURE_MODES.get(m, '')[:92]}")

    cal = learn.build_calibration(db, corrections)
    print("\n  observed accuracy per source-sentence shape, worst first:")
    for c in cal[:6]:
        print(f"    {int(c['accuracy']*100):3d}%  ({c['observed'] - c['corrected']}/"
              f"{c['observed']})  {c['signature'][:88]}")

    # ---------------------------------------------------------------- phase 3
    R("Phase 3. It proposes tightened rules. It cannot enact them.")
    proposals = learn.propose_rules(db, actor="agent:curator-v3", run_id=run1,
                                    min_observed=2, max_accuracy=0.5)
    ok = [p for p in proposals if p.get("policyId")]
    print(f"  {len(ok)} rules proposed, all written INACTIVE:")
    for p in ok:
        print(f"    {p['ruleId'][:66]}")
        print(f"      from {p['observed']} observations, {int(p['accuracy']*100)}% accurate")

    print("\n  The ratchet: the agent tries to propose a rule that would let it do MORE.")
    bad = policy.record_intent(
        db, runId=run1, actor="agent:curator-v3", action="policy.propose",
        collection="policies", recordId="pol:self_serving:1",
        payload={"ruleId": "self_serving", "version": 1, "effect": "allow",
                 "predicate": "always", "reason": "let me through",
                 "grounding": "because I would like to",
                 "indication": "lung", "jurisdiction": "US"})
    dbad = policy.evaluate(db, bad)
    print(f"    verdict: {dbad['verdict'].upper()}  rule: {dbad['decidingRuleId']}")
    print(f"    {dbad['reason']}")
    print("    The policies validator refuses a loosening proposal as well, so the")
    print("    constraint survives that policy rule being deleted.")

    print("\n  Self-activation is refused too:")
    if ok:
        try:
            learn.activate_rule(db, ok[0]["policyId"], by="agent:curator-v3", run_id=run1)
            print("    !! the agent activated its own rule, which should be impossible")
        except PermissionError as e:
            print(f"    PermissionError: {e}")

        human_rule = learn.activate_rule(
            db, ok[0]["policyId"], by="human:ester", run_id=run1,
            note="reviewed the corrections behind this shape")
        print(f"\n  human:ester activated {human_rule['ruleId']}")
        print(f"    active={human_rule['active']} approvedBy={human_rule['approvedBy']}")
        for p in ok[1:]:
            learn.activate_rule(db, p["policyId"], by="human:ester", run_id=run1,
                                note="batch approved with the first")

    # ---------------------------------------------------------------- phase 4
    R("Phase 4. Measured on the held-out TEST set, with learning off then on")

    def evaluate_test(label, learned_active):
        db.policies.update_many({"proposedBy": {"$ne": None}},
                                {"$set": {"active": bool(learned_active)}})
        run = agent.start_run(db, "agent:curator-v3", note=f"evaluation: {label}")
        stats = {"asserted_wrong": 0, "asserted_right": 0,
                 "escalated_wrong": 0, "escalated_right": 0, "denied": 0}
        fired = []
        for r in test:
            intent = policy.record_intent(db, runId=run, actor="agent:curator-v3",
                                          action="claim.write", collection="records",
                                          recordId=r["recordId"] + f"-{label}",
                                          payload=payload_for(r))
            d = policy.evaluate(db, intent)
            wrong = is_wrong(r)
            if d["verdict"] == "deny":
                stats["denied"] += 1
            elif d["verdict"] == "escalate":
                stats["escalated_wrong" if wrong else "escalated_right"] += 1
                if (d.get("decidingRuleId") or "").startswith("learned_"):
                    fired.append((r["recordId"], d["decidingRuleId"], wrong))
            else:
                stats["asserted_wrong" if wrong else "asserted_right"] += 1
        return run, stats, fired

    run_off, off, _ = evaluate_test("before", False)
    run_on, on, fired = evaluate_test("after", True)

    print(f"  {len(test)} held-out records, "
          f"{sum(1 for r in test if is_wrong(r))} of them genuinely wrong\n")
    hdr = f"  {'':34s}{'learning off':>14s}{'learning on':>14s}"
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    rows = [
        ("wrong figures asserted into the record", "asserted_wrong", "lower is better"),
        ("wrong figures caught and escalated", "escalated_wrong", "higher is better"),
        ("correct figures asserted", "asserted_right", "higher is better"),
        ("correct figures needlessly escalated", "escalated_right", "the cost"),
        ("denied outright", "denied", ""),
    ]
    for label, key, note in rows:
        print(f"  {label:34s}{off[key]:>14d}{on[key]:>14d}   {note}")

    prevented = off["asserted_wrong"] - on["asserted_wrong"]
    cost = on["escalated_right"] - off["escalated_right"]
    print(f"\n  wrong figures prevented from landing : {prevented}")
    print(f"  correct figures sent to a human for no reason : {cost}")
    if prevented > 0 and cost == 0:
        print("  Strictly better: it caught errors without adding review burden.")
    elif prevented > 0:
        print(f"  A trade, stated honestly: {prevented} errors prevented at the cost of "
              f"{cost} unnecessary reviews.")
    elif prevented == 0 and cost == 0:
        print("  No measurable change on this held-out set. The shapes it learned did not")
        print("  recur here. That is a real result and the sample is small: a demonstration")
        print("  that reports an improvement it cannot show would be the actual failure.")
    else:
        print("  It got worse on this split. Reported rather than hidden.")

    if fired:
        R("Phase 5. Proof it was the learned rule that acted")
        for rec, rule, wrong in fired[:5]:
            print(f"  {rec[:46]:48s} escalated by {rule[:46]}")
            print(f"    this record's machine reading was "
                  f"{'WRONG' if wrong else 'correct'} per the hand audit")
        pol = db.policies.find_one({"ruleId": fired[0][1]})
        print(f"\n  the rule that fired, with its own provenance:")
        print(f"    ruleId         : {pol['ruleId']}")
        print(f"    effect         : {pol['effect']}  (a learned rule can only tighten)")
        print(f"    proposedBy     : {pol['proposedBy']}")
        print(f"    approvedBy     : {pol['approvedBy']}")
        print(f"    observedAccuracy: {pol['observedAccuracy']}")
        print(f"    learnedFrom    : {len(pol.get('learnedFrom') or [])} ledger entries")
        print(f"    reason         : {pol['reason'][:104]}")

    R("What makes this auditable rather than just adaptive")
    print("  The training set is the audit ledger, so every example the agent learned from")
    print("  is already attributable to the human who corrected it. A reviewer can ask of")
    print("  any learned rule: which corrections produced this, who made them, and what")
    print("  was the measured accuracy that justified it. All four are fields on the rule.")
    print()
    print("  Two constraints are enforced by the database, not by convention:")
    print("    a learned rule may only deny or escalate, never allow")
    print("    an active learned rule must name the human who approved it")
    print()
    print(f"  chain integrity across all of it: {ledger.verify_chain(db)}")


if __name__ == "__main__":
    main()
