"""Learning from mistakes, without leaving the audit layer.

The agent does not get a memory store. It gets a mirror: the ledger already records every
case where a human corrected what the agent proposed, including the machine's original
value. That is a labelled dataset of the agent's own errors, and it is already governed,
already provenanced and already tamper-evident.

So learning here means three things, all of them inside the identity/policy/audit frame:

1. **Mine** the ledger for corrections, and classify each by failure mode.
2. **Calibrate**: group the failures by the *shape of the source sentence* and measure the
   agent's observed accuracy per shape. Written to `calibration`.
3. **Propose** tightened policy rules for the shapes it gets wrong, which the policy engine
   then enforces on the next run.

Two governance constraints make this safe, and both are enforced by the database rather
than by convention:

* **The ratchet.** A rule carrying `proposedBy` may only have effect `deny` or `escalate`.
  An agent physically cannot propose a rule that lets it do more. The `policies` validator
  refuses it.
* **No self-activation.** A learned rule with `active: true` must name `approvedBy`. The
  agent writes proposals as inactive; a human activates them, and that activation is itself
  policy-gated and ledgered.

The four failure modes below are not invented. They came from hand-auditing 20 machine-read
sensitivity/specificity pairs against their source sentences, where 10 were wrong.
"""
import re
import uuid
from datetime import datetime, timezone

from . import ledger, policy

# --------------------------------------------------------------------- failure modes
FAILURE_MODES = {
    "metric_inversion":
        "The two metric names sit adjacent to the two numbers and the binding crossed "
        "over, so sensitivity and specificity were swapped.",
    "adjacent_metric_theft":
        "A neighbouring metric (accuracy, precision, PPV, NPV) had its figure taken and "
        "attributed to sensitivity or specificity.",
    "ci_leakage":
        "A confidence-interval bound, usually 95, was read as a result.",
    "range_split":
        "A range or inequality covering both metrics was split into two false point "
        "values instead of being refused.",
    "unclassified":
        "Corrected, but the cause does not match a known shape.",
}

# ------------------------------------------------------------- sentence-shape features
# Each feature is a syntactic property of the source sentence that the audit showed
# correlates with a specific failure. These are the learnable part: a shape the agent gets
# wrong is a shape it should refuse to assert from.
FEATURES = {
    "ci_present": re.compile(r"\b9[05]\s?%?\s*(?:CI\b|confidence interval)", re.I),
    "reversed_order": None,          # computed: specificity mentioned before sensitivity
    "third_metric_near": re.compile(
        r"(accurac\w*|precision|\bPPV\b|\bNPV\b|F1|concordan\w*)", re.I),
    "range_form": re.compile(r"\d{1,3}\s?%?\s*(?:-|–|to)\s*\d{1,3}\s?%|"
                             r"(?:over|above|>|>=|≥|under|<)\s*\d{1,3}\s?%", re.I),
    "conjoined_metrics": re.compile(
        r"sensitivit\w*\s+and\s+specificit\w*|specificit\w*\s+and\s+sensitivit\w*", re.I),
    "number_precedes_metric": re.compile(
        r"\d{1,3}(?:\.\d+)?\s?%\s*(?:for\s+|in\s+)?(?:sensitivit|specificit)", re.I),
    "multiple_percentages": None,    # computed: four or more percentages in the sentence
}


def features_of(quote: str) -> list:
    """The shape of a source sentence, as a sorted list of feature names."""
    q = quote or ""
    out = []
    for name, rx in FEATURES.items():
        if rx is not None and rx.search(q):
            out.append(name)
    # reversed order: specificity is named before sensitivity
    si = q.lower().find("sensitiv")
    pi = q.lower().find("specific")
    if si >= 0 and pi >= 0 and pi < si:
        out.append("reversed_order")
    if len(re.findall(r"\d{1,3}(?:\.\d+)?\s?%", q)) >= 4:
        out.append("multiple_percentages")
    return sorted(out)


def signature(quote: str) -> str:
    f = features_of(quote)
    return "+".join(f) if f else "plain"


