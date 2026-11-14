#!/usr/bin/env python3
"""Pull 10 test records per non-Canadian source, mapped to the plan's step-3 fields.

Same honesty rules as the Canadian run: a field is only counted as populated if a
value was actually extracted from the source text. No inference from outside the record.
"""
import json, re, html, glob, sys, xml.etree.ElementTree as ET

FIELDS = ["modality","biomarker_gene","tumour_type","stage_line","sample_type",
          "clinical_use","study_size_result","sponsor_company","trial_id_drug","source_quote"]
def blank(): return {f: None for f in FIELDS}

GENES = ["EGFR","ALK","ROS1","KRAS","BRAF","NTRK","RET","MET","HER2","ERBB2","PD-L1","PDL1",
         "TP53","STK11","KEAP1","MSI","TMB","SHP2","CEACAM5","TROP2","DLL3","SMARCA4"]
MODALITY = [
 (r"\bctDNA\b|cell.?free DNA|\bcfDNA\b|circulating tumou?r DNA","ctDNA / cfDNA"),
 (r"liquid biops","liquid biopsy"),
 (r"\bMRD\b|minimal residual|molecular residual","MRD assay"),
 (r"methylat","methylation"),
 (r"fragmentom","fragmentomics"),
 (r"\bNGS\b|next.generation sequencing|comprehensive genomic profiling|\bCGP\b|whole.(genome|exome)","NGS / CGP panel"),
 (r"immunohistochem|\bIHC\b|pharmDx|\bclone\b","IHC"),
 (r"\bFISH\b|break.?apart|in situ hybrid","FISH / ISH"),
 (r"\bPCR\b|\bqPCR\b|ddPCR|droplet digital","PCR"),
 (r"radiomic|imaging biomarker","radiomics"),
 (r"digital patholog|AI.{0,15}patholog|deep learning.{0,25}(slide|histo|whole.slide)","AI / digital pathology"),
 (r"breath|volatile organic","breath analysis"),
 (r"circulating tumou?r cell|\bCTC\b","CTC"),
]
SAMPLE = [(r"\bplasma\b|\bserum\b|\bblood\b|cell.?free|peripheral blood","blood / plasma"),
          (r"\bFFPE\b|tissue|biops(y|ies)|resect|whole.slide|histolog","tissue"),
          (r"\burine\b|urinary","urine"),
          (r"\bsputum\b|bronchoalveolar|\bBAL\b|pleural","respiratory / pleural fluid"),
          (r"\bsaliva\b","saliva")]
USE = [(r"\bscreen(ing)?\b|early detection|\bMCED\b","screening / early detection"),
       (r"\bMRD\b|residual disease|surveillanc|monitor|recurrence|relapse","monitoring / MRD"),
       (r"companion diagnostic|treatment selection|therapy selection|eligib|predict(ive|ing) (of )?response|patient selection","treatment selection"),
       (r"prognos","prognostication"),
       (r"diagnos","diagnosis")]
STAGE = [(r"\bstage (I{1,3}V?|[1-4])\b","stage %s"),(r"resectable|\bresected\b|perioperativ","resectable / perioperative"),
         (r"neoadjuvant","neoadjuvant"),(r"\badjuvant\b","adjuvant"),
         (r"advanced|metastatic|\bIV\b","advanced / metastatic"),
         (r"first.?line|1L\b","first-line"),(r"second.?line|2L\b","second-line"),
         (r"early.stage","early-stage")]
PERF = re.compile(r"(sensitivit\w*|specificit\w*|\bPPV\b|\bNPV\b|positive predictive value|negative predictive value|"
                  r"\bAUC\b|\bAUROC\b|C.statistic|concordance|accuracy|limit of detection|\bLOD\b)"
                  r"[^.;]{0,60}?(\d{1,3}(?:\.\d+)?\s?%|0\.\d{2,3})", re.I)
SIZE = re.compile(r"(?:\bn\s?=\s?(\d{2,6})|(\d{2,6})\s+(?:patients|pts|participants|subjects|samples)|enrolled\s+(\d{2,6}))", re.I)
NCT  = re.compile(r"\bNCT\d{8}\b")

def pick(rules, text):
    for rx, val in rules:
        m = re.search(rx, text, re.I)
        if m:
            if "%s" in val:
                try: return val % m.group(1).upper()
                except Exception: return val.replace(" %s","")
            return val
    return None

