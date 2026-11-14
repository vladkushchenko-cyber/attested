"""Create collections with their validators, build indexes, seed identities and policy.

Every seeded policy rule carries a `grounding` string: the measurement that made the rule
necessary. A rule nobody can justify is a rule nobody will maintain, and at review time the
grounding is the first thing a compliance reader asks for.
"""
import sys
from datetime import datetime, timezone

from .db import client, DB_NAME, require_replica_set, uri
from .schema import COLLECTIONS, INDEXES

NOW = datetime.now(timezone.utc)

IDENTITIES = [
    {
        "_id": "agent:curator-v3",
        "kind": "agent",
        "displayName": "Evidence curation agent v3",
        "model": "extraction-pipeline-3.1",
        "allowedActions": ["claim.write", "rung.assert", "record.create",
                           "approval.publish", "alert.send"],
        "scope": {"indications": ["lung"], "jurisdictions": ["US", "CA"],
                  "maxActionsPerRun": 25},
        "active": True, "createdAt": NOW,
    },
    {
        # Deliberately narrower: used to demonstrate scope refusal.
        "_id": "agent:screening-scout",
        "kind": "agent",
        "displayName": "Screening scout agent (breast only)",
        "model": "extraction-pipeline-3.1",
        "allowedActions": ["claim.write", "record.create"],
        "scope": {"indications": ["breast"], "jurisdictions": ["US"],
                  "maxActionsPerRun": 5},
        "active": True, "createdAt": NOW,
    },
    {
        # No write actions at all: used to demonstrate action refusal.
        "_id": "agent:reader",
        "kind": "agent",
        "displayName": "Read-only landscape agent",
        "model": "extraction-pipeline-3.1",
        "allowedActions": ["alert.send"],
        "scope": {"indications": ["lung"], "jurisdictions": ["US", "CA"],
                  "maxActionsPerRun": 50},
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "human:ester",
        "kind": "human",
        "displayName": "Ester C, diagnostics subject expert",
        "model": None,
        "allowedActions": ["claim.verify", "claim.write", "rung.assert",
                           "record.create", "run.halt", "revert.apply"],
        "scope": {"indications": ["lung", "breast", "colorectal"],
                  "jurisdictions": ["US", "CA", "EU"], "maxActionsPerRun": None},
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "human:compliance",
        "kind": "human",
        "displayName": "Medical Affairs compliance reviewer",
        "model": None,
        "allowedActions": ["run.halt", "revert.apply", "claim.verify"],
        "scope": {"indications": ["lung", "breast", "colorectal"],
                  "jurisdictions": ["US", "CA", "EU"], "maxActionsPerRun": None},
        "active": True, "createdAt": NOW,
    },
]