def classify(machine: dict, corrected: dict, quote: str) -> str:
    """Why was the machine wrong? Derived from what changed and the sentence shape."""
    mq = quote or ""
    ms, mp = (machine or {}).get("sens"), (machine or {}).get("spec")
    cs, cp = (corrected or {}).get("sens"), (corrected or {}).get("spec")

    # swapped: the machine's pair is the corrected pair in reverse
    if ms is not None and mp is not None and cs is not None and cp is not None:
        if abs(ms - cp) < 0.01 and abs(mp - cs) < 0.01:
            return "metric_inversion"
    if FEATURES["range_form"].search(mq) and (cs is None or cp is None):
        return "range_split"
    if FEATURES["ci_present"].search(mq) and (
            (ms is not None and abs(ms - 95) < 0.01) or
            (mp is not None and abs(mp - 95) < 0.01) or
            (ms is not None and abs(ms - 90) < 0.01) or
            (mp is not None and abs(mp - 90) < 0.01)):
        return "ci_leakage"
    if FEATURES["third_metric_near"].search(mq):
        return "adjacent_metric_theft"
    if FEATURES["ci_present"].search(mq):
        return "ci_leakage"
    return "unclassified"


# ------------------------------------------------------------------------------ mining
def mine_corrections(db, run_id=None) -> list:
    """Every case in the ledger where a human changed what the agent had proposed.

    This is the whole point: the training set is the audit trail. Nothing extra had to be
    collected, and every example is already attributable to the human who corrected it.
    """
    q = {"applied": True, "effect": {"$ne": None}, "effect.collection": "records"}
    if run_id:
        q["runId"] = run_id
    out = []
    for e in db.ledger.find(q, sort=[("seq", 1)]):
        after = (e["effect"] or {}).get("after") or {}
        prov = after.get("provenance") or {}
        machine = prov.get("machineValue") or {}
        if not machine:
            continue
        final = {"sens": after.get("sensitivity"), "spec": after.get("specificity")}
        changed = (machine.get("sens") != final["sens"]
                   or machine.get("spec") != final["spec"])
        quote = prov.get("sourceQuote") or ""
        out.append({
            "ledgerId": e["_id"],
            "seq": int(e["seq"]),
            "recordId": (e["effect"] or {}).get("recordId"),
            "correctedBy": e.get("onBehalfOf"),
            "machine": machine,
            "final": final,
            "changed": bool(changed),
            "quote": quote,
            "signature": signature(quote),
            "features": features_of(quote),
            "failureMode": classify(machine, final, quote) if changed else None,
        })
    return out


def build_calibration(db, corrections) -> list:
    """Observed accuracy per sentence FEATURE, mined from corrections.

    An earlier version calibrated on the full feature conjunction, which learned nothing
    that transferred: every real sentence carries a slightly different combination, so no
    shape ever recurred and no rule was ever proposed. The signal that generalises lives in
    the individual features. `range_form` appeared in three different wrong readings while
    the conjunctions containing it were each unique.

    So each feature gets its own row, and a rule fires when a claim's source sentence
    carries that feature. Conjunctions are still recorded for diagnosis, marked
    `scope: "conjunction"`, but only features are proposed as rules.
    """
    per_feature, per_sig = {}, {}

    def bump(bucket, key, feats, c):
        g = bucket.setdefault(key, {
            "key": key, "features": feats, "observed": 0, "corrected": 0,
            "failureModes": {}, "learnedFrom": [],
        })
        g["observed"] += 1
        g["learnedFrom"].append(c["ledgerId"])
        if c["changed"]:
            g["corrected"] += 1
            fm = c["failureMode"] or "unclassified"
            g["failureModes"][fm] = g["failureModes"].get(fm, 0) + 1

    for c in corrections:
        for f in (c["features"] or ["plain"]):
            bump(per_feature, f, [f], c)
        bump(per_sig, c["signature"], c["features"], c)

    rows = []
    for scope, bucket in (("feature", per_feature), ("conjunction", per_sig)):
        for key, g in bucket.items():
            acc = (g["observed"] - g["corrected"]) / g["observed"]
            doc = {
                "_id": f"cal-{scope}-" + re.sub(r"[^A-Za-z0-9_+-]+", "_", key)[:180],
                "signature": key,
                "scope": scope,
                "features": g["features"],
                "observed": int(g["observed"]),
                "corrected": int(g["corrected"]),
                "accuracy": float(round(acc, 4)),
                "failureModes": g["failureModes"],
                "learnedFrom": g["learnedFrom"][:25],
                "updatedAt": datetime.now(timezone.utc),
            }
            db.calibration.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            rows.append(doc)
    return sorted(rows, key=lambda r: (r["scope"] != "feature", r["accuracy"]))


