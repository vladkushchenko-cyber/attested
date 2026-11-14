#!/usr/bin/env python3
"""Re-parse numeric performance claims from the fuller `quote` text.

Why: the first pass parsed only the pre-extracted `perf[]` fragments, which had already
been truncated to the first percentage. On the conjoined pattern
"sensitivity and specificity for ctDNA were 85% and 90%" that yielded sens=85, spec=85 -
the same number assigned to both. 14 of 58 records were affected.

This parser handles the conjoined forms explicitly, parses from `quote` (300 chars, which
retains the second figure), and refuses to assign one figure to two metrics.
"""
import json, re

PCT = r'(\d{1,3}(?:\.\d+)?)\s?%'

# conjoined: "sensitivity and specificity ... were 85% and 90%"
CONJ_SS = re.compile(r'sensitivit\w*\s+and\s+specificit\w*[^.;%]{0,60}?' + PCT + r'\s*(?:and|,|/|vs\.?)\s*' + PCT, re.I)
CONJ_SP = re.compile(r'specificit\w*\s+and\s+sensitivit\w*[^.;%]{0,60}?' + PCT + r'\s*(?:and|,|/|vs\.?)\s*' + PCT, re.I)
# conjoined but only ONE figure given: genuinely ambiguous, do not split
CONJ_ONE = re.compile(r'sensitivit\w*\s+and\s+specificit\w*[^.;%]{0,60}?' + PCT, re.I)

SENS = re.compile(r'sensitivit\w*(?!\s+and\s+specificit)[^.;%]{0,45}?' + PCT, re.I)
SPEC = re.compile(r'specificit\w*(?!\s+and\s+sensitivit)[^.;%]{0,45}?' + PCT, re.I)
PPV  = re.compile(r'(?:\bPPV\b|positive predictive value)[^.;%]{0,45}?' + PCT, re.I)
NPV  = re.compile(r'(?:\bNPV\b|negative predictive value)[^.;%]{0,45}?' + PCT, re.I)
CONC = re.compile(r'concordan\w*[^.;%]{0,45}?' + PCT, re.I)
AUC  = re.compile(r'\bAU[CR]O?C?\b[^.;]{0,35}?(0\.\d{2,3})', re.I)


def ok(v):
    try:
        f = float(v)
    except Exception:
        return None
    return f if 0 <= f <= 100 else None


# Confidence intervals inject a spurious "95%" (and "90%"/"99%") next to every metric.
# Strip the parenthetical before parsing, or "(95% CI: 93.6%-99.3%)" captures 95 for
# whichever metric name sits nearest.
CI = re.compile(r'\(\s*9[0-9](?:\.\d+)?\s?%?\s*(?:confidence interval|CI)\b[^)]*\)', re.I)
CI2 = re.compile(r'\b9[05]\s?%\s*(?:confidence interval|CI)\b\s*:?\s*[^;.]{0,40}', re.I)
# A bare range or inequality covering several metrics is joint, not two values.
RANGE_MULTI = re.compile(r'(sensitivit\w*[^.;]{0,40}specificit\w*|specificit\w*[^.;]{0,40}sensitivit\w*)'
                         r'[^.;]{0,40}?(?:>|>=|≥|about|around)?\s?' + PCT + r'\s?(?:-|to|–)\s?' + PCT, re.I)
GT_MULTI = re.compile(r'sensitivit\w*[^.;]{0,50}specificit\w*[^.;]{0,30}?(?:>|>=|≥)\s?' + PCT, re.I)
# Reversed order: "96.4% sensitivity", "89% for sensitivity"
SENS_R = re.compile(PCT + r'\s*(?:for\s+|in\s+)?sensitivit\w*', re.I)
SPEC_R = re.compile(PCT + r'\s*(?:for\s+|in\s+)?specificit\w*', re.I)


def clean(text):
    t = CI.sub(' ', text or '')
    t = CI2.sub(' ', t)
    return t


def parse(text):
    """Return (claims dict, flags dict)."""
    c, f = {}, {"conjoined": False, "ambiguous": False, "multiSens": False}
    if not text:
        return c, f
    raw = text
    text = clean(text)

    # a range or inequality spanning both metrics is never split into two values
    if RANGE_MULTI.search(text) or GT_MULTI.search(text):
        m = RANGE_MULTI.search(text) or GT_MULTI.search(text)
        v = ok(m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1))
        if v is None:
            v = ok(m.group(1))
        if v is not None:
            c["sensSpecJoint"] = v
            f["ambiguous"] = True
            for key, rx in (("ppv", PPV), ("npv", NPV), ("conc", CONC)):
                mm = rx.search(text)
                if mm and ok(mm.group(1)) is not None:
                    c[key] = ok(mm.group(1))
            mm = AUC.search(text)
            if mm:
                c["auc"] = float(mm.group(1))
            return c, f

    m = CONJ_SS.search(text)
    if m:
        a, b = ok(m.group(1)), ok(m.group(2))
        if a is not None and b is not None:
            c["sens"], c["spec"] = a, b
            f["conjoined"] = True
    if "sens" not in c:
        m = CONJ_SP.search(text)
        if m:
            a, b = ok(m.group(1)), ok(m.group(2))
            if a is not None and b is not None:
                c["spec"], c["sens"] = a, b
                f["conjoined"] = True
    if "sens" not in c and CONJ_ONE.search(text):
        # "sensitivity and specificity were 91.09%" - one figure for two metrics.
        # Record it as a single joint figure, never as two independent values.
        v = ok(CONJ_ONE.search(text).group(1))
        if v is not None:
            c["sensSpecJoint"] = v
            f["ambiguous"] = True

    if "sens" not in c and "sensSpecJoint" not in c:
        m = SENS.search(text) or SENS_R.search(text)
        if m and ok(m.group(1)) is not None:
            c["sens"] = ok(m.group(1))
    if "spec" not in c and "sensSpecJoint" not in c:
        m = SPEC.search(text) or SPEC_R.search(text)
        if m and ok(m.group(1)) is not None:
            c["spec"] = ok(m.group(1))
    # Identical values for two independent metrics almost always means one number was
    # matched twice. Refuse it: collapse to a joint figure and flag.
    if c.get("sens") is not None and c.get("sens") == c.get("spec") and not f["conjoined"]:
        c["sensSpecJoint"] = c.pop("sens")
        c.pop("spec", None)
        f["ambiguous"] = True

    for key, rx in (("ppv", PPV), ("npv", NPV), ("conc", CONC)):
        m = rx.search(text)
        if m and ok(m.group(1)) is not None:
            c[key] = ok(m.group(1))
    m = AUC.search(text)
    if m:
        c["auc"] = float(m.group(1))

    # more than one distinct sensitivity figure anywhere => flag, do not pick
    sv = {g for g in (ok(x.group(1)) for x in SENS.finditer(text)) if g is not None}
    if len(sv) > 1:
        f["multiSens"] = True
        f["ambiguous"] = True
    return c, f


