"""Collection validators.

These are the load-bearing part of the system. Policy evaluated in application code is a
rule the agent is asked to respect; a `$jsonSchema` validator with
`validationAction: "error"` is a rule the agent cannot get around, even holding a raw driver
connection. Everything the agent must never do is encoded here as well as in `policies`.

MongoDB's `$jsonSchema` implements a subset of JSON Schema draft 4: `allOf`, `anyOf`,
`oneOf`, `not`, `enum`, `required`, numeric bounds and string lengths are available;
draft-7 `if/then/else` is not. Conditional rules are therefore expressed as `anyOf` over
mutually exclusive branches, which is why each conditional field is also `required` - an
absent field would otherwise satisfy a `properties`-only branch silently.
"""

# --------------------------------------------------------------------------- identities
IDENTITIES = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "A principal that can act, or on whose authority an agent can act.",
        "required": ["_id", "kind", "displayName", "allowedActions", "scope", "active"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string", "minLength": 3},
            "kind": {"enum": ["agent", "human", "service"]},
            "displayName": {"bsonType": "string", "minLength": 2},
            "model": {"bsonType": ["string", "null"]},
            "allowedActions": {
                "bsonType": "array", "minItems": 1,
                "items": {"enum": [
                    "claim.write", "rung.assert", "record.create",
                    "approval.publish", "alert.send", "run.halt",
                    "claim.verify", "revert.apply",
                ]},
            },
            # Scope is enforced before the policy body is read. An empty indication list
            # means "no indication", not "all indications" - deny by default.
            "scope": {
                "bsonType": "object",
                "required": ["indications", "jurisdictions"],
                "additionalProperties": False,
                "properties": {
                    "indications": {"bsonType": "array", "items": {"bsonType": "string"}},
                    "jurisdictions": {"bsonType": "array", "items": {"bsonType": "string"}},
                    "maxActionsPerRun": {"bsonType": ["int", "null"]},
                },
            },
            "active": {"bsonType": "bool"},
            "createdAt": {"bsonType": "date"},
        },
    }
}

# ----------------------------------------------------------------------------- policies
POLICIES = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "A versioned policy rule. Immutable: a change is a new version.",
        "required": ["_id", "ruleId", "version", "appliesTo", "effect",
                     "predicate", "reason", "grounding", "active"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "ruleId": {"bsonType": "string", "minLength": 3},
            "version": {"bsonType": "int", "minimum": 1},
            "appliesTo": {
                "bsonType": "array", "minItems": 1,
                "items": {"enum": [
                    "claim.write", "rung.assert", "record.create",
                    "approval.publish", "alert.send", "claim.verify", "revert.apply", "*",
                ]},
            },
            "effect": {"enum": ["deny", "escalate", "allow"]},
            # A named predicate resolved in policy.py. Keeping the predicate a name rather
            # than embedded code means a policy document can be reviewed by a human who is
            # not reading Python, and cannot smuggle in executable logic.
            "predicate": {"bsonType": "string", "minLength": 2},
            "params": {"bsonType": ["object", "null"]},
            "reason": {"bsonType": "string", "minLength": 10},
            # Why this rule exists, as a measurement. A rule nobody can justify is a rule
            # nobody will maintain.
            "grounding": {"bsonType": "string", "minLength": 10},
            "active": {"bsonType": "bool"},
            "createdAt": {"bsonType": "date"},
        },
    }
}

# ------------------------------------------------------------------------------ intents
INTENTS = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "What the agent wants to do, written before it is permitted to do it.",
        "required": ["_id", "runId", "actor", "action", "target", "payload", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "runId": {"bsonType": "string"},
            "actor": {"bsonType": "string"},
            "action": {"enum": [
                "claim.write", "rung.assert", "record.create",
                "approval.publish", "alert.send", "claim.verify", "revert.apply",
            ]},
            "target": {
                "bsonType": "object",
                "required": ["collection", "recordId"],
                "properties": {
                    "collection": {"bsonType": "string"},
                    "recordId": {"bsonType": "string"},
                },
            },
            "payload": {"bsonType": "object"},
            "ts": {"bsonType": "date"},
        },
    }
}

