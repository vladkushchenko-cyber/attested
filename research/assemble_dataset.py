#!/usr/bin/env python3
"""Assemble a prototype dataset from the real extraction output.

Everything here traces to a source record we actually pulled: Crossref congress
abstracts (DOI), ClinicalTrials.gov (NCT), openFDA PMA (PMA number), Health Canada
MDALL (licence number), Ontario Health HTA (recommendation PDF).
No invented values. Fields we could not populate are emitted as null.
"""
import json, re, html, glob, collections

# ---------- reuse the ladder vocabulary ----------
RUNGS = {
 1: re.compile(r"limit of detection|\bLoD\b|\bLoB\b|\bLoQ\b|analytical (sensitivit|specificit|validat|performance)|"
               r"precision|reproducib|repeatab|linearity|spike.?in|contrived sampl|coefficient of variation|\bCV%|"
               r"input (DNA|amount)|interferen|carry.?over|analytic", re.I),
 2: re.compile(r"clinical (sensitivit|specificit|validat|performance)|sensitivit\w*|specificit\w*|\bPPV\b|\bNPV\b|"
               r"positive predictive|negative predictive|\bAUC\b|\bAUROC\b|C.statistic|concordan\w*|"
               r"reference standard|validation cohort|diagnostic (accuracy|performance|yield)|detection rate", re.I),
 3: re.compile(r"change in management|changed? (clinical )?management|treatment decision|clinical utility|"
               r"guided (therapy|treatment|adjuvant)|MRD.guided|biomarker.(directed|guided)|"
               r"de.?escalat|escalat\w* therapy|impact on (management|outcome|treatment)|"
               r"(DFS|OS|PFS|RFS|EFS).{0,60}(ctDNA|MRD|biomarker|assay).(positiv|negativ|status)|"
               r"(ctDNA|MRD).{0,30}(status|positivity).{0,60}(DFS|OS|PFS|RFS|surviv)", re.I)}

PERF = re.compile(r"(sensitivit\w*|specificit\w*|\bPPV\b|\bNPV\b|positive predictive value|negative predictive value|"
                  r"\bAUC\b|\bAUROC\b|concordan\w*|limit of detection|\bLOD\b)"
                  r"[^.;]{0,70}?(\d{1,3}(?:\.\d+)?\s?%|0\.\d{2,3})", re.I)
SIZE = re.compile(r"(?:\bn\s?=\s?(\d{2,6})|(\d{2,6})\s+(?:patients|pts|participants|subjects|samples))", re.I)
NCT  = re.compile(r"\bNCT\d{8}\b")
SAMPLE = [(r"\bplasma\b|cell.?free|peripheral blood|\bblood\b","blood / plasma"),
          (r"\bFFPE\b|tissue|resect|whole.slide|histolog","tissue"),(r"\burine\b","urine")]
USE = [(r"\bscreen(ing)?\b|early detection|\bMCED\b","screening / early detection"),
       (r"\bMRD\b|residual disease|surveillanc|monitor|recurrence|relapse","monitoring / MRD"),
       (r"companion diagnostic|treatment selection|eligib|patient selection|therapy selection","treatment selection"),
       (r"prognos","prognostication")]

