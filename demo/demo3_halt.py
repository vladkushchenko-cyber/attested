"""Q3: How do you stop or roll back an agent mid-run?

Shows:
  A. a halt issued from outside the process lands IN FLIGHT via a change stream
  B. the partial state is intact and explained, not corrupt
  C. a revert writes compensating actions rather than deleting history
  D. the chain still verifies afterwards, and the audit holds both the action and its undo
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attested import agent, control, ledger, revert   # noqa: E402
from attested.db import client, DB_NAME               # noqa: E402

db = client()[DB_NAME]
R = lambda s: print("\n" + s + "\n" + "=" * len(s))   # noqa: E731


def main():
    R("A. Halting a run in flight")
    cands = [c for c in agent.load_candidates(limit=120)
             if c["provenance"]["fragmentVerified"] and not c["ambiguous"]][:8]
    print(f"  {len(cands)} clean candidates queued; the agent will pause between each so a")
    print("  halt can be demonstrated landing mid-run rather than between runs.")

    run_id = agent.start_run(db, "agent:curator-v3", note="batch that will be halted")
    watcher = control.HaltWatcher(db, run_id).start()
    print(f"  run {run_id}")
    print("  change stream open on `controls`, filtered to this run")

    def halt_after_delay():
        # A separate thread stands in for a separate process: a compliance reviewer
        # clicking stop, or an automated guard reacting to a signal.
        time.sleep(2.0)
        print("\n  >> human:compliance issues a halt from outside the agent process")
        control.issue_halt(db, run_id, by="human:compliance",
                           note="pausing the batch: policy review of the rankable rule")

    threading.Thread(target=halt_after_delay, daemon=True).start()
    _, res = agent.curate(db, candidates=cands, run_id=run_id,
                          halt_watcher=watcher, pause_between=0.7)
    watcher.stop()

    run = db.runs.find_one({"_id": run_id})
    print(f"\n  run state: {run['state']}")
    print(f"  stopped after {res.get('haltedAfter')} of {len(cands)} candidates")
    print(f"  counters: {{k: v for k, v in run['counters'].items() if v}}".replace(
        "{k: v for k, v in run['counters'].items() if v}",
        str({k: v for k, v in run["counters"].items() if v})))
    print("  The process was never killed. The agent checked the halt event between steps,")
    print("  so no record was left half-written and the halt itself is in the ledger.")

    halt_entry = db.ledger.find_one({"runId": run_id, "verdict": "halt"})
    print(f"\n  ledger seq {int(halt_entry['seq'])}: {halt_entry['action']} by "
          f"{halt_entry['actor']}")
    print(f"    {halt_entry['reason']}")

    R("B. The partial state is intact and explained")
    applied = list(db.ledger.find({"runId": run_id, "applied": True,
                                   "effect.collection": "records"},
                                  sort=[("seq", 1)]))
    print(f"  {len(applied)} records were written before the halt:")
    for e in applied:
        rec = db.records.find_one({"_id": e["effect"]["recordId"]})
        print(f"    {e['effect']['recordId'][:46]:48s} "
              f"sens={rec['sensitivity']} spec={rec['specificity']} "
              f"rankable={rec['rankable']}")
    print("  A reviewer can tell exactly which of the queued candidates were applied and")
    print("  which were never attempted, because the intents are recorded too:")
    print(f"    intents written : {db.intents.count_documents({'runId': run_id})}")
    print(f"    decisions made  : {db.decisions.count_documents({'runId': run_id})}")
    print(f"    candidates queued: {len(cands)}")

    R("C. Revert: compensating actions, not deletion")
    plan = revert.plan(db, run_id)
    print(f"  {len(plan)} compensations planned (newest effect first):")
    for p in plan:
        print(f"    seq {p['ledgerSeq']:<4} {p['action']:8s} {p['collection']}/"
              f"{p['recordId'][:40]}")

    before_count = db.records.count_documents({})
    applied_rev = revert.apply_revert(db, run_id, by="human:compliance",
                                      note="reverting pending the policy review")
    after_count = db.records.count_documents({})
    print(f"\n  applied {len(applied_rev)} compensating actions")
    print(f"  records collection: {before_count} -> {after_count}")
    print(f"  run state: {db.runs.find_one({'_id': run_id})['state']}")

    R("D. The audit holds both the action and its undo")
    print("  Nothing was deleted from the ledger. Each compensation names what it reverses.\n")
    for e in db.ledger.find({"runId": run_id, "compensates": {"$ne": None}},
                            sort=[("seq", 1)]):
        print(f"    seq {int(e['seq']):<4} {e['action']:<22} compensates {e['compensates']}")
        print(f"              by {e['actor']}: {e['reason'][:88]}")

    v = ledger.verify_chain(db)
    print(f"\n  chain after revert: {v}")

    print("\n  Downstream effects are marked compensated rather than claimed as undone:")
    for o in db.outbox.find({"runId": run_id}):
        print(f"    {o['channel']}: {o['state']}")
    if db.outbox.count_documents({"runId": run_id}) == 0:
        print("    (none in this run; see demo4_crosssystem.py for the outbox path)")

    R("Q3 answered")
    print("Stop is cooperative and driven by a change stream on `controls`, so a halt issued")
    print("from another process lands between steps rather than at the end of the run, and")
    print("the halt is itself an audited action with an identity attached. Rollback is")
    print("compensation: every applied effect recorded its prior value, so a revert writes a")
    print("new entry undoing it and names what it reversed. The ledger therefore shows what")
    print("the system believed and what corrected it, which is what a reviewer needs. An end")
    print("state that has been tidied up cannot answer 'what did this assert last Tuesday'.")


if __name__ == "__main__":
    main()
