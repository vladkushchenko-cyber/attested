"""The curation agent.

It has no write path to `records`. For every candidate claim it:

    1. records an intent          -> `intents`
    2. asks the policy engine     -> `decisions` (+ a ledger entry either way)
    3. applies only on `allow`    -> `records` + ledger, in one transaction
    4. leaves `escalate` for a human, and stops on `deny`

Between every step it checks the halt event set by the change-stream watcher, so a halt
lands in flight rather than after the run finishes.

Candidate input is the real extraction output from the diagnostics corpus: 859 congress
abstracts with their parsed claims, ambiguity flags and fragment-verification state. That
matters, because the policy denials in a demo run are caused by genuine properties of real
records rather than by synthetic violations.
"""
import json
import os
import uuid
from datetime import datetime, timezone

from pymongo.errors import WriteError

from . import actions, control, ledger, policy, sources

# The real extraction output, shipped with the project so a demo run is reproducible:
# 859 congress abstracts with their parsed claims, ambiguity flags and
# fragment-verification state, plus live regulatory and reimbursement records.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.environ.get("ATTESTED_CORPUS", os.path.join(_HERE, "data", "corpus.json"))


def load_candidates(limit=12, corpus_path=CORPUS, want_rankable=False):
    """Turn real evidence records into candidate claim-write payloads."""
    with open(corpus_path) as fh:
        data = json.load(fh)
    # Deterministic but representative: file order clusters by congress, so taking the
    # first N would show only one kind of outcome. A stable hash order gives a spread
    # across verified, unverifiable and ambiguous records without using randomness
    # (which would make a demo run unreproducible).
    import hashlib
    ev = sorted(data["evidence"],
                key=lambda r: hashlib.md5(r["id"].encode()).hexdigest())
    out = []
    for e in ev:
        claims = e.get("claims") or {}
        if claims.get("sens") is None and claims.get("spec") is None:
            continue
        quote = " ".join((e.get("quote") or "").split())
        if len(quote) < 25:
            continue
        out.append({
            "recordId": "rec-" + e["id"].replace("/", "_").replace(".", "-")[:60],
            "assay": (e.get("entities") or ["(no product named)"])[0],
            "indication": "lung",
            "jurisdiction": "US",
            "modality": None,
            "rung": int(e.get("rung") or 2),
            "sensitivity": claims.get("sens"),
            "specificity": claims.get("spec"),
            "cohortN": None,
            "authority": "agent",
            # The agent records a machine-read figure as NOT rankable. It is permitted to
            # do that. Promoting a figure to rankable requires a human, because measured
            # machine accuracy on these pairs is 50%. Pass want_rankable=True to watch the
            # escalation path instead.
            "rankable": bool(want_rankable),
            "ambiguous": bool(e.get("claimAmbiguous")),
            "provenance": {
                "sourceId": e["id"],
                "sourceType": "congress",
                "sourceQuote": quote[:600],
                "sourceUrl": e.get("url"),
                "fragmentVerified": not bool(e.get("perfOutsideQuote")),
                "machineValue": {"sens": claims.get("sens"), "spec": claims.get("spec")},
            },
        })
        if len(out) >= limit:
            break
    return out


def start_run(db, actor, note=None):
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    db.runs.insert_one({
        "_id": run_id, "actor": actor, "state": "running",
        "startedAt": datetime.now(timezone.utc), "endedAt": None,
        "counters": {"proposed": 0, "allowed": 0, "denied": 0,
                     "escalated": 0, "applied": 0, "refusedByServer": 0},
        "note": note,
    })
    db.controls.insert_one({
        "_id": f"ctl-{run_id}-start", "runId": run_id, "command": "run",
        "issuedBy": actor, "note": note, "ts": datetime.now(timezone.utc),
    })
    ledger.append(db, runId=run_id, action="run.start", actor=actor,
                  verdict="allow", applied=True, reason=note or "run started")
    return run_id


def _bump(db, run_id, field, n=1):
    db.runs.update_one({"_id": run_id}, {"$inc": {f"counters.{field}": n}})