# ------------------------------------------------------------------- rule proposal
def propose_rules(db, *, actor, run_id, min_observed=2, max_accuracy=0.5):
    """Propose a tightened rule for every shape the agent demonstrably gets wrong.

    The proposal goes through the same gate as any other action: an intent, a decision, a
    ledger entry. It is written INACTIVE, so proposing is not enacting.
    """
    proposed = []
    for cal in db.calibration.find({"scope": "feature",
                                    "observed": {"$gte": min_observed},
                                    "accuracy": {"$lte": max_accuracy}},
                                   sort=[("accuracy", 1), ("observed", -1)]):
        rule_id = "learned_escalate_" + re.sub(r"[^a-z0-9]+", "_",
                                               cal["signature"].lower())[:48]
        existing = db.policies.find_one({"ruleId": rule_id})
        version = (existing["version"] + 1) if existing else 1
        modes = ", ".join(sorted(cal["failureModes"] or {"unclassified": 1}))
        payload = {
            "ruleId": rule_id,
            "version": version,
            "effect": "escalate",
            "predicate": "has_known_failure_feature",
            "params": {"feature": cal["signature"]},
            "reason": (f"Source sentences carrying {cal['signature']!r} have produced a "
                       f"wrong figure "
                       f"in {cal['corrected']} of {cal['observed']} observations "
                       f"({int(cal['accuracy']*100)}% accurate), by "
                       f"{modes}. A human reads the sentence instead."),
            "grounding": (f"Learned from {len(cal.get('learnedFrom') or [])} entries in "
                          f"the audit ledger. Calibrated on the single feature "
                          f"{cal['signature']!r}, because full feature conjunctions are "
                          f"unique per sentence and do not transfer."),
            "observedAccuracy": cal["accuracy"],
            "indication": "lung", "jurisdiction": "US",
        }
        intent = policy.record_intent(db, runId=run_id, actor=actor,
                                      action="policy.propose", collection="policies",
                                      recordId=f"pol:{rule_id}:{version}", payload=payload)
        decision = policy.evaluate(db, intent)
        if decision["verdict"] != "allow":
            proposed.append({"ruleId": rule_id, "verdict": decision["verdict"],
                             "reason": decision["reason"], "policyId": None})
            continue

        doc = {
            "_id": f"pol:{rule_id}:{version}",
            "ruleId": rule_id, "version": version,
            "appliesTo": ["claim.write"],
            "effect": "escalate",
            "predicate": "has_known_failure_feature",
            "params": {"feature": cal["signature"]},
            "reason": payload["reason"],
            "grounding": payload["grounding"],
            "active": False,                      # proposing is not enacting
            "proposedBy": actor,
            "learnedFrom": cal.get("learnedFrom") or [],
            "observedAccuracy": cal["accuracy"],
            "approvedBy": None,
            "createdAt": datetime.now(timezone.utc),
        }
        db.policies.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        ledger.append(db, runId=run_id, action="policy.propose", actor=actor,
                      verdict="allow", applied=True, intentId=intent["_id"],
                      decisionId=decision["_id"],
                      reason=f"proposed inactive rule {rule_id} v{version} from "
                             f"{cal['observed']} observations",
                      effect={"collection": "policies", "recordId": doc["_id"],
                              "before": None, "after": {"active": False,
                                                        "effect": "escalate"}})
        proposed.append({"ruleId": rule_id, "verdict": "allow",
                         "policyId": doc["_id"], "accuracy": cal["accuracy"],
                         "observed": cal["observed"], "corrected": cal["corrected"]})
    return proposed


def activate_rule(db, policy_id, *, by, run_id, note=None):
    """A human activates a proposed rule. The agent cannot do this for itself."""
    human = db.identities.find_one({"_id": by})
    if not human or human.get("kind") != "human":
        raise PermissionError(f"{by!r} is not a human identity")
    if "policy.activate" not in (human.get("allowedActions") or []):
        raise PermissionError(f"{by!r} is not permitted to activate a policy")
    before = db.policies.find_one({"_id": policy_id})
    if not before:
        raise ValueError(f"no such policy {policy_id}")
    db.policies.update_one({"_id": policy_id},
                           {"$set": {"active": True, "approvedBy": by}})
    ledger.append(db, runId=run_id, action="policy.activate", actor=by,
                  verdict="allow", applied=True,
                  reason=(f"activated learned rule {before['ruleId']} "
                          f"v{before['version']}" + (f": {note}" if note else "")),
                  effect={"collection": "policies", "recordId": policy_id,
                          "before": {"active": False, "approvedBy": None},
                          "after": {"active": True, "approvedBy": by}})
    return db.policies.find_one({"_id": policy_id})
