#!/usr/bin/env bash
# Full verification run. Assumes a replica set is up and PY points at an env with pymongo.
set -euo pipefail
PY="${PY:-/tmp/mongoenv/bin/python}"
cd "$(dirname "$0")"
echo "=================== bootstrap ==================="; $PY -m attested.bootstrap --drop
echo; echo "=================== validators =================="; $PY tests/test_validators.py
echo; echo "=================== Q1 gate ====================";  $PY -m attested.bootstrap --drop >/dev/null; $PY demo/demo1_gate.py
echo; echo "=================== Q2 audit ===================";  $PY -m attested.bootstrap --drop >/dev/null; $PY demo/demo2_audit.py
echo; echo "=================== Q3 halt ====================";  $PY -m attested.bootstrap --drop >/dev/null; $PY demo/demo3_halt.py
echo; echo "=================== cross-system ================"; $PY -m attested.bootstrap --drop >/dev/null; $PY demo/demo4_crosssystem.py
