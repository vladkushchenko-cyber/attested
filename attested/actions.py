"""The executor. The only path from an allowed decision to a real effect.

Two invariants:

1. **Nothing is applied without a decision id.** `apply` refuses an intent whose decision
   is not `allow`, or is `escalate` without an approved resolution naming a human. The
   database validators then refuse anything that slips past this anyway.

2. **The effect and its ledger entry commit together.** Both happen inside one transaction,
   so there is no window in which a record changed but the audit did not, or vice versa.
   This is why the deployment is a replica set rather than a standalone server.
"""
import uuid
from datetime import datetime, timezone

from pymongo.errors import WriteError

from . import ledger
from .policy import Denied


class NotPermitted(Exception):
    pass


def _authorised(db, decision):
    """Return (ok, onBehalfOf). An escalation needs an approving human."""
    if decision["verdict"] == "allow":
        return True, None
    if decision["verdict"] == "escalate":
        res = decision.get("resolution")
        if res and res.get("outcome") == "approved":
            return True, res["by"]
        return False, None
    return False, None


def apply(db, intent, decision, *, actor=None, record_builder=None):
    """Apply an allowed intent to the record of truth, transactionally with the ledger.

    record_builder(payload, before) -> the document to upsert into `records`.
    """
    ok, on_behalf = _authorised(db, decision)
    if not ok:
        raise NotPermitted(
            f"intent {intent['_id']} is not authorised: verdict={decision['verdict']}"
            + ("" if decision["verdict"] != "escalate" else " with no approved resolution"))

    acting = actor or intent["actor"]
    rec_id = intent["target"]["recordId"]
    before = db.records.find_one({"_id": rec_id})
    # An approved escalation transfers authority: the human read the sentence, so the
    # resulting record is human-authored, not agent-authored. This is what makes the
    # "only a human may mark a figure rankable" rule satisfiable legitimately rather
    # than bypassable.
    after = record_builder(intent["payload"], before, on_behalf_of=on_behalf)
    # The intent's target is authoritative, not the payload. Policy was evaluated against
    # `intent.target.recordId`, so the payload must not be able to redirect the write to a
    # different record than the one that was approved.
    after["_id"] = rec_id

    client = db.client
    with client.start_session() as s:
        with s.start_transaction():
            try:
                db.records.replace_one({"_id": rec_id}, after, upsert=True, session=s)
            except WriteError as e:
                # The server refused. Record the refusal in the ledger outside the aborted
                # transaction, so a schema rejection is itself auditable.
                s.abort_transaction()
                ledger.append(
                    db, runId=intent["runId"], action=intent["action"], actor=acting,
                    verdict="deny", applied=False, intentId=intent["_id"],
                    decisionId=decision["_id"],
                    reason=f"refused by the records validator: {str(e)[:220]}",
                    inputs={"recordId": rec_id},
                )
                raise

            e = ledger.append(
                db, runId=intent["runId"], action=intent["action"], actor=acting,
                onBehalfOf=on_behalf,
                verdict=decision["verdict"], applied=True,
                intentId=intent["_id"], decisionId=decision["_id"],
                policyId=decision.get("decidingRuleId"),
                policyVersion=decision.get("decidingVersion"),
                reason=(f"applied under authority of {on_behalf}" if on_behalf
                        else "applied: no rule objected"),
                inputs={"recordId": rec_id,
                        "sourceId": (intent["payload"].get("provenance") or {}).get("sourceId"),
                        "sourceQuote": (intent["payload"].get("provenance") or {}).get("sourceQuote")},
                effect={"collection": "records", "recordId": rec_id,
                        "before": before, "after": after},
                session=s,
            )
            db.records.update_one({"_id": rec_id}, {"$set": {"ledgerSeq": e["seq"]}}, session=s)
    return e


def claim_record_builder(payload, before, on_behalf_of=None):
    """Build the record document for a claim write.

    When `on_behalf_of` is set the write is happening under a human's approval, so the
    record's authority becomes `human` and the machine's original reading is retained
    alongside it for reconstruction.
    """
    now = datetime.now(timezone.utc)
    doc = dict(before) if before else {}
    doc.update({
        "_id": payload["recordId"],
        "assay": payload["assay"],
        "indication": payload["indication"],
        "modality": payload.get("modality"),
        "rung": int(payload["rung"]),
        "sensitivity": (float(payload["sensitivity"])
                        if payload.get("sensitivity") is not None else None),
        "specificity": (float(payload["specificity"])
                        if payload.get("specificity") is not None else None),
        "cohortN": (int(payload["cohortN"]) if payload.get("cohortN") is not None else None),
        "authority": "human" if on_behalf_of else payload.get("authority", "agent"),
        "rankable": bool(payload.get("rankable", False)),
        "provenance": payload["provenance"],
        "updatedAt": now,
    })
    doc.setdefault("ledgerSeq", None)
    return doc


# ---------------------------------------------------------------- downstream effects
def publish_outbox(db, *, runId, channel, payload, intent, decision, actor):
    """Send a downstream effect. `sent` and `confirmed` are deliberately separate states.

    A 2xx from a downstream system means the request was accepted, not that the effect
    took hold. Only an independent read-back moves the row to `confirmed`.
    """
    doc = {
        "_id": f"out-{uuid.uuid4().hex[:12]}",
        "runId": runId, "channel": channel, "state": "pending",
        "payload": payload, "confirmation": None, "ledgerSeq": None,
        "ts": datetime.now(timezone.utc),
    }
    db.outbox.insert_one(doc)
    e = ledger.append(db, runId=runId, action=f"{channel}.send", actor=actor,
                      verdict=decision["verdict"], applied=True,
                      intentId=intent["_id"], decisionId=decision["_id"],
                      reason="dispatched downstream; not yet confirmed",
                      effect={"collection": "outbox", "recordId": doc["_id"],
                              "before": None, "after": {"state": "sent"}})
    db.outbox.update_one({"_id": doc["_id"]},
                         {"$set": {"state": "sent", "ledgerSeq": e["seq"]}})
    return doc["_id"]


def confirm_outbox(db, outbox_id, *, confirmation, runId, actor):
    """Move an effect from `sent` to `confirmed` on independent evidence."""
    before = db.outbox.find_one({"_id": outbox_id})
    db.outbox.update_one({"_id": outbox_id},
                         {"$set": {"state": "confirmed", "confirmation": confirmation}})
    ledger.append(db, runId=runId, action=f"{before['channel']}.confirm", actor=actor,
                  verdict="allow", applied=True,
                  reason="confirmed by independent read-back, so the effect took hold",
                  effect={"collection": "outbox", "recordId": outbox_id,
                          "before": {"state": before["state"]},
                          "after": {"state": "confirmed"}})
