"""Public biomedical data sources. All free, no keys needed.

PubMed (NCBI E-utilities) ...... peer-reviewed papers + abstracts, publication trends
Europe PMC ..................... preprints (bioRxiv / medRxiv) + papers
ClinicalTrials.gov (API v2) .... clinical trials
"""
import copy, os, re, time, threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

S = requests.Session()
S.headers["User-Agent"] = "Medr/1.0 (student biomedical research tool)"
S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=24))
TIMEOUT = 30

# ---- speed: one shared pace-keeper for PubMed (its limit is 3 requests/sec, 10 with a free key) ----
_pace_lock = threading.Lock()
_next_slot = [0.0]


def _pace():
    gap = 0.105 if os.getenv("NCBI_API_KEY") else 0.34
    with _pace_lock:
        now = time.monotonic()
        slot = max(now, _next_slot[0])
        _next_slot[0] = slot + gap
    if slot > now:
        time.sleep(slot - now)


# ---- speed: remember answers for a while so repeat lookups are instant ----
_cache, _cache_lock = {}, threading.Lock()


def cached(ttl):
    def wrap(fn):
        def inner(*a, **k):
            key = repr((fn.__name__, a, sorted(k.items())))
            with _cache_lock:
                hit = _cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return copy.deepcopy(hit[1])       # callers may edit results; keep the cache clean
            val = fn(*a, **k)
            with _cache_lock:
                if len(_cache) > 5000:
                    _cache.clear()
                _cache[key] = (time.time(), copy.deepcopy(val))
            return val
        inner.__name__ = fn.__name__
        return inner
    return wrap


_pool = ThreadPoolExecutor(max_workers=12)
CHECK_POOL = ThreadPoolExecutor(max_workers=6)     # outer jobs that themselves use _pool (avoids deadlock)


def pmap(fn, items):
    """Run fn over items at the same time (PubMed still paced by _pace)."""
    return list(_pool.map(fn, items))


_outer = ThreadPoolExecutor(max_workers=24)     # jobs that may themselves fan out on _pool (keeps pools from deadlocking)


def together(*fns):
    """Run several no-argument functions at the same time; returns their results in order."""
    futs = [_outer.submit(f) for f in fns]
    return [f.result() for f in futs]


NAMES = {"eutils.ncbi.nlm.nih.gov": "PubMed", "www.ebi.ac.uk": "Europe PMC (preprints)",
         "clinicaltrials.gov": "ClinicalTrials.gov"}


class SourceError(Exception):
    def __init__(self, source, reason):
        super().__init__(f"{source}: {reason}")
        self.source, self.reason = source, reason