def genes_in(t):
    g=[x for x in GENES if re.search(r"(?<![A-Za-z])"+re.escape(x)+r"(?![A-Za-z0-9])",t,re.I)]
    return ", ".join(dict.fromkeys(g)) or None

def tumour(t):
    if re.search(r"\b(lung|NSCLC|SCLC|non.?small.?cell|small.?cell lung)\b",t,re.I): return "lung (NSCLC/SCLC)"
    m=re.search(r"\b(breast|colorect|pancrea\w*|prostate|ovarian|gastric|oesophag|esophag|hepatocell|renal|bladder|melanoma|pan.?cancer|multi.?cancer)\b",t,re.I)
    return m.group(1).lower() if m else None

def perf(t):
    out=[]
    for m in PERF.finditer(t):
        s=re.sub(r"\s+"," ",m.group(0)).strip()
        if s.lower() not in [x.lower() for x in out]: out.append(s)
        if len(out)>=3: break
    return "; ".join(out) or None

def size(t):
    m=SIZE.search(t)
    if not m: return None
    return "n=" + next(g for g in m.groups() if g)

def combine(t):
    """study_size_result = size + headline performance, per plan field definition."""
    s,p = size(t), perf(t)
    return " | ".join(x for x in [s,p] if x) or None

def quote(t, n=220):
    m=PERF.search(t) or SIZE.search(t)
    if m:
        a=max(0,m.start()-110); return '"'+re.sub(r"\s+"," ",t[a:a+n]).strip()+'"'
    return '"'+re.sub(r"\s+"," ",t[:n]).strip()+'"'

def build(t, sponsor=None, drug=None, extra=None):
    r=blank()
    r["modality"]=pick(MODALITY,t); r["biomarker_gene"]=genes_in(t); r["tumour_type"]=tumour(t)
    r["stage_line"]=pick(STAGE,t); r["sample_type"]=pick(SAMPLE,t); r["clinical_use"]=pick(USE,t)
    r["study_size_result"]=combine(t); r["sponsor_company"]=sponsor
    nct=NCT.search(t)
    r["trial_id_drug"]=drug or (nct.group(0) if nct else None)
    r["source_quote"]=quote(t)
    if extra: r["_extra"]=extra
    return r

out={}
LUNG=re.compile(r"\b(lung|NSCLC|SCLC|non.?small.?cell)\b",re.I)
DX=re.compile(r"ctDNA|cfDNA|liquid biops|\bMRD\b|minimal residual|methylat|fragmentom|\bNGS\b|"
              r"comprehensive genomic profiling|screening|early detection|sensitivit|specificit|assay|biomarker",re.I)

# ---------------- 1 & 2. Congress abstracts via Crossref ----------------
def load_jsonl(f):
    recs=[]
    for line in open(f):
        line=line.strip()
        if line:
            try: recs+=json.loads(line)["message"]["items"]
            except Exception: pass
    return recs

for fn,label in [("asco2025.jsonl","Crossref / ASCO 2025 abstracts"),("aacr2025.jsonl","Crossref / AACR 2025 abstracts")]:
    recs=[]
    for r in load_jsonl(fn):
        ti=(r.get("title") or [""])[0]
        ab=re.sub(r"<[^>]+>"," ",r.get("abstract") or "")
        t=html.unescape(ti+" "+ab)
        if not (LUNG.search(t) and DX.search(t)): continue
        if not PERF.search(t): continue          # require real performance data
        recs.append(build(t, extra={"DOI":r.get("DOI"),"title":ti[:95]}))
        if len(recs)>=10: break
    out[label]=recs