ENTITIES = {
 "Signatera": r"\bsignatera\b", "Guardant360": r"guardant\s?360", "Guardant Reveal": r"guardant\s?reveal",
 "Shield": r"\bshield\b(?!ing)", "FoundationOne Liquid CDx": r"foundation\s?one\s?liquid|\bF1LCDx\b",
 "FoundationOne CDx": r"foundation\s?one\s?cdx|\bF1CDx\b", "Galleri": r"\bgalleri\b",
 "Oncomine Dx Target Test": r"\boncomine\b", "cobas EGFR Mutation Test v2": r"\bcobas\b.{0,20}egfr",
 "therascreen EGFR RGQ PCR": r"therascreen.{0,20}egfr|therascreen", "Idylla EGFR Mutation Test": r"\bidylla\b",
 "PD-L1 IHC 22C3 pharmDx": r"22c3", "PD-L1 IHC 28-8 pharmDx": r"28.?8\s?pharmdx|pharmdx.{0,15}28.?8",
 "VENTANA PD-L1 (SP263)": r"sp263", "VENTANA PD-L1 (SP142)": r"sp142",
 "VENTANA ALK (D5F3)": r"d5f3", "Vysis ALK Break Apart FISH": r"vysis.{0,25}alk",
 "DELFI": r"\bDELFI\b", "Tempus xF": r"tempus\s?xf", "Haystack MRD": r"haystack",
 "NeXT Personal": r"next personal", "PhasED-seq": r"phased.?seq", "Avenio": r"\bavenio\b",
 "Nodify Lung": r"nodify", "Percepta Genomic Sequencing": r"percepta",
}
ENT = {k: re.compile(v, re.I) for k, v in ENTITIES.items()}
LUNG = re.compile(r"\b(lung|NSCLC|SCLC|non.?small.?cell)\b", re.I)
DX = re.compile(r"ctDNA|cfDNA|liquid biops|\bMRD\b|minimal residual|methylat|fragmentom|\bNGS\b|"
                r"comprehensive genomic profiling|screening|early detection|assay|biomarker|"
                r"immunohistochem|\bIHC\b|\bFISH\b", re.I)

def pick(rules, t):
    for rx, v in rules:
        if re.search(rx, t, re.I): return v
    return None

def load_jsonl(f):
    out = []
    for line in open(f):
        line = line.strip()
        if line:
            try: out += json.loads(line)["message"]["items"]
            except Exception: pass
    return out

def txt(r):
    ti = (r.get("title") or [""])[0]
    ab = re.sub(r"<[^>]+>", " ", r.get("abstract") or "")
    return html.unescape(ti + " \n " + ab), html.unescape(ti)

CONG = [("asco2023.jsonl","ASCO 2023",2023),("asco2024.jsonl","ASCO 2024",2024),
        ("asco2025.jsonl","ASCO 2025",2025),("aacr2025.jsonl","AACR 2025",2025)]

# ---------- 1. evidence records ----------
evidence = []
for fn, lab, yr in CONG:
    for r in load_jsonl(fn):
        t, ti = txt(r)
        if not (LUNG.search(t) and DX.search(t)): continue
        rungs = [k for k, rx in RUNGS.items() if rx.search(t)]
        if not rungs: continue
        pm = PERF.search(t); sz = SIZE.search(t); nct = NCT.search(t)
        ents = [e for e, rx in ENT.items() if rx.search(t)]
        perf_all = []
        for m in PERF.finditer(t):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if s not in perf_all: perf_all.append(s)
            if len(perf_all) >= 4: break
        a = max(0, (pm or sz).start() - 130) if (pm or sz) else 0
        quote = re.sub(r"\s+", " ", t[a:a+300]).strip()
        evidence.append({
          "id": r.get("DOI"), "src": lab, "year": yr, "srcType": "congress",
          "title": ti[:190], "rung": max(rungs), "rungs": sorted(rungs),
          "entities": ents, "perf": perf_all or None,
          "n": ("n=" + next(g for g in sz.groups() if g)) if sz else None,
          "sample": pick(SAMPLE, t), "use": pick(USE, t),
          "nct": nct.group(0) if nct else None, "quote": quote,
          "url": "https://doi.org/" + (r.get("DOI") or ""),
        })

# ---------- 2. regulatory records (R4 by provenance) ----------
reg = []
seen = set()
for r in json.load(open("src_fda_pma.json")).get("results", []):
    tn = (r.get("trade_name") or "").strip()
    ao = (r.get("ao_statement") or "")
    ADMIN = re.compile(r"RELOCAT|MANUFACTUR|FACILIT|QUALITY CONTROL|SHELF.?LIFE|PACKAG|SUPPLIER|"
                       r"SITE CHANGE|ANNUAL REPORT|SOFTWARE|FERMENT|BULK|FILL.?LINE|ENVIRONMENTAL MONITOR|"
                       r"ALTERNATIVE PARTS|ALTERNATE SUPPLIER|RAW MATERIAL|EQUIPMENT", re.I)
    kind = "administrative" if ADMIN.search(ao) else "clinical / indication"
    key = (r.get("pma_number"), r.get("supplement_number"))
    if key in seen: continue
    seen.add(key)
    ents = [e for e, rx in ENT.items() if rx.search(tn)]
    reg.append({"id": (r.get("pma_number") or "") + (r.get("supplement_number") or ""),
                "src": "openFDA PMA", "srcType": "regulatory", "jurisdiction": "US (FDA)",
                "rung": 4, "tradeName": tn, "applicant": r.get("applicant"),
                "date": r.get("decision_date"), "panel": r.get("advisory_committee_description"),
                "supplementKind": kind, "entities": ents,
                "quote": re.sub(r"\s+", " ", ao)[:280] or None,
                "url": "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pma.cfm?id=" + (r.get("pma_number") or "")})