# ---------------------------------------------------------------------------- decisions
DECISIONS = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "The verdict on an intent. Naming the rule that fired is mandatory.",
        "required": ["_id", "intentId", "runId", "verdict", "evaluated", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "intentId": {"bsonType": "string"},
            "runId": {"bsonType": "string"},
            "verdict": {"enum": ["allow", "deny", "escalate"]},
            # Every rule considered, not only the one that fired, so a reviewer can see
            # what was checked rather than inferring it.
            "evaluated": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["ruleId", "version", "matched"],
                    "properties": {
                        "ruleId": {"bsonType": "string"},
                        "version": {"bsonType": "int"},
                        "matched": {"bsonType": "bool"},
                        "effect": {"bsonType": ["string", "null"]},
                        "reason": {"bsonType": ["string", "null"]},
                    },
                },
            },
            "decidingRuleId": {"bsonType": ["string", "null"]},
            "decidingVersion": {"bsonType": ["int", "null"]},
            "reason": {"bsonType": "string"},
            # Set when an escalation is resolved by a human. The ledger schema requires
            # this to be present for any applied action whose verdict was `escalate`.
            "resolution": {
                "bsonType": ["object", "null"],
                "required": ["by", "outcome", "ts"],
                "properties": {
                    "by": {"bsonType": "string"},
                    "outcome": {"enum": ["approved", "rejected"]},
                    "note": {"bsonType": ["string", "null"]},
                    "ts": {"bsonType": "date"},
                },
            },
            "ts": {"bsonType": "date"},
        },
    }
}

# ------------------------------------------------------------------------------- ledger
LEDGER = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Append-only, hash-chained audit. Authority is not optional.",
        "required": ["_id", "seq", "runId", "action", "actor", "verdict",
                     "reason", "prevHash", "hash", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "seq": {"bsonType": "long", "minimum": 0},
            "runId": {"bsonType": "string"},
            "action": {"bsonType": "string"},
            "actor": {"bsonType": "string"},
            # The human whose authority was borrowed. See the anyOf below: for an applied
            # action that was escalated, this is mandatory.
            "onBehalfOf": {"bsonType": ["string", "null"]},
            "verdict": {"enum": ["allow", "deny", "escalate", "halt", "tamper", "revert"]},
            "applied": {"bsonType": "bool"},
            "intentId": {"bsonType": ["string", "null"]},
            "decisionId": {"bsonType": ["string", "null"]},
            "policyId": {"bsonType": ["string", "null"]},
            "policyVersion": {"bsonType": ["int", "null"]},
            "reason": {"bsonType": "string", "minLength": 3},
            "inputs": {"bsonType": ["object", "null"]},
            # before/after is what makes rollback possible. A ledger that records only the
            # new value can prove what happened but cannot undo it.
            "effect": {
                "bsonType": ["object", "null"],
                "properties": {
                    "collection": {"bsonType": "string"},
                    "recordId": {"bsonType": "string"},
                    "before": {"bsonType": ["object", "null"]},
                    "after": {"bsonType": ["object", "null"]},
                },
            },
            "compensates": {"bsonType": ["string", "null"]},
            "prevHash": {"bsonType": "string", "minLength": 64},
            "hash": {"bsonType": "string", "minLength": 64},
            "ts": {"bsonType": "date"},
        },
        "anyOf": [
            # not an applied escalation -> onBehalfOf may be null
            {"properties": {"verdict": {"enum": ["deny", "escalate", "halt", "tamper", "allow", "revert"]},
                            "applied": {"enum": [False]}}},
            {"properties": {"verdict": {"enum": ["deny", "halt", "tamper"]}}},
            {"properties": {"verdict": {"enum": ["allow", "revert"]}, "applied": {"enum": [True]}}},
            # an applied action that came from an escalation MUST name the human
            {"properties": {"verdict": {"enum": ["escalate"]}, "applied": {"enum": [True]},
                            "onBehalfOf": {"bsonType": "string", "minLength": 3}},
             "required": ["onBehalfOf"]},
        ],
    }
}

