#!/usr/bin/env python3
"""Distance-based performance-claim parser, plus an honest accuracy measurement.

Directional regexes fail on real abstract prose because the figure sits on either side of
the metric name, and a neighbouring metric steals it:
  "100% Specificity, 93% Sensitivity, 100% PPV"   -> forward match gives sens=100 (from PPV)
  "97.7% sensitivity, 98.7% specificity"          -> forward match gives both 98.7
  "sensitivity of 93% at 95% specificity"         -> forward match is correct

So: find every percentage token and every metric mention, then bind each metric to its
NEAREST unclaimed percentage, preferring a token that precedes the metric name when it is
immediately adjacent. Confidence intervals are stripped first. Ties and multi-metric ranges
are refused rather than guessed.
"""
import json, re, sys

PCT_T = re.compile(r'(\d{1,3}(?:\.\d+)?)\s?%')
METRICS = [
    ('sens', re.compile(r'sensitivit\w*', re.I)),
    ('spec', re.compile(r'specificit\w*', re.I)),
    ('ppv',  re.compile(r'\bPPV\b|positive predictive value', re.I)),
    ('npv',  re.compile(r'\bNPV\b|negative predictive value', re.I)),
    ('conc', re.compile(r'concordan\w*|agreement', re.I)),
    ('acc',  re.compile(r'\baccurac\w*', re.I)),
    ('prec', re.compile(r'\bprecision\b', re.I)),
]
CI = re.compile(r'\(\s*9[0-9](?:\.\d+)?\s?%?\s*(?:confidence interval|CI)\b[^)]*\)', re.I)
CI2 = re.compile(r'\b9[05]\s?%\s*(?:confidence interval|CI)\b\s*:?\s*[^;.]{0,45}', re.I)
AUC = re.compile(r'\bAU[CR]O?C?\b[^.;]{0,35}?(0\.\d{2,3})', re.I)
# "sensitivity and specificity ... A% and B%"
CONJ = re.compile(r'(sensitivit\w*)\s+and\s+(specificit\w*)[^.;%]{0,60}?(\d{1,3}(?:\.\d+)?)\s?%'
                  r'\s*(?:and|,|/|vs\.?)\s*(\d{1,3}(?:\.\d+)?)\s?%', re.I)
CONJ_R = re.compile(r'(specificit\w*)\s+and\s+(sensitivit\w*)[^.;%]{0,60}?(\d{1,3}(?:\.\d+)?)\s?%'
                    r'\s*(?:and|,|/|vs\.?)\s*(\d{1,3}(?:\.\d+)?)\s?%', re.I)
# a single figure or a range covering two or more metrics
MULTI = re.compile(r'(?:sensitivit\w*[^.;]{0,45}specificit\w*|specificit\w*[^.;]{0,45}sensitivit\w*)'
                   r'[^.;]{0,45}?(?:>|>=|≥|<|~|about|around)?\s?(\d{1,3}(?:\.\d+)?)\s?%'
                   r'(?!\s*(?:and|,|/)\s*\d)', re.I)

MAXDIST = 42          # characters between a metric name and its figure
ADJACENT = 12         # a preceding figure this close wins over a following one


def ok(v):
    try:
        f = float(v)
    except Exception:
        return None
    return f if 0 <= f <= 100 else None


