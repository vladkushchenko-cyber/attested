"""Append-only, hash-chained audit ledger.

Two properties matter and both are enforced rather than asserted:

1. **Append-only.** Nothing in the application updates or deletes a ledger entry. A
   change-stream watchdog (`control.watch_ledger_tamper`) observes the collection and writes
   a `tamper` entry if an update or delete ever occurs, so an out-of-band modification is
   recorded rather than silent.

2. **Chained.** Each entry hashes its own canonical content together with the previous
   entry's hash. Editing, reordering or removing an entry breaks the chain at that point,
   and `verify_chain` reports the first break. This is what makes the ledger evidence rather
   than a log.
"""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64

# Fields that participate in the hash, in a fixed order. Deliberately excludes `hash`
# itself and excludes `_id`, which is derived from seq.
HASHED_FIELDS = [
    "seq", "runId", "action", "actor", "onBehalfOf", "verdict", "applied",
    "intentId", "decisionId", "policyId", "policyVersion", "reason",
    "inputs", "effect", "compensates", "prevHash", "ts",
]


def ms_precision(dt: datetime) -> datetime:
    """Truncate to milliseconds.

    BSON stores datetimes at millisecond precision, so a timestamp hashed with microseconds
    at write time will not match the value read back, and every chain verification would
    fail on untampered data. Truncating before both hashing and storing makes the round trip
    exact.
    """
    return dt.astimezone(timezone.utc).replace(microsecond=(dt.microsecond // 1000) * 1000)


def _norm(v):
    """Recursively normalise a value for hashing.

    Datetimes appear nested inside `effect.before` and `effect.after` (a record document
    carries `updatedAt`), so normalisation has to walk the whole structure. Handling only
    the top level makes every entry with an effect fail verification.
    """
    if isinstance(v, datetime):
        return ms_precision(v).isoformat()
    if isinstance(v, dict):
        return {k: _norm(v[k]) for k in sorted(v)}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, float):
        # repr of a float is stable in Python 3, but round-tripping through BSON double
        # and back is exact, so no rounding is needed - only a consistent representation.
        return format(v, ".10g")
    return v


def _canonical(entry: dict) -> str:
    """Stable serialisation. Sorted keys, ISO timestamps, no incidental whitespace."""
    payload = {k: _norm(entry.get(k)) for k in HASHED_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()


def head(db):
    return db.ledger.find_one(sort=[("seq", -1)])


def append(db, *, runId, action, actor, verdict, reason,
           onBehalfOf=None, applied=False, intentId=None, decisionId=None,
           policyId=None, policyVersion=None, inputs=None, effect=None,
           compensates=None, session=None) -> dict:
    """Append one entry, chaining it to the current head.

    Not a transaction by itself: callers that must tie a ledger entry to a record write
    wrap both in a session (see actions.apply_claim_write).
    """
    prev = db.ledger.find_one(sort=[("seq", -1)], session=session)
    seq = (prev["seq"] + 1) if prev else 0
    e = {
        "_id": f"led-{seq:08d}",
        "seq": int(seq),
        "runId": runId,
        "action": action,
        "actor": actor,
        "onBehalfOf": onBehalfOf,
        "verdict": verdict,
        "applied": bool(applied),
        "intentId": intentId,
        "decisionId": decisionId,
        "policyId": policyId,
        "policyVersion": policyVersion,
        "reason": reason,
        "inputs": inputs,
        "effect": effect,
        "compensates": compensates,
        "prevHash": prev["hash"] if prev else GENESIS,
        "ts": ms_precision(datetime.now(timezone.utc)),
    }
    e["hash"] = entry_hash(e)
    # bson needs an int64 for the `long` bsonType in the validator
    from bson.int64 import Int64
    e["seq"] = Int64(e["seq"])
    db.ledger.insert_one(e, session=session)
    return e


def verify_chain(db) -> dict:
    """Walk the whole chain. Returns the first break, if any."""
    prev_hash = GENESIS
    n = 0
    for e in db.ledger.find(sort=[("seq", 1)]):
        n += 1
        if e["prevHash"] != prev_hash:
            return {"ok": False, "brokenAt": e["seq"], "entries": n,
                    "why": "prevHash does not match the previous entry's hash"}
        recomputed = entry_hash(e)
        if recomputed != e["hash"]:
            return {"ok": False, "brokenAt": e["seq"], "entries": n,
                    "why": "entry content does not match its stored hash"}
        prev_hash = e["hash"]
    return {"ok": True, "entries": n, "head": prev_hash[:16] + "..."}


def reconstruct(db, record_id: str) -> list:
    """Everything that ever happened to one record, in order.

    This is the reviewer's question: how did this record come to say what it says, and on
    whose authority. One query, ordered, with the value before each change.
    """
    out = []
    for e in db.ledger.find({"effect.recordId": record_id}, sort=[("seq", 1)]):
        out.append({
            "seq": int(e["seq"]),
            "ts": e["ts"],
            "action": e["action"],
            "actor": e["actor"],
            "onBehalfOf": e.get("onBehalfOf"),
            "verdict": e["verdict"],
            "applied": e.get("applied"),
            "rule": (f'{e.get("policyId")}@v{e.get("policyVersion")}'
                     if e.get("policyId") else None),
            "reason": e["reason"],
            "before": (e.get("effect") or {}).get("before"),
            "after": (e.get("effect") or {}).get("after"),
            "sourceQuote": (e.get("inputs") or {}).get("sourceQuote"),
            "compensates": e.get("compensates"),
        })
    return out
