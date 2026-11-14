"""Adapters for the real external systems the agent acts across.

Transport note, learned the hard way in this environment: Python's TLS store does not
trust the corporate proxy's root CA, so `urllib` and `requests` fail with
CERTIFICATE_VERIFY_FAILED while `curl` succeeds because it reads the system keychain.
Rather than disable verification, the transport shells out to `curl`, which verifies
against the trust store that actually contains the corporate root. On a host without an
intercepting proxy this behaves identically.

All four endpoints below were verified live: Crossref, openFDA device/pma, Health Canada
MDALL and ClinicalTrials.gov v2.
"""
import json
import subprocess
from urllib.parse import urlencode

UA = "Attested/1.0 (medical-affairs evidence curation; hackathon prototype)"
TIMEOUT = 45


class SourceError(Exception):
    pass


def fetch_json(url, params=None):
    full = url + ("?" + urlencode(params) if params else "")
    try:
        out = subprocess.run(
            ["curl", "-sS", "-m", str(TIMEOUT), "-A", UA, full],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise SourceError(f"{url}: curl failed: {e.stderr[:200]}") from e
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise SourceError(f"{url}: response was not JSON: {out.stdout[:160]}") from e


# ------------------------------------------------------------------------- regulators
def openfda_pma(trade_name, limit=10):
    """FDA premarket approvals. sourceType = regulator, so a rung 4 assertion is permitted.

    Returns records with `supplementKind` classified, because 12 of 25 real supplement
    records are administrative (facility relocations, fermenter replacements) rather than
    approval events, and policy refuses to publish those as approvals.
    """
    import re
    admin = re.compile(
        r"RELOCAT|MANUFACTUR|FACILIT|QUALITY CONTROL|SHELF.?LIFE|PACKAG|SUPPLIER|"
        r"SITE CHANGE|ANNUAL REPORT|SOFTWARE|FERMENT|BULK|FILL.?LINE|"
        r"ENVIRONMENTAL MONITOR|ALTERNATIVE PARTS|ALTERNATE SUPPLIER|RAW MATERIAL|EQUIPMENT",
        re.I)
    data = fetch_json("https://api.fda.gov/device/pma.json",
                      {"search": f'trade_name:"{trade_name}"', "limit": limit})
    out = []
    for r in data.get("results", []):
        ao = r.get("ao_statement") or ""
        out.append({
            "sourceId": (r.get("pma_number") or "") + (r.get("supplement_number") or ""),
            "sourceType": "regulator",
            "tradeName": r.get("trade_name"),
            "applicant": r.get("applicant"),
            "decisionDate": r.get("decision_date"),
            "panel": r.get("advisory_committee_description"),
            "supplementKind": "administrative" if admin.search(ao) else "clinical",
            "sourceQuote": " ".join(ao.split())[:400],
            "sourceUrl": ("https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/"
                          f"pma.cfm?id={r.get('pma_number')}"),
        })
    return out


def health_canada_devices(device_name):
    """Health Canada MDALL. sourceType = regulator for the Canadian jurisdiction.

    Caveat encoded here because it is a real trap: the API matches substrings, so
    `device_name=MET` returns thousands of metatarsal bone plates. Callers must pass a
    specific term and post-filter.
    """
    data = fetch_json("https://health-products.canada.ca/api/medical-devices/device/",
                      {"type": "json", "state": "active", "device_name": device_name})
    return [{
        "sourceId": f"HC-{d.get('original_licence_no')}",
        "sourceType": "regulator",
        "tradeName": d.get("trade_name"),
        "firstLicensed": d.get("first_licence_dt"),
        "sourceQuote": (f"Health Canada active device licence "
                        f"{d.get('original_licence_no')} for {d.get('trade_name')}, "
                        f"first licensed {d.get('first_licence_dt')}."),
        "sourceUrl": "https://health-products.canada.ca/mdall-limh/",
    } for d in (data if isinstance(data, list) else [])]


# -------------------------------------------------------------------------- literature
def crossref_abstract(doi):
    """One congress abstract. sourceType = congress, so rung 4 is refused by policy."""
    d = fetch_json(f"https://api.crossref.org/works/{doi}")
    m = d.get("message", {})
    import re
    abstract = re.sub(r"<[^>]+>", " ", m.get("abstract") or "")
    abstract = " ".join(abstract.split())
    return {
        "sourceId": m.get("DOI"),
        "sourceType": "congress",
        "title": (m.get("title") or [""])[0],
        "container": (m.get("container-title") or [""])[0],
        "sourceQuote": abstract[:600],
        "sourceUrl": f"https://doi.org/{m.get('DOI')}",
    }


def clinicaltrials(term, cond="lung cancer", page_size=5):
    """ClinicalTrials.gov v2. sourceType = trial."""
    d = fetch_json("https://clinicaltrials.gov/api/v2/studies", {
        "query.cond": cond, "query.term": term,
        "pageSize": page_size, "format": "json",
    })
    out = []
    for s in d.get("studies", []):
        p = s["protocolSection"]
        i = p["identificationModule"]
        out.append({
            "sourceId": i.get("nctId"),
            "sourceType": "trial",
            "title": i.get("briefTitle"),
            "status": (p.get("statusModule") or {}).get("overallStatus"),
            "sponsor": ((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name"),
            "enrolment": ((p.get("designModule") or {}).get("enrollmentInfo") or {}).get("count"),
            "sourceQuote": " ".join(
                ((p.get("descriptionModule") or {}).get("briefSummary") or
                 i.get("briefTitle") or "").split())[:400],
            "sourceUrl": f"https://clinicaltrials.gov/study/{i.get('nctId')}",
        })
    return out


def reachable():
    """Which external systems answer right now. Printed by the demos for honesty."""
    checks = {
        "Crossref": ("https://api.crossref.org/works", {"rows": 0}),
        "openFDA": ("https://api.fda.gov/device/pma.json", {"limit": 1}),
        "Health Canada": ("https://health-products.canada.ca/api/medical-devices/device/",
                          {"type": "json", "state": "active", "device_name": "EGFR"}),
        "ClinicalTrials.gov": ("https://clinicaltrials.gov/api/v2/studies",
                               {"pageSize": 1, "format": "json"}),
    }
    out = {}
    for name, (url, params) in checks.items():
        try:
            fetch_json(url, params)
            out[name] = True
        except Exception:
            out[name] = False
    return out