POLICIES = [
    {
        "_id": "pol:require_source_quote:1", "ruleId": "require_source_quote", "version": 1,
        "appliesTo": ["claim.write", "rung.assert", "record.create"],
        "effect": "deny", "predicate": "missing_source_quote", "params": {"minLength": 25},
        "reason": "A figure may not be written without the verbatim sentence it was read from.",
        "grounding": "Reviewers judge an assay on evidence provenance, not on a headline "
                     "number. An uncited figure is worse than a blank, because a blank "
                     "prompts a check and a bare number does not.",
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "pol:reject_unverifiable_fragment:1", "ruleId": "reject_unverifiable_fragment",
        "version": 1, "appliesTo": ["claim.write"],
        "effect": "deny", "predicate": "fragment_unverified", "params": {},
        "reason": "The quoted fragment could not be located in the retained source text, so "
                  "the figure is not verifiable at source.",
        "grounding": "94 of 859 real records carry a performance fragment that is not a "
                     "substring of the retained excerpt. Those figures cannot be confirmed "
                     "without re-fetching the source.",
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "pol:escalate_ambiguous_claim:1", "ruleId": "escalate_ambiguous_claim",
        "version": 1, "appliesTo": ["claim.write"],
        "effect": "escalate", "predicate": "claim_ambiguous", "params": {},
        "reason": "More than one candidate figure appears in the source, so a single value "
                  "cannot be chosen without a human reading the sentence.",
        "grounding": "46 of 859 records carry more than one distinct candidate sensitivity. "
                     "Hand audit found inversions and figures stolen from an adjacent "
                     "metric in exactly these cases.",
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "pol:deny_rung4_from_literature:1", "ruleId": "deny_rung4_from_literature",
        "version": 1, "appliesTo": ["rung.assert", "record.create", "claim.write"],
        "effect": "deny", "predicate": "rung_from_literature", "params": {"minRung": 4},
        "reason": "A regulatory or reimbursement rung may only be asserted from a regulator "
                  "or an HTA body, never inferred from literature.",
        "grounding": "Regulatory vocabulary inside research abstracts is about 85 per cent "
                     "false positive. 'FDA-approved' in an abstract almost always describes "
                     "the therapy, not the assay. Also measured: 'uptake' meaning "
                     "Fc-mediated cellular uptake, and 'CMS' meaning screening eligibility "
                     "criteria rather than a coverage decision.",
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "pol:require_human_for_rankable:1", "ruleId": "require_human_for_rankable",
        "version": 1, "appliesTo": ["claim.write", "claim.verify"],
        "effect": "escalate", "predicate": "rankable_by_agent", "params": {},
        "reason": "Only a human may mark a figure rankable, because machine-read figures are "
                  "about half wrong.",
        "grounding": "Hand audit of 20 machine-parsed sensitivity and specificity pairs: 10 "
                     "correct, 7 wrong, 3 that should have been refused. Measured accuracy "
                     "50 per cent. Failure modes: metric inversion, theft from an adjacent "
                     "metric, confidence-interval leakage, ranges split instead of refused.",
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "pol:deny_administrative_as_approval:1",
        "ruleId": "deny_administrative_as_approval", "version": 1,
        "appliesTo": ["approval.publish", "alert.send"],
        "effect": "deny", "predicate": "administrative_supplement", "params": {},
        "reason": "An administrative supplement is not an approval event and may not be "
                  "published or alerted on.",
        "grounding": "12 of 25 openFDA PMA supplement records are administrative: facility "
                     "relocations, fermenter replacements, alternate suppliers, reduced "
                     "environmental monitoring. Unfiltered, an approval alert fires on "
                     "manufacturing paperwork.",
        "active": True, "createdAt": NOW,
    },
    {
        "_id": "pol:deny_value_out_of_range:1", "ruleId": "deny_value_out_of_range",
        "version": 1, "appliesTo": ["claim.write"],
        "effect": "deny", "predicate": "value_out_of_range", "params": {},
        "reason": "A percentage outside 0 to 100 is a parse artefact, not a result.",
        "grounding": "Defence in depth: the records validator also bounds these fields, so a "
                     "bad value is refused twice, once by policy and once by the server.",
        "active": True, "createdAt": NOW,
    },
]


def bootstrap(drop: bool = False) -> None:
    c = client()
    require_replica_set(c)
    db = c[DB_NAME]
    print(f"target: {uri().split('@')[-1]}  db: {DB_NAME}")

    if drop:
        for name in COLLECTIONS:
            db.drop_collection(name)
        print("dropped existing collections")

    existing = set(db.list_collection_names())
    for name, validator in COLLECTIONS.items():
        if name in existing:
            # collMod applies the validator to a collection that already exists, which is
            # how a policy change reaches a live system without a migration window.
            db.command({"collMod": name, "validator": validator,
                        "validationLevel": "strict", "validationAction": "error"})
            print(f"  collMod  {name:12s} validator updated")
        else:
            db.create_collection(name, validator=validator,
                                 validationLevel="strict", validationAction="error")
            print(f"  created  {name:12s} with $jsonSchema, validationAction=error")

    for name, specs in INDEXES.items():
        for keys, opts in specs:
            db[name].create_index(keys, **opts)
    print(f"indexes built on {len(INDEXES)} collections")

    for ident in IDENTITIES:
        db.identities.replace_one({"_id": ident["_id"]}, ident, upsert=True)
    print(f"seeded {len(IDENTITIES)} identities "
          f"({sum(1 for i in IDENTITIES if i['kind']=='agent')} agents, "
          f"{sum(1 for i in IDENTITIES if i['kind']=='human')} humans)")

    for pol in POLICIES:
        db.policies.replace_one({"_id": pol["_id"]}, pol, upsert=True)
    print(f"seeded {len(POLICIES)} policy rules, each with a grounding measurement")

    print("\nvalidators active on:", ", ".join(sorted(COLLECTIONS)))


if __name__ == "__main__":
    bootstrap(drop="--drop" in sys.argv)