def curate(db, *, actor="agent:curator-v3", candidates=None, run_id=None,
           halt_watcher=None, verbose=True, pause_between=0.0):
    """Propose and, where permitted, apply a batch of claims."""
    import time

    run_id = run_id or start_run(db, actor, note="claim curation batch")
    cands = candidates if candidates is not None else load_candidates()
    results = {"allowed": [], "denied": [], "escalated": [], "refusedByServer": []}

    for i, payload in enumerate(cands, 1):
        if halt_watcher is not None:
            try:
                halt_watcher.check()
            except control.Halted as h:
                db.runs.update_one({"_id": run_id}, {
                    "$set": {"state": "halted", "endedAt": datetime.now(timezone.utc),
                             "note": str(h)}})
                if verbose:
                    print(f"  [{i}] HALTED in flight: {h}")
                results["halted"] = True
                results["haltedAfter"] = i - 1
                return run_id, results

        intent = policy.record_intent(
            db, runId=run_id, actor=actor, action="claim.write",
            collection="records", recordId=payload["recordId"], payload=payload)
        _bump(db, run_id, "proposed")
        decision = policy.evaluate(db, intent)

        if decision["verdict"] == "deny":
            _bump(db, run_id, "denied")
            results["denied"].append((payload["recordId"], decision["reason"],
                                      decision["decidingRuleId"]))
            if verbose:
                print(f"  [{i}] DENY      {decision['decidingRuleId']}: {decision['reason'][:88]}")
        elif decision["verdict"] == "escalate":
            _bump(db, run_id, "escalated")
            results["escalated"].append((payload["recordId"], decision["_id"],
                                         decision["decidingRuleId"]))
            if verbose:
                print(f"  [{i}] ESCALATE  {decision['decidingRuleId']}: awaiting a human")
        else:
            try:
                e = actions.apply(db, intent, decision,
                                  record_builder=actions.claim_record_builder)
                _bump(db, run_id, "allowed"); _bump(db, run_id, "applied")
                results["allowed"].append((payload["recordId"], int(e["seq"])))
                if verbose:
                    print(f"  [{i}] APPLIED   {payload['recordId']} at ledger seq {int(e['seq'])}")
            except WriteError as exc:
                _bump(db, run_id, "refusedByServer")
                results["refusedByServer"].append((payload["recordId"], str(exc)[:120]))
                if verbose:
                    print(f"  [{i}] SERVER REFUSED {payload['recordId']}: {str(exc)[:90]}")
        if pause_between:
            time.sleep(pause_between)

    db.runs.update_one({"_id": run_id}, {
        "$set": {"state": "completed", "endedAt": datetime.now(timezone.utc)}})
    ledger.append(db, runId=run_id, action="run.complete", actor=actor,
                  verdict="allow", applied=True, reason="batch finished")
    return run_id, results


def publish_regulatory(db, *, actor="agent:curator-v3", trade_name="PD-L1 IHC 22C3",
                       run_id=None, verbose=True):
    """Act across a second system: pull FDA approvals and try to publish them downstream.

    Administrative supplements are denied by policy, which is the point: without that rule
    an approval alert fires on fermenter replacements.
    """
    run_id = run_id or start_run(db, actor, note=f"regulatory publication: {trade_name}")
    try:
        recs = sources.openfda_pma(trade_name, limit=12)
    except sources.SourceError as e:
        print(f"  openFDA unreachable: {e}")
        return run_id, {}
    out = {"published": [], "denied": []}
    for r in recs:
        payload = {
            "recordId": f"rec-fda-{r['sourceId']}",
            "assay": r.get("tradeName") or trade_name,
            "indication": "lung", "jurisdiction": "US",
            "rung": 4,                              # permitted: the source is a regulator
            "supplementKind": r["supplementKind"],
            "authority": "agent", "rankable": False,
            "provenance": {
                "sourceId": r["sourceId"], "sourceType": "regulator",
                "sourceQuote": (r["sourceQuote"] or
                                f"FDA premarket approval record {r['sourceId']} for "
                                f"{r.get('tradeName')}.")[:600],
                "sourceUrl": r["sourceUrl"], "fragmentVerified": True,
                "machineValue": None,
            },
        }
        intent = policy.record_intent(db, runId=run_id, actor=actor,
                                      action="approval.publish", collection="records",
                                      recordId=payload["recordId"], payload=payload)
        decision = policy.evaluate(db, intent)
        if decision["verdict"] == "allow":
            oid = actions.publish_outbox(
                db, runId=run_id, channel="curated-dataset", payload=payload,
                intent=intent, decision=decision, actor=actor)
            # An independent read-back is what distinguishes `sent` from `confirmed`.
            actions.confirm_outbox(
                db, oid, runId=run_id, actor=actor,
                confirmation={"method": "read-back", "sourceId": r["sourceId"]})
            out["published"].append((r["sourceId"], r["supplementKind"]))
            if verbose:
                print(f"  PUBLISHED {r['sourceId']:14s} {r['supplementKind']}")
        else:
            out["denied"].append((r["sourceId"], decision["decidingRuleId"]))
            if verbose:
                print(f"  DENY      {r['sourceId']:14s} {decision['decidingRuleId']}")
    db.runs.update_one({"_id": run_id},
                       {"$set": {"state": "completed",
                                 "endedAt": datetime.now(timezone.utc)}})
    return run_id, out
