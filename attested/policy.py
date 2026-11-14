"""The policy engine.

The agent never acts directly. It writes an intent, the engine decides, and only an `allow`
decision can be presented to the executor. Rules live in the `policies` collection as data,
not code: a predicate is a *name* resolved here, so a policy document can be reviewed by
someone who is not reading Python and cannot smuggle in executable logic.

Order of enforcement, strongest refusal first:

1. identity is active and the action is in its `allowedActions`
2. the target is inside the identity's scope
3. per-run rate limit
4. the rule set, in document order; the first `deny` wins, otherwise any `escalate` wins,
   otherwise `allow`

Every rule considered is recorded on the decision, not only the one that fired, so a
reviewer can see what was checked rather than inferring it from an outcome.
"""
import uuid
from datetime import datetime, timezone

from . import ledger


# --------------------------------------------------------------------------- predicates
# Each returns True when the rule MATCHES, i.e. when its effect should apply.

def _p_missing_source_quote(intent, ctx, params):
    q = (intent["payload"].get("provenance") or {}).get("sourceQuote") or ""
    return len(q.strip()) < int(params.get("minLength", 25))


def _p_fragment_unverified(intent, ctx, params):
    prov = intent["payload"].get("provenance") or {}
    return prov.get("fragmentVerified") is False


def _p_claim_ambiguous(intent, ctx, params):
    return bool(intent["payload"].get("ambiguous"))


def _p_rung_from_literature(intent, ctx, params):
    rung = intent["payload"].get("rung")
    st = (intent["payload"].get("provenance") or {}).get("sourceType")
    if rung is None:
        return False
    return int(rung) >= int(params.get("minRung", 4)) and st not in ("regulator", "hta")


def _p_rankable_by_agent(intent, ctx, params):
    return bool(intent["payload"].get("rankable")) and ctx["identity"]["kind"] != "human"


def _p_administrative_supplement(intent, ctx, params):
    kind = (intent["payload"].get("supplementKind") or "").lower()
    return kind == "administrative"


def _p_value_out_of_range(intent, ctx, params):
    for k in ("sensitivity", "specificity"):
        v = intent["payload"].get(k)
        if v is None:
            continue
        if not (0 <= float(v) <= 100):
            return True
    return False


def _p_always(intent, ctx, params):
    return True


PREDICATES = {
    "missing_source_quote": _p_missing_source_quote,
    "fragment_unverified": _p_fragment_unverified,
    "claim_ambiguous": _p_claim_ambiguous,
    "rung_from_literature": _p_rung_from_literature,
    "rankable_by_agent": _p_rankable_by_agent,
    "administrative_supplement": _p_administrative_supplement,
    "value_out_of_range": _p_value_out_of_range,
    "always": _p_always,
}


class Denied(Exception):
    def __init__(self, decision):
        self.decision = decision
        super().__init__(decision.get("reason", "denied"))


def record_intent(db, *, runId, actor, action, collection, recordId, payload) -> dict:
    intent = {
        "_id": f"int-{uuid.uuid4().hex[:12]}",
        "runId": runId,
        "actor": actor,
        "action": action,
        "target": {"collection": collection, "recordId": recordId},
        "payload": payload,
        "ts": datetime.now(timezone.utc),
    }
    db.intents.insert_one(intent)
    return intent


def _scope_ok(identity, intent) -> tuple:
    scope = identity.get("scope") or {}
    ind = intent["payload"].get("indication")
    if ind is not None and ind not in (scope.get("indications") or []):
        return False, (f"indication {ind!r} is outside this identity's scope "
                       f"{scope.get('indications')}")
    jur = intent["payload"].get("jurisdiction")
    if jur is not None and jur not in (scope.get("jurisdictions") or []):
        return False, (f"jurisdiction {jur!r} is outside this identity's scope "
                       f"{scope.get('jurisdictions')}")
    return True, None