d = json.load(open("prototype_data.json"))
ev = d["evidence"]

stats = {"any": 0, "both": 0, "joint": 0, "amb": 0, "same": 0, "auc": 0}
for e in ev:
    # parse from the fuller quote, falling back to the perf fragments
    text = (e.get("quote") or "")
    if e.get("perf"):
        text = text + " ; " + " ; ".join(e["perf"])
    c, f = parse(text)
    e["claims"] = c or None
    e["claimAmbiguous"] = bool(f["ambiguous"])
    e["claimConjoined"] = bool(f["conjoined"])
    if c:
        stats["any"] += 1
    if "sens" in c and "spec" in c:
        stats["both"] += 1
        if c["sens"] == c["spec"]:
            stats["same"] += 1
    if "sensSpecJoint" in c:
        stats["joint"] += 1
    if f["ambiguous"]:
        stats["amb"] += 1
    if "auc" in c:
        stats["auc"] += 1

print("re-parsed claims:")
for k, v in stats.items():
    print(f"  {k:7s} {v}")

print("\nspot-check: records where sens and spec differ, against the source text")
n = 0
for e in ev:
    c = e.get("claims") or {}
    if "sens" in c and "spec" in c and c["sens"] != c["spec"]:
        print(f"  sens={c['sens']:<6} spec={c['spec']:<6} conj={e['claimConjoined']}  {e['title'][:52]}")
        n += 1
    if n >= 6:
        break

print("\nspot-check: the previously-broken conjoined cases")
for e in ev:
    if e.get("claimConjoined"):
        c = e["claims"]
        q = e["quote"]
        i = q.lower().find("sensitivit")
        print(f"  sens={c.get('sens')} spec={c.get('spec')}  <- ...{q[max(0,i-10):i+105]}...")

print("\njoint-figure (one number for two metrics, correctly refused to split):")
k = 0
for e in ev:
    c = e.get("claims") or {}
    if "sensSpecJoint" in c:
        print(f"  joint={c['sensSpecJoint']}%  {e['title'][:58]}")
        k += 1
    if k >= 5:
        break

# recompute the worked examples and findings against the corrected claims
def counts(pred):
    m = [e for e in ev if pred(e)]
    def has(k):
        return sum(1 for e in m if (e.get("claims") or {}).get(k) is not None)
    both = sum(1 for e in m if (e.get("claims") or {}).get("sens") is not None
               and (e.get("claims") or {}).get("spec") is not None)
    return {"matching": len(m), "anyPerf": sum(1 for e in m if e.get("perf")),
            "sens": has("sens"), "spec": has("spec"), "both": both,
            "named": sum(1 for e in m if e.get("entities")),
            "utility": sum(1 for e in m if e.get("rung") == 3)}

for w in d["workedExamples"]:
    w["counts"] = counts(lambda e, w=w: (w["use"] is None or e.get("use") == w["use"])
                         and (w["sample"] is None or e.get("sample") == w["sample"]))
    print(f"\n{w['id']} recomputed: {w['counts']}")

for f_ in d["findings"]:
    if f_["id"] == "F4":
        f_["headline"] = f"Only {stats['both']} of {len(ev)} evidence records report both a sensitivity and a specificity figure."
        f_["detail"] = (f"A numeric sensitivity-and-specificity threshold query is supported, but it searches a thin slice. "
                        f"A further {stats['joint']} records give a single figure for both metrics and are never split. "
                        f"{stats['amb']} records are flagged ambiguous. The console states the denominator before you run "
                        f"the query, so an empty result is never mistaken for an absent technology.")
        f_["evidence"] = "deterministic parse of the retained source text, conjoined forms handled explicitly"

d["meta"]["claimParse"] = {
    "recordsWithAnyClaim": stats["any"], "bothSensSpec": stats["both"],
    "jointSingleFigure": stats["joint"], "ambiguous": stats["amb"], "withAUC": stats["auc"],
    "note": "Conjoined 'sensitivity and specificity were A% and B%' is split correctly. "
            "Where only one figure is given for both metrics it is stored as sensSpecJoint and never split."
}
json.dump(d, open("prototype_data.json", "w"), indent=1)
print("\nwritten. sens==spec collisions now:", stats["same"])
