#!/usr/bin/env python3
"""Pull 10 test records from each Canadian source, mapped to the plan's step-3 extraction fields.

Plan step 3 fields:
  modality, biomarker_gene, tumour_type, stage_line, sample_type, clinical_use,
  study_size_result, sponsor_company, trial_id_drug, source_quote
Unpopulatable fields are emitted as None so field coverage can be scored honestly.
"""
import json, re, glob, html, sys

FIELDS = ["modality","biomarker_gene","tumour_type","stage_line","sample_type",
          "clinical_use","study_size_result","sponsor_company","trial_id_drug","source_quote"]

def blank():
    return {f: None for f in FIELDS}

# ---------- shared inference helpers (deterministic, keyword-based) ----------
GENES = ["EGFR","ALK","ROS1","KRAS","BRAF","MET","NTRK","RET","HER2","ERBB2",
         "PD-L1","PDL1","TP53","BRCA","MSI","TMB","CEACAM5"]
MODALITY = [
    (r"\bFISH\b|BREAK.?APART|PROBE", "FISH / ISH"),
    (r"IQFISH|ISH\b", "in situ hybridisation"),
    (r"PCR|RGQ|IDYLLA|COBAS|THERASCREEN|QPCR", "PCR"),
    (r"NGS|SEQUENC|ONCOMINE|FOUNDATION", "NGS panel"),
    (r"ANTIBODY|MONOCLONAL|IMMUNOHISTOCHEM|\bIHC\b|CLONE|DAKO|VENTANA|PHARMDX", "IHC"),
    (r"METHYLAT", "methylation"),
]
SAMPLE = [(r"PLASMA|CFDNA|CTDNA|BLOOD|LIQUID", "blood / plasma"),
          (r"TISSUE|FFPE|BIOPS|SLIDE|HISTO", "tissue"),
          (r"URINE", "urine")]
USE = [(r"SCREEN|EARLY DETECT", "screening"),
       (r"MRD|RESIDUAL|MONITOR|RECURRENCE", "monitoring / MRD"),
       (r"COMPANION|SELECT|THERAP|TREATMENT|ELIGIB|PREDICTIV", "treatment selection"),
       (r"DIAGNOS", "diagnosis")]

def pick(rules, text, default=None):
    for rx, val in rules:
        if re.search(rx, text, re.I):
            return val
    return default

def genes_in(text):
    hits = [g for g in GENES if re.search(r"\b"+re.escape(g)+r"\b", text, re.I)]
    return ", ".join(dict.fromkeys(hits)) or None

def lung_ctx(text):
    if re.search(r"\b(lung|NSCLC|SCLC|non.?small.?cell|thoracic)\b", text, re.I):
        return "lung (NSCLC/SCLC)"
    return None

out = {}

# =========================================================================
# SOURCE 1 — Health Canada Medical Devices API (MDALL)
# =========================================================================
lic = {r["original_licence_no"]: r for r in json.load(open("hc_licence_all.json"))}
comp = {r["company_id"]: r for r in json.load(open("hc_company_all.json"))}

devices, seen = [], set()
for f in glob.glob("dev_*.json"):
    for d in json.load(open(f)):
        tn = (d.get("trade_name") or "").strip()
        key = (tn, d.get("original_licence_no"))
        if not tn or key in seen:
            continue
        seen.add(key)
        devices.append(d)

# keep only genuine molecular/IHC oncology diagnostics.
# Unambiguous gene tokens can stand alone; ambiguous ones (MET, RET, ALK) must co-occur
# with assay-platform vocabulary, otherwise MDALL returns orthopaedic and tox-screen devices.
SPECIFIC = re.compile(r"\b(EGFR|ROS1|KRAS|BRAF|NTRK|PD-?L1|ERBB2|HER2)\b", re.I)
AMBIG    = re.compile(r"\b(MET|RET|ALK)\b", re.I)
ASSAY_CTX= re.compile(r"PROBE|FISH|ISH\b|ANTIBODY|MONOCLONAL|CLONE|MUTATION|AMPLIFICAT|EXON|"
                      r"IMMUNOHISTOCHEM|\bIHC\b|PHARMDX|DAKO|VENTANA|COBAS|IDYLLA|THERASCREEN|"
                      r"ONCOMINE|SEQUENC|BREAK.?APART|DUAL COLOR|SPEC\b", re.I)
NOISE    = re.compile(r"ALKALINE|ALKYL|METAB|METER|METRIC|DIAMETER|THERMOMET|FLOWMET|CEMENT|"
                      r"METATARS|HELMET|LARYN|BLADE|METHAMPHET|MAMP|DRUG OF ABUSE|ALK PHOSPHAT|PLAP|"
                      r"PROSTEP|HOOK|PLATE|SCREW|TAMP|RETRACTOR|RETAINER|URINE TEST", re.I)

def is_onco_dx(name: str) -> bool:
    if not name or NOISE.search(name):
        return False
    if SPECIFIC.search(name):
        return True
    if AMBIG.search(name) and ASSAY_CTX.search(name):
        return True
    return False

cands = [d for d in devices if is_onco_dx(d.get("trade_name") or "")]
cands.sort(key=lambda d: (d.get("first_licence_dt") or ""), reverse=True)