def evaluate(db, intent: dict) -> dict:
    """Decide an intent. Writes a decision document and a ledger entry. Never acts."""
    identity = db.identities.find_one({"_id": intent["actor"]})
    evaluated = []
    verdict, reason, rule_id, rule_ver = "allow", "no rule objected", None, None

    def finish():
        decision = {
            "_id": f"dec-{uuid.uuid4().hex[:12]}",
            "intentId": intent["_id"],
            "runId": intent["runId"],
            "verdict": verdict,
            "evaluated": evaluated,
            "decidingRuleId": rule_id,
            "decidingVersion": rule_ver,
            "reason": reason,
            "resolution": None,
            "ts": datetime.now(timezone.utc),
        }
        db.decisions.insert_one(decision)
        ledger.append(
            db, runId=intent["runId"], action=intent["action"], actor=intent["actor"],
            verdict=verdict, reason=reason, applied=False,
            intentId=intent["_id"], decisionId=decision["_id"],
            policyId=rule_id, policyVersion=rule_ver,
            inputs={"recordId": intent["target"]["recordId"],
                    "sourceQuote": (intent["payload"].get("provenance") or {}).get("sourceQuote")},
        )
        return decision

    # 1. identity
    if not identity or not identity.get("active"):
        verdict, reason = "deny", f"identity {intent['actor']!r} is unknown or inactive"
        return finish()
    if intent["action"] not in (identity.get("allowedActions") or []):
        verdict, reason = "deny", (
            f"identity {intent['actor']!r} is not permitted action {intent['action']!r}; "
            f"permitted: {identity.get('allowedActions')}")
        return finish()

    # 2. scope
    ok, why = _scope_ok(identity, intent)
    if not ok:
        verdict, reason, rule_id, rule_ver = "deny", why, "scope_indication", 1
        return finish()

    # 3. rate limit
    cap = (identity.get("scope") or {}).get("maxActionsPerRun")
    if cap is not None:
        applied = db.ledger.count_documents(
            {"runId": intent["runId"], "actor": intent["actor"], "applied": True})
        if applied >= int(cap):
            verdict, reason, rule_id, rule_ver = "deny", (
                f"per-run action cap of {cap} already reached ({applied} applied); "
                "a runaway agent is a policy violation, not an incident"), "rate_limit_per_run", 1
            return finish()

    # 4. the rule set
    ctx = {"identity": identity}
    cursor = db.policies.find(
        {"active": True, "appliesTo": {"$in": [intent["action"], "*"]}},
        sort=[("ruleId", 1), ("version", -1)])
    seen = set()
    escalate_hit = None
    for rule in cursor:
        if rule["ruleId"] in seen:      # only the newest active version of each rule
            continue
        seen.add(rule["ruleId"])
        fn = PREDICATES.get(rule["predicate"])
        if fn is None:
            evaluated.append({"ruleId": rule["ruleId"], "version": rule["version"],
                              "matched": False, "effect": None,
                              "reason": f"predicate {rule['predicate']!r} is not implemented"})
            continue
        matched = bool(fn(intent, ctx, rule.get("params") or {}))
        evaluated.append({"ruleId": rule["ruleId"], "version": rule["version"],
                          "matched": matched,
                          "effect": rule["effect"] if matched else None,
                          "reason": rule["reason"] if matched else None})
        if matched and rule["effect"] == "deny":
            verdict, reason = "deny", rule["reason"]
            rule_id, rule_ver = rule["ruleId"], rule["version"]
            return finish()
        if matched and rule["effect"] == "escalate" and escalate_hit is None:
            escalate_hit = rule

    if escalate_hit is not None:
        verdict, reason = "escalate", escalate_hit["reason"]
        rule_id, rule_ver = escalate_hit["ruleId"], escalate_hit["version"]
    return finish()


def resolve_escalation(db, decision_id: str, *, by: str, outcome: str, note=None) -> dict:
    """A human approves or rejects an escalated intent. This is where authority enters."""
    human = db.identities.find_one({"_id": by})
    if not human or human.get("kind") != "human":
        raise ValueError(f"{by!r} is not a human identity; an escalation cannot be "
                         "resolved by an agent")
    db.decisions.update_one(
        {"_id": decision_id},
        {"$set": {"resolution": {"by": by, "outcome": outcome, "note": note,
                                 "ts": datetime.now(timezone.utc)}}})
    d = db.decisions.find_one({"_id": decision_id})
    ledger.append(db, runId=d["runId"], action="escalation.resolve", actor=by,
                  verdict="allow" if outcome == "approved" else "deny",
                  applied=False, decisionId=decision_id,
                  reason=f"escalation {outcome} by {by}" + (f": {note}" if note else ""))
    return d
