"""The control plane, built on change streams.

Two watchers:

* `HaltWatcher` tails `controls` and sets an in-process `threading.Event` the moment a
  `halt` lands for its run. The agent checks that event between steps, so the run stops in
  flight without the process being killed and without leaving a half-written record. A
  cooperative stop is what you want in an audited system: a `SIGKILL` leaves no ledger
  entry explaining why the run ended.

* `TamperWatcher` tails `ledger` for `update`, `replace` or `delete` operations. The
  application never issues any of those, so one occurring means something modified the audit
  out of band. The watcher writes a `tamper` entry, which is itself chained, so the
  modification is recorded even though it could not be prevented.

A change stream needs a replica set. Atlas is one; the local deployment is a single-node
replica set for the same reason.
"""
import threading
from datetime import datetime, timezone

from . import ledger


class HaltWatcher:
    """Watch `controls` for a halt on one run."""

    def __init__(self, db, run_id):
        self.db = db
        self.run_id = run_id
        self.halted = threading.Event()
        self.halt_doc = None
        self._thread = None
        self._stop = threading.Event()
        self.ready = threading.Event()

    def _run(self):
        pipeline = [{"$match": {
            "operationType": {"$in": ["insert", "replace", "update"]},
            "fullDocument.runId": self.run_id,
            "fullDocument.command": "halt",
        }}]
        # `resume_after` is omitted deliberately: this watcher is for a live run. A durable
        # audit tailer would persist the resume token and restart from it.
        with self.db.controls.watch(pipeline, full_document="updateLookup",
                                    max_await_time_ms=500) as stream:
            self.ready.set()
            while not self._stop.is_set():
                change = stream.try_next()
                if change is None:
                    continue
                self.halt_doc = change.get("fullDocument")
                self.halted.set()
                return

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait until the stream is actually open, otherwise a halt issued immediately
        # afterwards could land before the watcher is listening.
        self.ready.wait(timeout=10)
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def check(self):
        """Called by the agent between steps. Raises if a halt has landed."""
        if self.halted.is_set():
            raise Halted(self.halt_doc or {})


class Halted(Exception):
    def __init__(self, doc):
        self.doc = doc
        super().__init__(f"halted by {doc.get('issuedBy', 'unknown')}: "
                         f"{doc.get('note') or 'no reason given'}")


class TamperWatcher:
    """Watch `ledger` for any modification. The application never makes one."""

    def __init__(self, db):
        self.db = db
        self.events = []
        self._thread = None
        self._stop = threading.Event()
        self.ready = threading.Event()

    def _run(self):
        pipeline = [{"$match": {"operationType": {"$in": ["update", "replace", "delete"]}}}]
        with self.db.ledger.watch(pipeline, max_await_time_ms=500) as stream:
            self.ready.set()
            while not self._stop.is_set():
                change = stream.try_next()
                if change is None:
                    continue
                doc_id = (change.get("documentKey") or {}).get("_id")
                self.events.append({"op": change["operationType"], "id": doc_id})
                ledger.append(
                    self.db, runId="system", action="ledger.tamper",
                    actor="service:tamper-watchdog", verdict="tamper", applied=False,
                    reason=(f"audit ledger entry {doc_id} was subject to a "
                            f"{change['operationType']} operation; the application never "
                            f"issues one, so this happened out of band"),
                )

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.ready.wait(timeout=10)
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)


def issue_halt(db, run_id, *, by, note=None):
    """Issue a halt. Recorded in the ledger with the identity that issued it."""
    ident = db.identities.find_one({"_id": by})
    if not ident or "run.halt" not in (ident.get("allowedActions") or []):
        raise PermissionError(f"{by!r} is not permitted to halt a run")
    doc = {
        "_id": f"ctl-{run_id}-halt-{int(datetime.now(timezone.utc).timestamp()*1000)}",
        "runId": run_id, "command": "halt", "issuedBy": by, "note": note,
        "ts": datetime.now(timezone.utc),
    }
    db.controls.insert_one(doc)
    ledger.append(db, runId=run_id, action="run.halt", actor=by,
                  verdict="halt", applied=True,
                  reason=f"halt issued by {by}" + (f": {note}" if note else ""))
    return doc