# ---------------- 3. ClinicalTrials.gov ----------------
recs=[]
for s in json.load(open("src_ctg.json")).get("studies",[]):
    p=s["protocolSection"]; i=p["identificationModule"]
    ti=i.get("briefTitle") or ""; desc=(p.get("descriptionModule",{}) or {}).get("briefSummary","") or ""
    arms=" ".join((a.get("name","")+" "+a.get("description","") or "") for a in ((p.get("armsInterventionsModule",{}) or {}).get("interventions") or []))
    cond=" ".join((p.get("conditionsModule",{}) or {}).get("conditions") or [])
    des=p.get("designModule",{}) or {}
    enroll=((des.get("enrollmentInfo") or {}).get("count"))
    spon=((p.get("sponsorCollaboratorsModule",{}) or {}).get("leadSponsor") or {}).get("name")
    t=" ".join([ti,cond,desc,arms])
    if not (LUNG.search(t) and DX.search(t)): continue
    r=build(t, sponsor=spon, drug=i.get("nctId"),
            extra={"nct":i.get("nctId"),"type":des.get("studyType"),
                   "phase":",".join(des.get("phases") or []) or "N/A","enrol":enroll,"title":ti[:92]})
    if enroll and not r["study_size_result"]: r["study_size_result"]=f"n={enroll} (enrolment)"
    elif enroll: r["study_size_result"]=f"n={enroll} (enrolment) | "+r["study_size_result"]
    recs.append(r)
    if len(recs)>=10: break
out["ClinicalTrials.gov API v2"]=recs

# ---------------- 4. PubMed ----------------
recs=[]
try:
    root=ET.parse("src_pubmed.xml").getroot()
    for art in root.findall(".//PubmedArticle"):
        ti="".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
        ab=" ".join("".join(x.itertext()) for x in art.findall(".//Abstract/AbstractText"))
        jr=art.findtext(".//Journal/Title") or ""
        pmid=art.findtext(".//PMID") or ""
        t=ti+" "+ab
        if not (LUNG.search(t) and DX.search(t)): continue
        recs.append(build(t, sponsor=jr or None, extra={"pmid":pmid,"title":ti[:92]}))
        if len(recs)>=10: break
except Exception as e:
    recs=[{"error":str(e)}]
out["PubMed (E-utilities efetch)"]=recs

# ---------------- 5. Europe PMC preprints ----------------
recs=[]
for r in json.load(open("src_epmc.json"))["resultList"]["result"]:
    t=(r.get("title") or "")+" "+(r.get("abstractText") or "")
    t=re.sub(r"<[^>]+>"," ",t)
    if not (LUNG.search(t) and DX.search(t)): continue
    recs.append(build(t, sponsor=(r.get("authorString") or "")[:46] or None,
                      extra={"id":r.get("id"),"src":r.get("source"),"doi":r.get("doi"),"title":(r.get("title") or "")[:92]}))
    if len(recs)>=10: break
out["Europe PMC (preprints, SRC:PPR)"]=recs

# ---------------- 6. openFDA PMA ----------------
recs=[]
for r in json.load(open("src_fda_pma.json")).get("results",[]):
    t=" ".join(str(r.get(k) or "") for k in ["trade_name","generic_name","ao_statement","advisory_committee_description","decision_code"])
    rec=build(t, sponsor=r.get("applicant"),
              extra={"pma":r.get("pma_number"),"supplement":r.get("supplement_number"),
                     "decision_date":r.get("decision_date"),"committee":r.get("advisory_committee_description"),
                     "trade_name":(r.get("trade_name") or "")[:72]})
    recs.append(rec)
    if len(recs)>=10: break
out["openFDA device/pma"]=recs

# ---------------- 7. NIH RePORTER ----------------
recs=[]
try:
    for p in json.load(open("src_reporter.json")).get("results",[]):
        t=(p.get("project_title") or "")+" "+(p.get("abstract_text") or "")
        if not (LUNG.search(t) and DX.search(t)): continue
        org=((p.get("organization") or {}).get("org_name"))
        recs.append(build(t, sponsor=org, extra={"project":p.get("project_num"),"fy":p.get("fiscal_year"),
                                                 "award":p.get("award_amount"),"title":(p.get("project_title") or "")[:92]}))
        if len(recs)>=10: break
except Exception as e: recs=[{"error":str(e)}]
out["NIH RePORTER"]=recs

# ---------------- 8. OpenAlex ----------------
recs=[]
def inv2txt(idx):
    if not idx: return ""
    pos={}
    for w,ps in idx.items():
        for p in ps: pos[p]=w
    return " ".join(pos[k] for k in sorted(pos))
