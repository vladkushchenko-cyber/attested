"""Prove the server refuses. These are the rules the agent cannot get around.

Each case writes directly with a raw driver connection, bypassing the policy engine
entirely, which is the scenario that matters: application code can be buggy or bypassed,
so the last line of defence has to be the database.
"""
import os
import sys
from datetime import datetime, timezone

from pymongo.errors import WriteError
from bson.int64 import Int64

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attested.db import client, DB_NAME  # noqa: E402

NOW = datetime.now(timezone.utc)
db = client()[DB_NAME]


def base_record(**over):
    r = {
        "_id": "rec-test", "assay": "Test assay", "indication": "lung",
        "modality": "ctDNA", "rung": 2, "sensitivity": 68.0, "specificity": 86.0,
        "cohortN": 300, "authority": "agent", "rankable": False,
        "provenance": {
            "sourceId": "10.1200/jco.test", "sourceType": "congress",
            "sourceQuote": "With lower sensitivity (68%) and high specificity (86%), "
                           "liquid biopsy should be used as a triage test.",
            "sourceUrl": None, "fragmentVerified": True, "machineValue": None,
        },
        "ledgerSeq": None, "updatedAt": NOW,
    }
    r.update(over)
    return r


def expect_reject(coll, doc, label):
    try:
        db[coll].insert_one(doc)
        db[coll].delete_one({"_id": doc["_id"]})
        print(f"  FAIL  accepted but should have been refused: {label}")
        return False
    except WriteError:
        print(f"  pass  server refused: {label}")
        return True


def expect_accept(coll, doc, label):
    try:
        db[coll].insert_one(doc)
        db[coll].delete_one({"_id": doc["_id"]})
        print(f"  pass  server accepted: {label}")
        return True
    except WriteError as e:
        print(f"  FAIL  refused but should have been accepted: {label}\n        {e}")
        return False


def main():
    ok = []
    print("records: provenance is mandatory")
    ok.append(expect_accept("records", base_record(), "a fully provenanced agent claim"))
    r = base_record(); r["provenance"].pop("sourceQuote")
    ok.append(expect_reject("records", r, "no source quote at all"))
    r = base_record(); r["provenance"]["sourceQuote"] = "too short"
    ok.append(expect_reject("records", r, "source quote under 25 characters"))
    r = base_record(); r.pop("provenance")
    ok.append(expect_reject("records", r, "no provenance object"))
    r = base_record(); r["provenance"]["sourceType"] = "hearsay"
    ok.append(expect_reject("records", r, "source type not in the enum"))

    print("\nrecords: a regulatory rung may not come from literature")
    ok.append(expect_reject("records", base_record(rung=4), "rung 4 from a congress abstract"))
    ok.append(expect_reject("records", base_record(rung=5), "rung 5 from a congress abstract"))
    r = base_record(rung=4); r["provenance"]["sourceType"] = "regulator"
    ok.append(expect_accept("records", r, "rung 4 from a regulator"))
    r = base_record(rung=5); r["provenance"]["sourceType"] = "hta"
    ok.append(expect_accept("records", r, "rung 5 from an HTA body"))
    ok.append(expect_reject("records", base_record(rung=6), "rung outside 1 to 5"))

    print("\nrecords: only a human may mark a figure rankable")
    ok.append(expect_reject("records", base_record(rankable=True, authority="agent"),
                            "agent marking its own figure rankable"))
    ok.append(expect_accept("records", base_record(rankable=True, authority="human"),
                            "human marking a figure rankable"))
    r = base_record(rankable=True, authority="human")
    r["provenance"]["fragmentVerified"] = False
    ok.append(expect_reject("records", r, "rankable with an unverifiable fragment"))

    print("\nrecords: a mis-parsed percentage cannot land")
    ok.append(expect_reject("records", base_record(sensitivity=168.0), "sensitivity 168%"))
    ok.append(expect_reject("records", base_record(specificity=-5.0), "specificity -5%"))
    ok.append(expect_reject("records", base_record(cohortN=0), "cohort size of zero"))

    print("\nrecords: no undeclared fields")
    ok.append(expect_reject("records", base_record(sneaky="value"),
                            "an undeclared extra field"))

    print("\nledger: authority is not optional for an applied escalation")
    def led(**over):
        e = {"_id": "led-test", "seq": Int64(999999), "runId": "run-test",
             "action": "claim.write", "actor": "agent:curator-v3", "verdict": "escalate",
             "applied": True, "onBehalfOf": None, "reason": "test entry",
             "prevHash": "0" * 64, "hash": "f" * 64, "ts": NOW}
        e.update(over); return e
    ok.append(expect_reject("ledger", led(), "applied escalation with no human named"))
    ok.append(expect_accept("ledger", led(onBehalfOf="human:ester"),
                            "applied escalation naming the human"))
    ok.append(expect_accept("ledger", led(verdict="allow", onBehalfOf=None),
                            "a plain allow needs no human"))
    ok.append(expect_reject("ledger", led(prevHash="short"), "a truncated hash chain link"))

    print("\nidentities and policies")
    ok.append(expect_reject("identities", {
        "_id": "agent:bad", "kind": "agent", "displayName": "Bad",
        "allowedActions": ["claim.write", "delete.everything"],
        "scope": {"indications": ["lung"], "jurisdictions": ["US"]}, "active": True},
        "an identity claiming an action outside the known set"))
    ok.append(expect_reject("policies", {
        "_id": "pol:x", "ruleId": "x", "version": 1, "appliesTo": ["claim.write"],
        "effect": "deny", "predicate": "p", "reason": "short", "grounding": "short",
        "active": True}, "a policy with no usable reason or grounding"))

    print(f"\n{sum(ok)}/{len(ok)} validator cases behaved correctly")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