def get(url, **params):
    """GET with retries. Waits and tries again on rate limits, server hiccups and timeouts."""
    from urllib.parse import urlparse
    source = NAMES.get(urlparse(url).hostname, urlparse(url).hostname)
    last = "no response"
    ncbi = "ncbi.nlm.nih.gov" in url
    for attempt in range(4):
        if ncbi:
            _pace()
        try:
            r = S.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            last = f"couldn't connect ({type(e).__name__})"
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = f"{r.status_code} {'too many requests' if r.status_code == 429 else 'server error'}"
            wait = r.headers.get("Retry-After")
            time.sleep(float(wait) if wait and wait.isdigit() else 1.5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            if r.status_code == 404:
                r.raise_for_status()          # callers handle "not found"
            raise SourceError(source, f"{r.status_code} {r.reason}: {r.text[:160]}")
        return r
    raise SourceError(source, last)


def num(x):
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def clean(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


# ======================= PubMed =======================
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _ncbi(params):
    if os.getenv("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    return params


@cached(600)
def pubmed_search(term, n=20, sort="relevance", min_year=None, reviews_only=False, since=None):
    if reviews_only:
        term = f"({term}) AND (review[pt] OR systematic review[pt] OR meta-analysis[pt])"
    p = _ncbi(dict(db="pubmed", term=term, retmode="json", retmax=n, sort=sort))
    if min_year:
        p.update(datetype="pdat", mindate=str(min_year), maxdate="3000")
    if since:   # YYYY-MM-DD: papers added to PubMed since then
        p.update(datetype="edat", mindate=since.replace("-", "/"), maxdate="3000", sort="pub_date")
    res = get(f"{EUTILS}/esearch.fcgi", **p).json().get("esearchresult", {})
    ids, total = res.get("idlist", []), int(res.get("count") or 0)
    if not ids:
        return [], total
    summ = get(f"{EUTILS}/esummary.fcgi", **_ncbi(dict(db="pubmed", id=",".join(ids), retmode="json"))).json()["result"]
    abstracts = pubmed_abstracts(ids)
    out = []
    for pid in ids:
        r = summ.get(pid, {})
        doi = next((a.get("value") for a in r.get("articleids", []) if a.get("idtype") == "doi"), "")
        out.append({
            "pmid": pid, "title": clean(r.get("title", "")), "journal": r.get("source", ""),
            "year": (r.get("pubdate") or "")[:4], "authors": [a.get("name") for a in r.get("authors", [])][:6],
            "types": r.get("pubtype", []), "doi": doi, "abstract": abstracts.get(pid, ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
        })
    return out, total


def pubmed_abstracts(ids):
    xml = get(f"{EUTILS}/efetch.fcgi", **_ncbi(dict(db="pubmed", id=",".join(ids), retmode="xml"))).text
    out = {}
    for art in ET.fromstring(xml).iter("PubmedArticle"):
        pid = art.findtext(".//PMID")
        parts = []
        for ab in art.iter("AbstractText"):
            txt = "".join(ab.itertext()).strip()
            parts.append(f"{ab.get('Label')}: {txt}" if ab.get("Label") else txt)
        out[pid] = "\n".join(p for p in parts if p)
    return out


# ======================= ClinicalTrials.gov =======================
CTGOV = "https://clinicaltrials.gov/api/v2/studies"


@cached(600)
def _trials_search(condition="", intervention="", term="", statuses=None, phases=None, n=25, updated_since=None):
    p = {"pageSize": n, "format": "json", "countTotal": "true"}
    if condition:
        p["query.cond"] = condition
    if intervention:
        p["query.intr"] = intervention
    if term:
        p["query.term"] = term
    if statuses:
        p["filter.overallStatus"] = ",".join(statuses)
    adv = []
    if phases:  # e.g. ["PHASE2", "PHASE3"]
        adv.append("(" + " OR ".join(f"AREA[Phase]{ph}" for ph in phases) + ")")
    if updated_since:
        adv.append(f"AREA[LastUpdatePostDate]RANGE[{updated_since},MAX]")
    if adv:
        p["filter.advanced"] = " AND ".join(adv)
    data = get(CTGOV, **p).json()
    out = []
    out = [parse_study(st) for st in data.get("studies", [])]
    return out, data.get("totalCount")




def trials_search(condition="", intervention="", term="", statuses=None, phases=None, n=25, updated_since=None):
    """ClinicalTrials.gov rejects some long free-text queries as 'too complicated': retry simpler instead of failing."""
    tries = [(condition, intervention, term), (condition, intervention, ""), (condition, "", "")]
    for k, (c, i, t) in enumerate(tries):
        if k and (c, i, t) == tries[k - 1]:
            continue
        try:
            return _trials_search(c, i, t, statuses, phases, n, updated_since)
        except SourceError as e:
            if "complicated" not in e.reason.lower() and "400" not in e.reason:
                raise
            last = e
    raise last


def parse_study(st):
    ps = st.get("protocolSection", {})
    idm, stm = ps.get("identificationModule", {}), ps.get("statusModule", {})
    dm = ps.get("designModule", {})
    nct = idm.get("nctId")
    return {
        "nct": nct, "title": idm.get("briefTitle", ""), "status": stm.get("overallStatus", ""),
        "phases": dm.get("phases", []) or [], "type": dm.get("studyType", ""),
        "conditions": ps.get("conditionsModule", {}).get("conditions", []),
        "interventions": [i.get("name") for i in ps.get("armsInterventionsModule", {}).get("interventions", [])],
        "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", ""),
        "start": stm.get("startDateStruct", {}).get("date", ""),
        "updated": stm.get("lastUpdatePostDateStruct", {}).get("date", ""),
        "enrollment": dm.get("enrollmentInfo", {}).get("count"),
        "summary": ps.get("descriptionModule", {}).get("briefSummary", ""),
        "url": f"https://clinicaltrials.gov/study/{nct}",
    }


def trial_by_id(nct):
    return parse_study(get(f"{CTGOV}/{nct}", format="json").json())


@cached(6 * 3600)
def pubmed_count(term, year_from=None, year_to=None):
    t = term
    if year_from:
        t = f"({term}) AND {year_from}:{year_to or 3000}[pdat]"
    p = _ncbi(dict(db="pubmed", term=t, retmode="json", retmax=0))
    res = get(f"{EUTILS}/esearch.fcgi", **p).json().get("esearchresult", {})
    return int(res.get("count") or 0)       # a query PubMed can't parse just counts as 0


@cached(6 * 3600)
def pubmed_trend(term, years=15):
    """Papers per year for a topic, to see if a field is heating up."""
    this = time.localtime().tm_year
    ys = list(range(this - years + 1, this + 1))
    return [{"year": y, "count": c} for y, c in zip(ys, pmap(lambda y: pubmed_count(term, y, y), ys))]


# ======================= Europe PMC (preprints) =======================
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


@cached(600)
def preprints_search(term, n=15, since=None):
    q = f"({term}) AND SRC:PPR" + (f" AND FIRST_PDATE:[{since} TO 3000-01-01]" if since else "")
    d = get(EPMC, query=q, format="json", pageSize=n, resultType="core",
            sort="FIRST_PDATE_D desc").json()
    out = []
    for r in d.get("resultList", {}).get("result", []):
        server = (r.get("bookOrReportDetails") or {}).get("publisher") or "Preprint"
        out.append({"id": r.get("id"), "title": clean(r.get("title", "")), "authors": (r.get("authorString") or "")[:160],
                    "server": server, "date": r.get("firstPublicationDate", ""), "year": str(r.get("pubYear", "")),
                    "doi": r.get("doi", ""), "abstract": clean(r.get("abstractText", "")),
                    "url": f"https://doi.org/{r['doi']}" if r.get("doi") else f"https://europepmc.org/article/PPR/{r.get('id')}"})
    return out, d.get("hitCount")