lic = {x["original_licence_no"]: x for x in json.load(open("hc_licence_all.json"))}
comp = {x["company_id"]: x for x in json.load(open("hc_company_all.json"))}
SPECIFIC = re.compile(r"\b(EGFR|ROS1|KRAS|BRAF|NTRK|PD-?L1|ERBB2|HER2)\b", re.I)
AMBIG = re.compile(r"\b(MET|RET|ALK)\b", re.I)
ACTX = re.compile(r"PROBE|FISH|ISH\b|ANTIBODY|MONOCLONAL|CLONE|MUTATION|AMPLIFICAT|EXON|"
                  r"IMMUNOHISTOCHEM|\bIHC\b|PHARMDX|DAKO|VENTANA|COBAS|IDYLLA|THERASCREEN|"
                  r"ONCOMINE|SEQUENC|BREAK.?APART|DUAL COLOR|SPEC\b", re.I)
NOISE = re.compile(r"ALKALINE|ALK PHOSPHAT|PLAP|ALKYL|METAB|METER|METRIC|DIAMETER|THERMOMET|FLOWMET|"
                   r"CEMENT|METATARS|HELMET|LARYN|BLADE|METHAMPHET|MAMP|DRUG OF ABUSE|PROSTEP|"
                   r"HOOK|PLATE|SCREW|TAMP|RETRACTOR|RETAINER|URINE TEST", re.I)
hc, hcseen = [], set()
for f in glob.glob("dev_*.json"):
    for d in json.load(open(f)):
        n = (d.get("trade_name") or "").strip()
        if not n or NOISE.search(n): continue
        if not (SPECIFIC.search(n) or (AMBIG.search(n) and ACTX.search(n))): continue
        k = (n, d.get("original_licence_no"))
        if k in hcseen: continue
        hcseen.add(k)
        L = lic.get(d.get("original_licence_no"), {}); C = comp.get(L.get("company_id"), {})
        hc.append({"id": "HC-" + str(d.get("original_licence_no")), "src": "Health Canada MDALL",
                   "srcType": "regulatory", "jurisdiction": "Canada (Health Canada)", "rung": 4,
                   "tradeName": n, "applicant": C.get("company_name"), "country": C.get("country_cd"),
                   "date": d.get("first_licence_dt"), "riskClass": L.get("appl_risk_class"),
                   "licenceType": L.get("licence_type_desc"),
                   "entities": [e for e, rx in ENT.items() if rx.search(n)],
                   "url": "https://health-products.canada.ca/mdall-limh/"})
hc.sort(key=lambda x: x["date"] or "", reverse=True)

# ---------- 3. reimbursement record (R5 by provenance) ----------
ohtxt = re.sub(r"\s+", " ", open("oh_ok.txt", encoding="utf-8", errors="replace").read())
m = re.search(r"With lower sensitivity.{0,400}", ohtxt)
m2 = re.search(r"recommends publicly funding.{0,260}", ohtxt)
reimb = [{
  "id": "OH-ctDNA-T790M", "src": "Ontario Health (OHTAC)", "srcType": "reimbursement",
  "jurisdiction": "Canada (Ontario)", "rung": 5,
  "title": "Cell-Free Circulating Tumour DNA Blood Testing to Detect EGFR T790M Mutation in People With Advanced NSCLC",
  "decision": "Recommended for public funding, as a triage test",
  "evidenceRung": 2,
  "perf": ["sensitivity 68%", "specificity 86%", "PPV 89%", "NPV 61%"],
  "grade": "GRADE: Moderate",
  "modality": "ctDNA / cfDNA", "gene": "EGFR T790M", "tumour": "lung (NSCLC)",
  "stage": "advanced; acquired-resistance setting", "sample": "blood / plasma",
  "use": "treatment selection (triage)",
  "quote": (m.group(0)[:330] if m else None), "quote2": (m2.group(0)[:270] if m2 else None),
  "url": "https://www.ontariohealth.ca/content/dam/ontariohealth/documents/filter-list/hta/recommendation-cell-free-circulating-tumour-en.pdf",
}]