# ------------------------------------------------------------------------------ records
# The record of truth. This validator is the gate that cannot be bypassed.
RECORDS = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "A curated evidence record. The server refuses anything unprovenanced.",
        "required": ["_id", "assay", "indication", "rung", "authority",
                     "rankable", "provenance", "updatedAt"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "assay": {"bsonType": "string", "minLength": 2},
            "indication": {"bsonType": "string", "minLength": 3},
            "modality": {"bsonType": ["string", "null"]},
            "rung": {"bsonType": "int", "minimum": 1, "maximum": 5},
            # Performance figures. Bounded, so a mis-parse that yields 168% cannot land.
            "sensitivity": {"bsonType": ["double", "null"], "minimum": 0, "maximum": 100},
            "specificity": {"bsonType": ["double", "null"], "minimum": 0, "maximum": 100},
            "cohortN": {"bsonType": ["int", "null"], "minimum": 1},
            "authority": {"enum": ["human", "agent"]},
            # Only a human may mark a figure rankable: machine accuracy is 50%.
            "rankable": {"bsonType": "bool"},
            "provenance": {
                "bsonType": "object",
                "required": ["sourceId", "sourceType", "sourceQuote", "fragmentVerified"],
                "additionalProperties": False,
                "properties": {
                    "sourceId": {"bsonType": "string", "minLength": 3},
                    "sourceType": {"enum": ["literature", "congress", "trial",
                                            "regulator", "hta"]},
                    # No figure without the sentence it came from.
                    "sourceQuote": {"bsonType": "string", "minLength": 25},
                    "sourceUrl": {"bsonType": ["string", "null"]},
                    # False when the quoted fragment could not be located in the retained
                    # source text. 94 of 859 real records are in this state.
                    "fragmentVerified": {"bsonType": "bool"},
                    "machineValue": {"bsonType": ["object", "null"]},
                },
            },
            "ledgerSeq": {"bsonType": ["long", "null"]},
            "updatedAt": {"bsonType": "date"},
        },
        "allOf": [
            {
                "title": "A rung of 4 or 5 may only come from a regulator or an HTA body.",
                "anyOf": [
                    {"properties": {"rung": {"bsonType": "int", "maximum": 3}}},
                    {"properties": {
                        "rung": {"bsonType": "int", "minimum": 4},
                        "provenance": {"bsonType": "object", "properties": {
                            "sourceType": {"enum": ["regulator", "hta"]}}},
                    }},
                ],
            },
            {
                "title": "Only a human may mark a figure rankable.",
                "anyOf": [
                    {"properties": {"rankable": {"enum": [False]}}},
                    {"properties": {"rankable": {"enum": [True]},
                                    "authority": {"enum": ["human"]}}},
                ],
            },
            {
                "title": "An unverifiable fragment may not be marked rankable.",
                "anyOf": [
                    {"properties": {"rankable": {"enum": [False]}}},
                    {"properties": {"rankable": {"enum": [True]},
                                    "provenance": {"bsonType": "object", "properties": {
                                        "fragmentVerified": {"enum": [True]}}}}},
                ],
            },
        ],
    }
}

# ----------------------------------------------------------------------------- controls
CONTROLS = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Per-run control channel. Watched by a change stream.",
        "required": ["_id", "runId", "command", "issuedBy", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "runId": {"bsonType": "string"},
            "command": {"enum": ["run", "halt", "resume"]},
            "issuedBy": {"bsonType": "string", "minLength": 3},
            "note": {"bsonType": ["string", "null"]},
            "ts": {"bsonType": "date"},
        },
    }
}

# --------------------------------------------------------------------------------- runs
RUNS = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "actor", "state", "startedAt", "counters"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "actor": {"bsonType": "string"},
            "state": {"enum": ["running", "halted", "completed", "failed", "reverted"]},
            "startedAt": {"bsonType": "date"},
            "endedAt": {"bsonType": ["date", "null"]},
            "counters": {"bsonType": "object"},
            "note": {"bsonType": ["string", "null"]},
        },
    }
}

# ------------------------------------------------------------------------------- outbox
# Downstream effects. `sent` and `confirmed` are separate so an effect that was only
# dispatched is distinguishable from one that is known to have taken effect.
OUTBOX = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "runId", "channel", "state", "payload", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},
            "runId": {"bsonType": "string"},
            "channel": {"enum": ["curated-dataset", "analyst-alert"]},
            "state": {"enum": ["pending", "sent", "confirmed", "failed", "compensated"]},
            "payload": {"bsonType": "object"},
            "confirmation": {"bsonType": ["object", "null"]},
            "ledgerSeq": {"bsonType": ["long", "null"]},
            "ts": {"bsonType": "date"},
        },
    }
}

COLLECTIONS = {
    "identities": IDENTITIES,
    "policies": POLICIES,
    "intents": INTENTS,
    "decisions": DECISIONS,
    "ledger": LEDGER,
    "records": RECORDS,
    "controls": CONTROLS,
    "runs": RUNS,
    "outbox": OUTBOX,
}

INDEXES = {
    "identities": [([("kind", 1)], {}), ([("active", 1)], {})],
    "policies": [([("ruleId", 1), ("version", -1)], {"unique": True}),
                 ([("active", 1), ("appliesTo", 1)], {})],
    "intents": [([("runId", 1), ("ts", 1)], {})],
    "decisions": [([("intentId", 1)], {"unique": True}), ([("runId", 1)], {})],
    "ledger": [([("seq", 1)], {"unique": True}),
               ([("runId", 1), ("seq", 1)], {}),
               ([("effect.recordId", 1), ("seq", 1)], {})],
    "records": [([("assay", 1), ("indication", 1)], {}), ([("rankable", 1)], {})],
    "controls": [([("runId", 1), ("ts", -1)], {})],
    "runs": [([("state", 1)], {})],
    "outbox": [([("runId", 1), ("state", 1)], {})],
}
