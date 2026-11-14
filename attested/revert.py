"""Rollback as compensation, not deletion.

A ledger you can edit is not a ledger. So a revert never removes the original entries: it
walks the applied effects of a run backwards and writes a **compensating action** for each
one, which is itself policy-gated and itself ledgered.

The result is that the audit contains both what was believed and what corrected it, which
is what a reviewer actually needs. An end state that has been tidied up cannot answer
"what did this system assert last Tuesday, and who let it".
"""
from datetime import datetime, timezone

from . import ledger


def plan(db, run_id):
    """The compensations that a revert would apply, newest effect first."""
    entries = list(db.ledger.find(
        {"runId": run_id, "applied": True, "effect": {"$ne": None}},
        sort=[("seq", -1)]))
    out = []
    for e in entries:
        eff = e["effect"]
        out.append({
            "ledgerSeq": int(e["seq"]),
            "collection": eff["collection"],
            "recordId": eff["recordId"],
            "action": ("delete" if eff.get("before") is None else "restore"),
            "before": eff.get("before"),
            "after": eff.get("after"),
        })
    return out


def apply_revert(db, run_id, *, by, note=None):
    """Apply the compensations. `by` must be a human permitted to revert."""
    ident = db.identities.find_one({"_id": by})
    if not ident or "revert.apply" not in (ident.get("allowedActions") or []):
        raise PermissionError(f"{by!r} is not permitted to revert a run")
    if ident.get("kind") != "human":
        raise PermissionError("a revert must be authorised by a human identity")

    steps = plan(db, run_id)
    applied = []
    for step in steps:
        coll = step["collection"]
        rid = step["recordId"]
        current = db[coll].find_one({"_id": rid})

        if step["action"] == "delete":
            db[coll].delete_one({"_id": rid})
            after = None
        else:
            restored = dict(step["before"])
            db[coll].replace_one({"_id": rid}, restored, upsert=True)
            after = restored

        e = ledger.append(
            db, runId=run_id, action=f"revert.{coll}", actor=by, onBehalfOf=None,
            verdict="revert", applied=True,
            reason=(f"compensating the effect recorded at seq {step['ledgerSeq']}"
                    + (f": {note}" if note else "")),
            effect={"collection": coll, "recordId": rid,
                    "before": current, "after": after},
            compensates=f"led-{step['ledgerSeq']:08d}",
        )
        applied.append({"recordId": rid, "action": step["action"],
                        "compensatedSeq": step["ledgerSeq"],
                        "newSeq": int(e["seq"])})

    # Downstream effects cannot be un-sent, only marked compensated. Saying so is more
    # honest than implying the outside world was rewound.
    db.outbox.update_many({"runId": run_id, "state": {"$in": ["sent", "confirmed"]}},
                          {"$set": {"state": "compensated"}})

    db.runs.update_one({"_id": run_id},
                       {"$set": {"state": "reverted",
                                 "endedAt": datetime.now(timezone.utc),
                                 "note": note}})
    ledger.append(db, runId=run_id, action="run.revert", actor=by,
                  verdict="revert", applied=True,
                  reason=(f"run reverted by {by}: {len(applied)} compensating actions"
                          + (f". {note}" if note else "")))
    return applied