for w in json.load(open("src_openalex.json")).get("results",[]):
    t=(w.get("title") or "")+" "+inv2txt(w.get("abstract_inverted_index"))
    if not (LUNG.search(t) and DX.search(t)): continue
    inst=None
    for a in (w.get("authorships") or []):
        if a.get("institutions"): inst=a["institutions"][0].get("display_name"); break
    recs.append(build(t, sponsor=inst, extra={"id":(w.get("id") or "").split("/")[-1],"year":w.get("publication_year"),
                                              "cited":w.get("cited_by_count"),"title":(w.get("title") or "")[:92]}))
    if len(recs)>=10: break
out["OpenAlex"]=recs

# ---------------- 9. ISRCTN ----------------
recs=[]
try:
    root=ET.parse("src_isrctn.xml").getroot()
    def strip(tag): return tag.split('}')[-1]
    for ft in root.iter():
        if strip(ft.tag)!="fullTrial": continue
        t=" ".join(x.strip() for x in ft.itertext() if x and x.strip())
        if not (LUNG.search(t) and DX.search(t)): continue
        isr=None
        for e in ft.iter():
            if strip(e.tag) in ("isrctn","value") and e.text and re.match(r"^\d{8}$",(e.text or "").strip()):
                isr=e.text.strip(); break
        recs.append(build(t[:6000], extra={"isrctn":isr,"len":len(t)}))
        if len(recs)>=10: break
except Exception as e: recs=[{"error":str(e)}]
out["ISRCTN registry"]=recs

# ---------------- 10. SEC EDGAR ----------------
recs=[]
try:
    d=json.load(open("src_sec.json"))
    for h in d.get("hits",{}).get("hits",[]):
        s=h.get("_source",{})
        t=" ".join(str(s.get(k) or "") for k in ["display_names","file_description","form"])
        r=blank()
        r["modality"]=pick(MODALITY,t) or "n/a (corporate filing)"
        r["biomarker_gene"]=genes_in(t); r["tumour_type"]=tumour(t)
        r["sponsor_company"]=(s.get("display_names") or [None])[0]
        r["trial_id_drug"]=None
        r["source_quote"]=f'"{(s.get("file_description") or s.get("form"))}" — {s.get("form")} filed {s.get("file_date")}'
        r["_extra"]={"form":s.get("form"),"date":s.get("file_date"),"cik":(s.get("ciks") or [None])[0]}
        recs.append(r)
        if len(recs)>=10: break
except Exception as e: recs=[{"error":str(e)}]
out["SEC EDGAR full-text search"]=recs

json.dump(out,open("global_records.json","w"),indent=1)

# ---------------- report ----------------
SHOW=set(sys.argv[1:]) if len(sys.argv)>1 else None
summary={}
for src,recs in out.items():
    if recs and isinstance(recs[0],dict) and "error" in recs[0]:
        print(f"!! {src}: ERROR {recs[0]['error']}"); summary[src]=None; continue
    cov={f:sum(1 for r in recs if r.get(f)) for f in FIELDS}
    summary[src]=(len(recs),cov)
    if SHOW and not any(k.lower() in src.lower() for k in SHOW): continue
    print("="*80); print(f"{src}  —  {len(recs)} records"); print("="*80)
    for i,r in enumerate(recs,1):
        ex=r.get("_extra") or {}
        print(f"\n[{i}] {ex.get('title') or ex.get('trade_name') or ex.get('nct') or ''}")
        for f in FIELDS:
            print(f"      {f:18s}: {r.get(f) if r.get(f) else '—'}")
        if ex: print(f"      {'(ids)':18s}: { {k:v for k,v in ex.items() if k!='title'} }")
    print()

print("\n" + "#"*92)
print("FIELD COVERAGE MATRIX  (populated records / records pulled)")
print("#"*92)
hdr=["modality","biomark","tumour","stage","sample","use","size+result","sponsor","trial/drug"]
print(f"{'source':34s}{'n':>3s} " + " ".join(f"{h:>11s}" for h in hdr))
for src,v in summary.items():
    if not v: print(f"{src[:34]:34s}  —  (error)"); continue
    n,cov=v
    cells=[cov[f] for f in ["modality","biomarker_gene","tumour_type","stage_line","sample_type","clinical_use","study_size_result","sponsor_company","trial_id_drug"]]
    print(f"{src[:34]:34s}{n:>3d} " + " ".join(f"{c:>8d}/{n:<2d}" for c in cells))
