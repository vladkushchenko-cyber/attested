#!/usr/bin/env python3
"""Can a diagnostic's position on the diagnostics maturity ladder be read from our best-candidate sources?

Ladder (replaces trial phase as the radar's maturity axis):
  R1 Analytical validation   - LoD, precision, reproducibility
  R2 Clinical validation     - sens/spec/PPV/NPV vs a reference standard
  R3 Clinical utility         - does using it change management or outcomes
  R4 Regulatory               - PMA / 510(k) / De Novo / CE-IVD / licensed, or declared LDT
  R5 Reimbursement & adoption - coverage, guideline inclusion, HTA, real-world uptake

Two questions, tested separately:
  Q1 DETECTION  - can a rung be assigned to a record?
  Q2 PROGRESSION - can the same assay be followed across rungs and years?
"""
import json, re, html, glob, collections, sys

RUNGS = {
 1: ("analytical validation", re.compile(
      r"limit of detection|\bLoD\b|\bLoB\b|\bLoQ\b|analytical (sensitivit|specificit|validat|performance)|"
      r"precision|reproducib|repeatab|linearity|spike.?in|contrived sampl|coefficient of variation|\bCV%|"
      r"input (DNA|amount)|interferen|carry.?over|\bVAF\b limit|analytic", re.I)),
 2: ("clinical validation", re.compile(
      r"clinical (sensitivit|specificit|validat|performance)|sensitivit\w*|specificit\w*|\bPPV\b|\bNPV\b|"
      r"positive predictive|negative predictive|\bAUC\b|\bAUROC\b|C.statistic|concordan\w*|"
      r"reference standard|validation cohort|diagnostic (accuracy|performance|yield)|detection rate|"
      r"case.control|cross.sectional", re.I)),
 3: ("clinical utility", re.compile(
      r"change in management|changed? (clinical )?management|treatment decision|clinical utility|"
      r"guided (therapy|treatment|adjuvant)|MRD.guided|biomarker.(directed|guided)|"
      r"de.?escalat|escalat\w* therapy|impact on (management|outcome|treatment)|"
      r"randomi[sz]ed.{0,40}(assay|test|ctDNA|biomarker|guided)|interventional (study|trial).{0,40}(assay|ctDNA)|"
      r"(DFS|OS|PFS|RFS|EFS).{0,60}(ctDNA|MRD|biomarker|assay).(positiv|negativ|status)|"
      r"(ctDNA|MRD).{0,30}(status|positivity).{0,60}(DFS|OS|PFS|RFS|surviv)", re.I)),
 # R4 is deliberately narrow. Three traps found by diagnosis and fixed here:
 #  - "IDE\b" (no left boundary) matched inside provIDE/sIDE/nucleotIDE -> ~150 false positives
 #  - bare "De Novo" matched the clinical phrase "de novo metastasis", not the FDA pathway
 #  - bare "FDA-approved" overwhelmingly refers to the *therapy*, not the assay, so it now
 #    requires assay vocabulary within a short window
 4: ("regulatory", re.compile(
      r"\bPMA\b|premarket approval|510\(k\)|\bDe Novo (request|classification|pathway|authoriz\w*)|"
      r"breakthrough device|\bCE.?IVD\b|\bIVDR\b|\bCE.?mark\w*|Health Canada|"
      r"regulatory (approv|clearance|submission|status|pathway)|companion diagnostic (approv|clear)|"
      r"laboratory.developed test|\bLDT\b|submitted to (the )?FDA|\bIDE\b|"
      r"(assay|test|device|diagnostic|platform|panel|IHC|NGS)[^.;]{0,60}?FDA.{0,3}(approv|clear|authori)\w*|"
      r"FDA.{0,3}(approv|clear|authori)\w*[^.;]{0,60}?(assay|test|device|diagnostic|platform|panel)", re.I)),
 5: ("reimbursement & adoption", re.compile(
      r"reimburse\w*|\bMedicare\b|\bCMS\b|coverage (decision|determination|policy)|\bMolDX\b|"
      r"\bNCCN\b|guideline (inclusion|recommend)|publicly fund|public funding|"
      r"cost.effectiv\w*|\bQALY\b|budget impact|health technology assessment|\bHTA\b|payer|"
      r"real.world (implementation|uptake|adoption)|uptake", re.I)),
}