recs = []
for d in cands[:10]:
    tn = d["trade_name"]
    L = lic.get(d.get("original_licence_no"), {})
    C = comp.get(L.get("company_id"), {})
    r = blank()
    r["modality"] = pick(MODALITY, tn)
    r["biomarker_gene"] = genes_in(tn)
    r["tumour_type"] = lung_ctx(tn)  # usually None: trade names omit indication
    r["sample_type"] = pick(SAMPLE, tn)
    r["clinical_use"] = pick(USE, tn + " " + (L.get("licence_type_desc") or ""))
    r["sponsor_company"] = (C.get("company_name") or None)
    r["source_quote"] = (f'"{tn}" — licence {d.get("original_licence_no")}, '
                         f'first licensed {d.get("first_licence_dt")}, '
                         f'class {L.get("appl_risk_class")}, type {L.get("licence_type_desc")}, '
                         f'status {L.get("licence_status")}')
    r["_extra"] = {"licence_no": d.get("original_licence_no"),
                   "risk_class": L.get("appl_risk_class"),
                   "licence_type": L.get("licence_type_desc"),
                   "first_licensed": d.get("first_licence_dt"),
                   "company_country": C.get("country_cd")}
    recs.append(r)
out["HC Medical Devices API (MDALL)"] = recs

# =========================================================================
# SOURCE 2 — Health Canada Drug Product Database (for CDx<->drug linking)
# =========================================================================
try:
    recs = []
    for f in sorted(glob.glob("dpd_*.json")):
        for d in json.load(open(f))[:2]:
            bn = d.get("brand_name", "")
            r = blank()
            r["modality"] = "n/a (therapeutic, for CDx linkage)"
            r["tumour_type"] = None
            r["sponsor_company"] = d.get("company_name")
            r["trial_id_drug"] = f'{bn} (DIN {d.get("drug_identification_number")})'
            r["source_quote"] = (f'"{bn}" — DIN {d.get("drug_identification_number")}, '
                                 f'class {d.get("class_name")}, status {d.get("status")}')
            r["_extra"] = {"din": d.get("drug_identification_number"),
                           "ai": d.get("_ai"), "number": d.get("drug_code")}
            recs.append(r)
    out["HC Drug Product Database"] = recs[:10]
except Exception as e:
    out["HC Drug Product Database"] = [{"error": str(e)}]

# =========================================================================
# SOURCE 3 — Ontario Health HTA (reviews & recommendations index)
# =========================================================================
try:
    h = open("oh.html", encoding="utf-8", errors="replace").read()
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', h, re.S)
    seen, recs = set(), []
    for u, t in links:
        title = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
        if len(title) < 15 or title in seen:
            continue
        if not re.search(r"screen|genomic|biomarker|ctDNA|circulating|testing|tumour|cancer|profiling|pharmacogenom|diagnos",
                         title, re.I):
            continue
        seen.add(title)
        r = blank()
        r["modality"] = pick(MODALITY, title) or ("genomic profiling" if re.search(r"genomic|profiling", title, re.I) else None)
        r["biomarker_gene"] = genes_in(title)
        m = re.search(r"(lung|breast|cervical|colorect|prostate|ovarian|nervous system|central nervous)", title, re.I)
        r["tumour_type"] = lung_ctx(title) or (m.group(1).lower() if m else None)
        r["sample_type"] = pick(SAMPLE, title)
        r["clinical_use"] = pick(USE, title)
        r["sponsor_company"] = "Ontario Health (HTA body, not a vendor)"
        r["source_quote"] = f'"{title}" — Ontario Health Technology Review'
        r["_extra"] = {"url": "https://www.hqontario.ca" + u if u.startswith("/") else u}
        recs.append(r)
    out["Ontario Health HTA"] = recs[:10]
except Exception as e:
    out["Ontario Health HTA"] = [{"error": str(e)}]

# =========================================================================
# SOURCE 4 — CDA-AMC (Canada's Drug Agency) via sitemap enumeration
# =========================================================================
try:
    urls = [re.sub(r"</?loc>", "", x) for x in open("cda_all.txt").read().split()]
    rel = [u for u in urls if re.search(r"biomarker|genom|screening|ctdna|circulating|liquid-bio|precision-medicine|tumour|cancer", u, re.I)]
    recs = []
    for u in rel[:10]:
        slug = u.rstrip("/").split("/")[-1].replace("-", " ")
        r = blank()
        r["modality"] = pick(MODALITY, slug) or ("genomic / biomarker testing" if re.search(r"biomarker|genom", slug, re.I) else None)
        r["biomarker_gene"] = genes_in(slug)
        r["tumour_type"] = lung_ctx(slug) or (re.search(r"(cervical|breast|colorect|lung|prostate)", slug, re.I).group(1) if re.search(r"(cervical|breast|colorect|lung|prostate)", slug, re.I) else None)
        r["clinical_use"] = pick(USE, slug)
        r["sample_type"] = pick(SAMPLE, slug)
        r["sponsor_company"] = "CDA-AMC (HTA body, not a vendor)"
        r["source_quote"] = f'"{slug}" — CDA-AMC page: {u}'
        r["_extra"] = {"url": u}
        recs.append(r)
    out["CDA-AMC (sitemap)"] = recs
except Exception as e:
    out["CDA-AMC (sitemap)"] = [{"error": str(e)}]

json.dump(out, open("canada_records.json", "w"), indent=1)

# ---------------- report ----------------
for src, recs in out.items():
    print("=" * 78)
    print(f"{src}  —  {len(recs)} records")
    print("=" * 78)
    if recs and "error" in recs[0]:
        print("  ERROR:", recs[0]["error"]); continue
    cov = {f: 0 for f in FIELDS}
    for i, r in enumerate(recs, 1):
        print(f"\n[{i}] {r.get('source_quote')}")
        for f in FIELDS:
            if f == "source_quote": continue
            v = r.get(f)
            if v: cov[f] += 1
            print(f"      {f:18s}: {v if v else '—'}")
        if r.get("_extra"): print(f"      {'(extra)':18s}: {r['_extra']}")
    n = len(recs)
    print(f"\n  FIELD COVERAGE across {n} records:")
    for f in FIELDS:
        if f == "source_quote": continue
        print(f"    {f:20s} {cov[f]}/{n}")
    print()