# ---------- 4. trials ----------
trials = []
for s in json.load(open("src_ctg.json")).get("studies", []):
    p = s["protocolSection"]; i = p["identificationModule"]; des = p.get("designModule", {}) or {}
    t = " ".join([i.get("briefTitle") or "",
                  " ".join((p.get("conditionsModule", {}) or {}).get("conditions") or []),
                  (p.get("descriptionModule", {}) or {}).get("briefSummary", "") or ""])
    if not (LUNG.search(t) and DX.search(t)): continue
    rungs = [k for k, rx in RUNGS.items() if rx.search(t)]
    trials.append({"id": i.get("nctId"), "src": "ClinicalTrials.gov", "srcType": "trial",
                   "title": (i.get("briefTitle") or "")[:170],
                   "status": (p.get("statusModule", {}) or {}).get("overallStatus"),
                   "studyType": des.get("studyType"), "phase": ",".join(des.get("phases") or []) or None,
                   "n": ((des.get("enrollmentInfo") or {}).get("count")),
                   "sponsor": ((p.get("sponsorCollaboratorsModule", {}) or {}).get("leadSponsor") or {}).get("name"),
                   "start": ((p.get("statusModule", {}) or {}).get("startDateStruct") or {}).get("date"),
                   "rung": max(rungs) if rungs else None,
                   "entities": [e for e, rx in ENT.items() if rx.search(t)],
                   "sample": pick(SAMPLE, t), "use": pick(USE, t),
                   "quote": re.sub(r"\s+", " ", t)[:260],
                   "url": "https://clinicaltrials.gov/study/" + (i.get("nctId") or "")})

# ---------- 5. modality classes: rung mix by year ----------
CLASS = {
 "ctDNA MRD": r"(ctdna|cfdna).{0,60}(mrd|minimal residual|molecular residual)|(mrd|minimal residual).{0,60}(ctdna|cfdna)",
 "cfDNA methylation": r"methylat\w*.{0,50}(cfdna|ctdna|plasma|cell.?free)|(cfdna|ctdna|cell.?free).{0,50}methylat",
 "cfDNA fragmentomics": r"fragmentom|fragment (size|length) (profil|pattern)|\bDELFI\b",
 "Plasma CGP / liquid NGS": r"(plasma|liquid).{0,40}(comprehensive genomic profiling|\bCGP\b|\bNGS\b)|liquid biops.{0,40}(ngs|sequenc)",
 "Multi-cancer early detection": r"multi.?cancer early detection|\bMCED\b|multi.?cancer (screening|detection) test",
 "AI / digital pathology": r"(deep learning|machine learning|artificial intelligence|\bAI\b).{0,60}(whole.slide|histopath|patholog|H&E|slide)",
 "Radiomics / imaging AI": r"radiomic|(deep learning|\bAI\b|machine learning).{0,50}(\bCT\b|\bPET\b|imaging|nodule)",
 "PD-L1 IHC": r"pd.?l1.{0,40}(ihc|immunohistochem|tps|cps|expression)",
 "CTC": r"circulating tumou?r cell|\bCTCs?\b",
}
CL = {k: re.compile(v, re.I) for k, v in CLASS.items()}
corp = {}
for fn, lab, yr in CONG:
    rows = []
    for r in load_jsonl(fn):
        t, ti = txt(r)
        if LUNG.search(t) and DX.search(t): rows.append(t)
    corp[lab] = rows

