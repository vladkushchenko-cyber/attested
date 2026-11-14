"""Real actions across real systems, with policy deciding what may leave the building.

Pulls live FDA premarket approval records, then tries to publish each one downstream.
Administrative supplements are refused by policy, which is the whole point: 12 of 25 real
PMA records are facility relocations, fermenter replacements and supplier changes, so an
unfiltered approval feed alerts analysts about manufacturing paperwork.

Also demonstrates the distinction the outbox exists for: `sent` means dispatched, while
`confirmed` means an independent read-back showed the effect took hold. A 2xx is not
evidence that anything happened.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attested import agent, ledger, sources           # noqa: E402
from attested.db import client, DB_NAME               # noqa: E402

db = client()[DB_NAME]
R = lambda s: print("\n" + s + "\n" + "=" * len(s))   # noqa: E731


def main():
    R("External systems, checked live")
    reach = sources.reachable()
    for name, up in reach.items():
        print(f"  {'reachable' if up else 'UNREACHABLE':12s} {name}")
    if not reach.get("openFDA"):
        print("\n  openFDA is unreachable, so this demo cannot run its publication path.")
        return

    R("A. A regulator may assert a regulatory rung; literature may not")
    fda = sources.openfda_pma("PD-L1 IHC 22C3", limit=12)
    print(f"  pulled {len(fda)} live FDA records for PD-L1 IHC 22C3")
    kinds = {}
    for r in fda:
        kinds[r["supplementKind"]] = kinds.get(r["supplementKind"], 0) + 1
    print(f"  supplement kinds: {kinds}")
    print("\n  Examples of what the administrative classifier is filtering:")
    for r in fda:
        if r["supplementKind"] == "administrative":
            print(f"    {r['sourceId']:14s} {r['sourceQuote'][:84]}")
    print()

    run_id, out = agent.publish_regulatory(db, trade_name="PD-L1 IHC 22C3")
    print(f"\n  run {run_id}")
    print(f"  published: {len(out.get('published', []))}   "
          f"denied: {len(out.get('denied', []))}")
    print("  Every denial names the rule, so nobody has to guess why an approval did not")
    print("  appear in the feed:")
    for sid, rule in out.get("denied", [])[:6]:
        print(f"    {sid:14s} {rule}")

    R("B. Sent is not the same as took effect")
    for o in db.outbox.find({"runId": run_id}, sort=[("ts", 1)]).limit(6):
        conf = o.get("confirmation") or {}
        print(f"  {o['_id']}  {o['channel']:16s} state={o['state']:10s} "
              f"confirmed_by={conf.get('method', '-')}")
    print("\n  The outbox separates the two states deliberately. A downstream system that")
    print("  returns 200 has accepted a request; only an independent read-back is evidence")
    print("  that the record actually changed there. Both transitions are ledgered.")

    R("C. A contrast: the same assertion from a congress abstract")
    print("  The agent now tries to assert the same rung 4 from a Crossref abstract instead")
    print("  of from the regulator.\n")
    from attested import policy
    try:
        abs_rec = sources.crossref_abstract("10.1200/JCO.2025.43.16_suppl.8500")
        payload = {
            "recordId": "rec-literature-rung4-attempt",
            "assay": "PD-L1 IHC 22C3", "indication": "lung", "jurisdiction": "US",
            "rung": 4, "authority": "agent", "rankable": False,
            "provenance": {
                "sourceId": abs_rec["sourceId"], "sourceType": "congress",
                "sourceQuote": abs_rec["sourceQuote"][:600] or "x" * 30,
                "sourceUrl": abs_rec["sourceUrl"],
                "fragmentVerified": True, "machineValue": None,
            },
        }
        intent = policy.record_intent(db, runId=run_id, actor="agent:curator-v3",
                                      action="rung.assert", collection="records",
                                      recordId=payload["recordId"], payload=payload)
        d = policy.evaluate(db, intent)
        print(f"  verdict: {d['verdict'].upper()}  rule: {d['decidingRuleId']}")
        print(f"  {d['reason']}")
        print(f"\n  grounding on that rule:")
        pol = db.policies.find_one({"ruleId": d["decidingRuleId"], "active": True})
        print(f"    {pol['grounding']}")
    except sources.SourceError as e:
        print(f"  Crossref unreachable: {e}")

    R("Chain integrity across both systems")
    print(f"  {ledger.verify_chain(db)}")
    print(f"  ledger entries: {db.ledger.count_documents({})}")
    print(f"  distinct runs  : {len(db.ledger.distinct('runId'))}")


if __name__ == "__main__":
    main()