# Named assay entities, for the progression test
ENTITIES = {
 "Signatera":        r"\bsignatera\b",
 "Guardant360":      r"guardant\s?360|guardant360",
 "Guardant Reveal":  r"guardant\s?reveal",
 "Shield (Guardant)":r"\bshield\b(?!ing)",
 "FoundationOne Liquid": r"foundation\s?one\s?liquid|\bF1LCDx\b|foundationone liquid",
 "FoundationOne CDx":r"foundation\s?one\s?cdx|\bF1CDx\b",
 "Galleri (Grail)":  r"\bgalleri\b",
 "Oncomine Dx":      r"\boncomine\b",
 "cobas EGFR":       r"\bcobas\b.{0,20}egfr|cobas egfr",
 "therascreen":      r"therascreen",
 "Idylla":           r"\bidylla\b",
 "Ventana/pharmDx (PD-L1)": r"pharmdx|ventana.{0,15}(pd.?l1|sp142|sp263)|22c3|28.?8",
 "Tempus xF/xM":     r"tempus\s?x[fm]\b|\btempus\b.{0,10}(xf|xm)",
 "Haystack MRD":     r"haystack",
 "NeXT Personal":    r"next personal",
 "PhasED-seq":       r"phased.?seq",
 "Avenio":           r"\bavenio\b",
 "NavDx":            r"\bnavdx\b",
 "Cologuard":        r"cologuard",
 "DELFI":            r"\bDELFI\b",
 "CancerSEEK":       r"cancerseek",
 "Percepta/Envisia (Veracyte)": r"percepta|envisia",
 "Nodify (Biodesix)":r"nodify|\bbiodesix\b",
 "Lunar (Guardant)": r"\blunar\b",
 "Archer/AMP":       r"archer\s?dx|archerdx",
}
ENT = {k: re.compile(v, re.I) for k, v in ENTITIES.items()}

LUNG = re.compile(r"\b(lung|NSCLC|SCLC|non.?small.?cell)\b", re.I)
DX   = re.compile(r"ctDNA|cfDNA|liquid biops|\bMRD\b|minimal residual|methylat|fragmentom|\bNGS\b|"
                  r"comprehensive genomic profiling|screening|early detection|assay|biomarker|"
                  r"immunohistochem|\bIHC\b|\bFISH\b", re.I)

# Diagnosis showed R4/R5 vocabulary inside research abstracts is ~85% false positive
# ("uptake" = Fc-mediated cellular uptake; "CMS" = screening eligibility criteria;
#  "NCCN" = panel naming; "FDA-approved" = the therapy, not the assay).
# So: evidence rungs R1-R3 are read from text; R4/R5 are asserted by source provenance.
EVIDENCE_RUNGS = (1, 2, 3)

def rungs_of(t, text_only=True):
    rs = [r for r, (_, rx) in RUNGS.items() if rx.search(t)]
    return [r for r in rs if r in EVIDENCE_RUNGS] if text_only else rs

def load_jsonl(f):
    recs = []
    for line in open(f):
        line = line.strip()
        if line:
            try: recs += json.loads(line)["message"]["items"]
            except Exception: pass
    return recs

def txt_of(r):
    ti = (r.get("title") or [""])[0]
    ab = re.sub(r"<[^>]+>", " ", r.get("abstract") or "")
    return html.unescape(ti + " " + ab), ti

# ============================ Q1: DETECTION ============================
print("#" * 94)
print("Q1  RUNG DETECTION — can a ladder position be assigned from each source?")
print("#" * 94)

corpora = {}
for fn, lab in [("asco2023.jsonl","ASCO 2023"),("asco2024.jsonl","ASCO 2024"),
                ("asco2025.jsonl","ASCO 2025"),("aacr2025.jsonl","AACR 2025")]:
    rows = []
    for r in load_jsonl(fn):
        t, ti = txt_of(r)
        if not (LUNG.search(t) and DX.search(t)): continue
        rows.append({"doi": r.get("DOI"), "title": ti, "text": t, "rungs": rungs_of(t)})
    corpora[lab] = rows

hdr = f"{'source':16s}{'lung×dx':>9s}{'classified':>12s}{'%':>6s}   " + "".join(f"{'R'+str(i):>7s}" for i in RUNGS)
print(hdr); print("-"*len(hdr))
for lab, rows in corpora.items():
    cl = [x for x in rows if x["rungs"]]
    dist = collections.Counter()
    for x in rows:
        if x["rungs"]: dist[max(x["rungs"])] += 1
    print(f"{lab:16s}{len(rows):>9d}{len(cl):>12d}{100*len(cl)//max(len(rows),1):>5d}%   "
          + "".join(f"{dist.get(i,0):>7d}" for i in RUNGS))

# ---- structured (non-abstract) sources ----
print()
def report_struct(label, texts):
    cl = [t for t in texts if rungs_of(t)]
    dist = collections.Counter()
    for t in texts:
        rs = rungs_of(t)
        if rs: dist[max(rs)] += 1
    print(f"{label:16s}{len(texts):>9d}{len(cl):>12d}{100*len(cl)//max(len(texts),1):>5d}%   "
          + "".join(f"{dist.get(i,0):>7d}" for i in RUNGS))
    return dist