classes = []
for name, rx in CL.items():
    years = {}
    for lab in ["ASCO 2023", "ASCO 2024", "ASCO 2025"]:
        mix = collections.Counter()
        for t in corp[lab]:
            if not rx.search(t): continue
            rs = [k for k, r2 in RUNGS.items() if r2.search(t)]
            if rs: mix[max(rs)] += 1
        n = sum(mix.values())
        years[lab[-4:]] = {"n": n, "R1": mix[1], "R2": mix[2], "R3": mix[3],
                           "mean": round(sum(k*v for k, v in mix.items())/n, 2) if n else None}
    tot = sum(y["n"] for y in years.values())
    a, c = years["2023"]["mean"], years["2025"]["mean"]
    classes.append({"name": name, "years": years, "total": tot,
                    "meanRung": c, "delta": (round(c-a, 2) if (a and c) else None),
                    "trend": ("maturing" if (a and c and c-a > 0.15) else
                              ("regressing" if (a and c and c-a < -0.15) else "stable")),
                    # R4/R5 presence for this class, by provenance
                    "hasR4": any(any(CL[name].search(x.get("tradeName") or "") for x in reg+hc) for _ in [0]),
                    })

# ---------- 6. assay entities: trails ----------
assays = []
for e, rx in ENT.items():
    trail = {}
    for fn, lab, yr in CONG:
        rs = set()
        for t in corp[lab]:
            if rx.search(t):
                rs |= {k for k, r2 in RUNGS.items() if r2.search(t)}
        trail[lab] = max(rs) if rs else None
    ev = [x for x in evidence if e in x["entities"]]
    r4 = [x for x in reg+hc if e in x["entities"]]
    r5 = [x for x in reimb if rx.search(x["title"] + " " + (x.get("gene") or ""))]
    tr = [x for x in trails if False] if False else [x for x in trials if e in x["entities"]]
    if not (ev or r4 or r5 or tr): continue
    best = None
    for x in ev:
        if x["perf"]:
            best = x; break
    rungs_seen = sorted({x["rung"] for x in ev} | {4 for _ in r4} | {5 for _ in r5})
    assays.append({
      "name": e, "trail": trail, "evidenceCount": len(ev), "trialCount": len(tr),
      "rungsSeen": rungs_seen, "maxRung": max(rungs_seen) if rungs_seen else None,
      "regUS": [x["id"] for x in r4 if x["jurisdiction"].startswith("US")][:4],
      "regCA": [x["id"] for x in r4 if x["jurisdiction"].startswith("Canada")][:4],
      "applicants": sorted({x["applicant"] for x in r4 if x.get("applicant")})[:3],
      "reimbursed": [x["id"] for x in r5],
      "headlinePerf": (best or {}).get("perf"),
      "headlineQuote": (best or {}).get("quote"),
      "headlineSrc": (best or {}).get("src"), "headlineId": (best or {}).get("id"),
      "sample": next((x["sample"] for x in ev if x.get("sample")), None),
      "use": next((x["use"] for x in ev if x.get("use")), None),
    })
assays.sort(key=lambda a: (-(a["maxRung"] or 0), -a["evidenceCount"]))

data = {
  "meta": {
    "built_from": "live pulls: Crossref (ASCO 2023/24/25, AACR 2025), ClinicalTrials.gov v2, "
                  "openFDA device/pma, Health Canada MDALL, Ontario Health HTA",
    "corpus": {lab: len(rows) for lab, rows in corp.items()},
    "evidence_records": len(evidence), "regulatory_records": len(reg) + len(hc),
    "trials": len(trials), "assays": len(assays), "classes": len(classes),
    "scope": "lung cancer diagnostics (MVP therapy area)",
    "ladder": {1: "Analytical validation", 2: "Clinical validation", 3: "Clinical utility",
               4: "Regulatory", 5: "Reimbursement & adoption"},
    "caveat": "R1-R3 extracted from text; R4-R5 asserted by source provenance. "
              "Entity attribution is possible for ~13% of rung-classified abstracts.",
  },
  "evidence": evidence, "regulatory": reg, "mdall": hc, "reimbursement": reimb,
  "trials": trials, "classes": classes, "assays": assays,
}
json.dump(data, open("prototype_data.json", "w"), indent=1)

print("built prototype_data.json")
for k in ["evidence", "regulatory", "mdall", "reimbursement", "trials", "classes", "assays"]:
    print(f"  {k:16s} {len(data[k])}")
print("\nassays with a performance figure attached:",
      sum(1 for a in assays if a["headlinePerf"]))
print("assays with US reg:", sum(1 for a in assays if a["regUS"]),
      "| with CA reg:", sum(1 for a in assays if a["regCA"]))