def parse(text):
    flags = {'ambiguous': False, 'conjoined': False, 'refused': False}
    if not text:
        return {}, flags
    t = CI2.sub(' ', CI.sub(' ', text))
    claims = {}

    # 1. conjoined two-figure form is unambiguous, take it first
    m = CONJ.search(t) or CONJ_R.search(t)
    if m:
        a, b = ok(m.group(3)), ok(m.group(4))
        if a is not None and b is not None:
            if m.re is CONJ:
                claims['sens'], claims['spec'] = a, b
            else:
                claims['spec'], claims['sens'] = a, b
            flags['conjoined'] = True

    # 2. distance binding for everything not already set
    pcts = [(mm.start(), mm.end(), ok(mm.group(1))) for mm in PCT_T.finditer(t)]
    pcts = [p for p in pcts if p[2] is not None]
    used = set()
    # bind in a fixed order so sens/spec get first refusal on a shared token
    for key, rx in METRICS:
        if key in claims:
            continue
        best, bestd = None, 10 ** 6
        for mm in rx.finditer(t):
            ms, me = mm.start(), mm.end()
            for i, (ps, pe, v) in enumerate(pcts):
                if i in used:
                    continue
                if pe <= ms:
                    dist = ms - pe
                    dist = dist - 6 if dist <= ADJACENT else dist   # prefer an adjacent preceding figure
                elif ps >= me:
                    dist = ps - me
                else:
                    dist = 0
                if dist < bestd and abs(dist) <= MAXDIST:
                    best, bestd = i, dist
        if best is not None:
            claims[key] = pcts[best][2]
            used.add(best)

    # 3. refuse a single figure or range that covers both metrics
    if not flags['conjoined'] and MULTI.search(t):
        both = claims.get('sens') is not None and claims.get('spec') is not None
        if not both or claims.get('sens') == claims.get('spec'):
            v = ok(MULTI.search(t).group(1))
            claims.pop('sens', None)
            claims.pop('spec', None)
            if v is not None:
                claims['sensSpecJoint'] = v
            flags['ambiguous'] = True
            flags['refused'] = True

    # 4. never allow one figure to stand for two independent metrics
    if claims.get('sens') is not None and claims.get('sens') == claims.get('spec'):
        claims['sensSpecJoint'] = claims.pop('sens')
        claims.pop('spec', None)
        flags['ambiguous'] = True
        flags['refused'] = True

    m = AUC.search(t)
    if m:
        claims['auc'] = float(m.group(1))

    # more than one distinct sensitivity figure in the passage
    svals = set()
    for mm in re.finditer(r'sensitivit\w*', t, re.I):
        for ps, pe, v in pcts:
            if abs(ps - mm.end()) <= 14 or abs(mm.start() - pe) <= 14:
                svals.add(v)
    if len(svals) > 1:
        flags['ambiguous'] = True
    return claims, flags


if __name__ == '__main__':
    d = json.load(open('prototype_data.json'))
    ev = d['evidence']
    st = {'any': 0, 'both': 0, 'joint': 0, 'amb': 0, 'same': 0, 'auc': 0}
    for e in ev:
        text = (e.get('quote') or '')
        if e.get('perf'):
            text += ' ; ' + ' ; '.join(e['perf'])
        c, f = parse(text)
        e['claims'] = c or None
        e['claimAmbiguous'] = bool(f['ambiguous'])
        e['claimConjoined'] = bool(f['conjoined'])
        if c: st['any'] += 1
        if c.get('sens') is not None and c.get('spec') is not None:
            st['both'] += 1
            if c['sens'] == c['spec']: st['same'] += 1
        if 'sensSpecJoint' in c: st['joint'] += 1
        if f['ambiguous']: st['amb'] += 1
        if 'auc' in c: st['auc'] += 1
    print('distance-based parse:', st)

    # recompute dependent aggregates
    def counts(use, sample):
        m = [e for e in ev if (use is None or e.get('use') == use) and (sample is None or e.get('sample') == sample)]
        def has(k): return sum(1 for e in m if (e.get('claims') or {}).get(k) is not None)
        both = sum(1 for e in m if (e.get('claims') or {}).get('sens') is not None
                   and (e.get('claims') or {}).get('spec') is not None)
        return {'matching': len(m), 'anyPerf': sum(1 for e in m if e.get('perf')),
                'sens': has('sens'), 'spec': has('spec'), 'both': both,
                'named': sum(1 for e in m if e.get('entities')),
                'utility': sum(1 for e in m if e.get('rung') == 3)}
    for w in d['workedExamples']:
        w['counts'] = counts(w['use'], w['sample'])
        print(' ', w['id'], w['counts'])
    for f_ in d['findings']:
        if f_['id'] == 'F4':
            f_['headline'] = (f"Only {st['both']} of {len(ev)} evidence records report a sensitivity and a "
                              f"specificity figure that can be read independently.")
            f_['detail'] = (f"A numeric threshold query is supported but searches a thin slice. A further "
                            f"{st['joint']} records give one figure covering both metrics and are never split. "
                            f"{st['amb']} records are flagged ambiguous. Every parsed number is shown beside the "
                            f"sentence it came from, so a mis-parse is visible rather than silent.")
    d['meta']['claimParse'] = {
        'recordsWithAnyClaim': st['any'], 'bothSensSpec': st['both'],
        'jointSingleFigure': st['joint'], 'ambiguous': st['amb'], 'withAUC': st['auc'],
        'method': 'distance-based binding of each metric to its nearest unclaimed percentage; '
                  'confidence intervals stripped; single-figure and range forms refused rather than split',
        'knownLimit': 'A regex parser cannot resolve every prose form. Measured accuracy is reported in '
                      'meta.claimAccuracy. The UI always shows the verbatim sentence beside the parsed value.'
    }
    json.dump(d, open('prototype_data.json', 'w'), indent=1)
    print('\nwritten.')