struct = {}
# ClinicalTrials.gov
try:
    ts = []
    for s in json.load(open("src_ctg.json")).get("studies", []):
        p = s["protocolSection"]
        parts = [(p["identificationModule"].get("briefTitle") or ""),
                 " ".join((p.get("conditionsModule", {}) or {}).get("conditions") or []),
                 (p.get("descriptionModule", {}) or {}).get("briefSummary", "") or "",
                 (p.get("descriptionModule", {}) or {}).get("detailedDescription", "") or "",
                 " ".join((o.get("measure","")+" "+o.get("description","") or "") for o in
                          ((p.get("outcomesModule", {}) or {}).get("primaryOutcomes") or []))]
        t = " ".join(parts)
        if LUNG.search(t) and DX.search(t): ts.append(t)
    struct["ClinicalTrials.gov"] = report_struct("ClinTrials.gov", ts)
except Exception as e: print("ctg err", e)
# openFDA PMA
try:
    ts = [" ".join(str(r.get(k) or "") for k in
          ["trade_name","generic_name","ao_statement","advisory_committee_description","decision_code"])
          for r in json.load(open("src_fda_pma.json")).get("results", [])]
    struct["openFDA PMA"] = report_struct("openFDA PMA", ts)
except Exception as e: print("fda err", e)
# Europe PMC
try:
    ts = [re.sub(r"<[^>]+>"," ",(r.get("title") or "")+" "+(r.get("abstractText") or ""))
          for r in json.load(open("src_epmc.json"))["resultList"]["result"]]
    struct["Europe PMC"] = report_struct("EuropePMC(PPR)", ts)
except Exception as e: print("epmc err", e)
# Ontario Health HTA pdf
try:
    t = open("oh_ok.txt", encoding="utf-8", errors="replace").read()
    struct["Ontario Health"] = report_struct("OntarioHealthHTA", [t])
except Exception as e: print("oh err", e)
# Health Canada MDALL (trade names + licence metadata only)
try:
    lic = {r["original_licence_no"]: r for r in json.load(open("hc_licence_all.json"))}
    ts = []
    for f in glob.glob("dev_*.json"):
        for d in json.load(open(f))[:400]:
            L = lic.get(d.get("original_licence_no"), {})
            # no regulator name injected: that would make R4 detection circular
            ts.append(f"{d.get('trade_name','')} {L.get('licence_type_desc','')}")
    struct["HC MDALL"] = report_struct("HC MDALL", ts[:300])
except Exception as e: print("hc err", e)

# ============================ Q2: PROGRESSION ============================
print()
print("#" * 94)
print("Q2  PROGRESSION — can the same named assay be followed across rungs and years?")
print("#" * 94)

# build per-entity observations across congress years + structured sources
obs = collections.defaultdict(list)   # entity -> [(source, max_rung, rungs, title)]
for lab, rows in corpora.items():
    for x in rows:
        for ent, rx in ENT.items():
            if rx.search(x["text"]):
                obs[ent].append((lab, max(x["rungs"]) if x["rungs"] else None, x["rungs"], x["title"][:70]))
for lab, key in [("ClinicalTrials.gov","src_ctg.json")]:
    try:
        for s in json.load(open(key)).get("studies", []):
            p = s["protocolSection"]
            t = (p["identificationModule"].get("briefTitle") or "") + " " + \
                ((p.get("descriptionModule", {}) or {}).get("briefSummary", "") or "")
            rs = rungs_of(t)
            for ent, rx in ENT.items():
                if rx.search(t):
                    obs[ent].append((lab, max(rs) if rs else None, rs, (p["identificationModule"].get("briefTitle") or "")[:70]))
    except Exception: pass
try:
    for r in json.load(open("src_fda_pma.json")).get("results", []):
        t = " ".join(str(r.get(k) or "") for k in ["trade_name","generic_name","ao_statement"])
        for ent, rx in ENT.items():
            if rx.search(t):
                obs[ent].append(("openFDA PMA", 4, [4], (r.get("trade_name") or "")[:70]))
except Exception: pass
try:
    t = open("oh_ok.txt", encoding="utf-8", errors="replace").read()
    for ent, rx in ENT.items():
        if rx.search(t):
            obs[ent].append(("Ontario Health HTA", 5, rungs_of(t), "OHTAC recommendation"))
except Exception: pass

print(f"{'assay entity':26s}{'obs':>5s}{'sources':>9s}{'rungs seen':>14s}  {'years':<22s} trail?")
print("-"*96)
trackable = 0
for ent in sorted(obs, key=lambda e: -len(obs[e])):
    o = obs[ent]
    srcs = sorted({x[0] for x in o})
    rs = sorted({r for x in o for r in x[2]})
    yrs = sorted({x[0].split()[-1] for x in o if x[0].startswith(("ASCO","AACR"))})
    trail = "YES" if len(rs) >= 2 and (len(yrs) >= 2 or len(srcs) >= 2) else ("partial" if len(rs) >= 2 or len(yrs) >= 2 else "no")
    if trail == "YES": trackable += 1
    print(f"{ent:26s}{len(o):>5d}{len(srcs):>9d}{str(rs):>14s}  {','.join(yrs):<22s} {trail}")
print(f"\n  entities with a usable multi-rung / multi-year trail: {trackable}/{len(obs)}")

json.dump({k: v for k, v in obs.items()}, open("ladder_obs.json","w"), indent=1, default=str)
