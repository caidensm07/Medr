"""Smart search: read what the person typed, keep every part of it, and search for exactly that.
If nothing matches everything, relax step by step (and say what was relaxed)."""
import re, time
import ai
import sources as src

_cache = {}
STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "with", "how", "what", "why", "is", "are",
        "do", "does", "can", "about", "better", "best", "new", "ways", "way", "find", "help", "my", "me", "i",
        "research", "studies", "study", "papers", "paper", "into", "from", "that", "this", "it", "its", "be",
        "they", "them", "their", "theirs", "we", "us", "our", "you", "your", "he", "she", "his", "her", "these", "those",
        "can", "could", "should", "would", "will", "may", "might", "must", "more", "most", "less", "very", "so", "if",
        "when", "where", "which", "who", "whom", "there", "here", "any", "some", "all", "each", "other", "than", "then",
        "get", "got", "make", "makes", "making", "want", "need", "try", "trying", "look", "looking", "see", "show",
        "possible", "potential", "ideas", "idea", "what's", "whats", "newest", "latest", "recent", "current", "today",
        "finding", "find", "catch", "catching", "detect", "detecting", "help", "helps", "work", "works", "good", "info", "information", "things", "thing", "stuff", "etc", "something"}


ABBR = {"ai": ["AI", "artificial intelligence", "machine learning", "deep learning"], "ml": ["machine learning", "ML"],
        "dl": ["deep learning"], "t1d": ["type 1 diabetes", "T1D"], "t2d": ["type 2 diabetes", "T2D"], "cgm": ["continuous glucose monitoring", "CGM"],
        "copd": ["COPD", "chronic obstructive pulmonary disease"], "mri": ["MRI", "magnetic resonance imaging"], "ct": ["CT", "computed tomography"],
        "adhd": ["ADHD", "attention deficit hyperactivity disorder"], "ptsd": ["PTSD", "post-traumatic stress disorder"], "ms": ["multiple sclerosis"],
        "als": ["ALS", "amyotrophic lateral sclerosis"], "ckd": ["chronic kidney disease", "CKD"], "ibd": ["inflammatory bowel disease", "IBD"],
        "kids": ["children", "adolescents", "pediatric"], "teens": ["adolescents", "teenagers", "youth"], "elderly": ["older adults", "elderly", "aged"]}
GENERIC = {"treatment", "treatments", "therapy", "therapies", "approach", "approaches", "method", "methods", "way", "ways", "stop", "start",
           "before", "after", "during", "using", "use", "via", "improve", "improving", "better", "best", "new", "novel", "management",
           "manage", "managing", "early-stage", "related", "based", "people", "patients", "options", "tools", "tool", "solutions"}


def _variants(w):
    w = w.replace("’", "'")
    if w.endswith("'s"):
        return [w, w[:-2]]
    out = [w]
    if len(w) > 4 and w.endswith("ies"):
        out.append(w[:-3] + "y")
    elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        out.append(w[:-1])
    return out


def fallback_interp(text):
    """No AI available: a careful rule-based reading. Quoted phrases stay together, "A or B" / "A/B" become one
    either-or concept, every other meaningful word is its own concept, and filler words are loosened first."""
    concepts = [{"name": p, "terms": [p], "mesh": "", "importance": 3} for p in re.findall(r'"([^"]+)"', text)]
    rest = re.sub(r'"[^"]+"', " ", text)
    toks = re.findall(r"[A-Za-z0-9][\w'-]*(?:/[\w'-]+)*", rest)
    words = []
    for t in toks:
        lw = t.lower()
        if lw in STOP:
            words.append(None)              # keeps "x or y" detection simple
            continue
        if re.fullmatch(r"\d+", lw) and words and words[-1]:
            words[-1] = words[-1] + " " + lw          # "type 1", "covid 19"
            continue
        words.append(t)
    low = rest.lower()
    for w in words:
        if not w:
            continue
        parts = [x for x in w.split("/") if x]
        terms = []
        for x in parts:
            lx = x.lower()
            terms += ABBR.get(lx, _variants(lx))
        imp = 1 if w.lower() in GENERIC else 2
        concepts.append({"name": w, "terms": list(dict.fromkeys(terms)), "mesh": "", "importance": imp})
    # "A or B": merge neighbours joined by "or"
    merged = []
    for c in concepts:
        if merged and re.search(r"\b" + re.escape(merged[-1]["name"].lower().split("/")[-1]) + r"\W+or\W+" + re.escape(c["name"].lower().split("/")[0]), low):
            m = merged[-1]
            merged[-1] = {"name": m["name"] + " or " + c["name"], "terms": list(dict.fromkeys(m["terms"] + c["terms"])),
                          "mesh": "", "importance": max(m["importance"], c["importance"])}
        else:
            merged.append(c)
    concepts = merged
    if concepts and all(c["importance"] < 3 for c in concepts):
        core = [c for c in concepts if c["importance"] == 2]
        (core[-1] if core else concepts[-1])["importance"] = 3      # the last real noun is usually the subject
    concepts = merge_alternatives(text, concepts)
    return {"meaning": text, "concepts": concepts[:8], "trial_condition": next((c["terms"][0] for c in concepts if c["importance"] == 3), text),
            "trial_intervention": "", "trial_other": "", "fallback": True}


def interpret(text):
    key = text.strip().lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    try:
        out = ai.interpret_search(text)
    except Exception as e:
        print("search interpreter failed, using simple reading:", e, flush=True)
        out = fallback_interp(text)
    for c in out["concepts"]:
        c["terms"] = [t.replace('"', "").strip() for t in (c.get("terms") or [c.get("name", "")]) if t and t.strip()][:8]
        c["importance"] = int(c.get("importance") or 2)
    out["concepts"] = merge_alternatives(text, out["concepts"])
    _cache[key] = (time.time(), out)
    return out


EXAMPLE_CUE = re.compile(r"\b(such as|like|e\.g\.?|for example|including|especially|particularly)[\s,:]+(?:[\w'-]+\s+){0,3}$", re.I)


def _where(text, c):
    """Where a concept first shows up in the person's words: (start, end) or None."""
    low = text.lower()
    best = None
    for t in [c.get("name", "")] + list(c.get("terms") or []):
        t = (t or "").lower().strip()
        if len(t) < 2:
            continue
        stem = re.escape(t.split("'")[0].split()[0][:6]) if t.split() else ""
        for pat in (re.escape(t), stem):
            if not pat:
                continue
            m = re.search(r"\b" + pat, low)
            if m:
                end = m.end()
                tail = re.match(r"[\w'-]*", low[end:])
                end += tail.end() if tail else 0
                if best is None or m.start() < best[0]:
                    best = (m.start(), end)
                break
    return best


def merge_alternatives(text, concepts):
    """Never let 'X or Y' (or 'such as X, Y or Z') become X AND Y. Rule-based, so it holds even if the AI slips."""
    if not text or len(concepts) < 2:
        return concepts
    spans = [(_where(text, c), c) for c in concepts]
    placed = sorted([(sp, c) for sp, c in spans if sp], key=lambda x: x[0][0])
    unplaced = [c for sp, c in spans if not sp]
    low = text.lower()
    groups = []
    for sp, c in placed:
        if groups:
            psp, members = groups[-1]
            gap = low[psp[1]:sp[0]].strip(" \t")
            in_list = bool(re.fullmatch(r"(,?\s*(or|and/or|/)|,)", gap)) and (" or " in low[psp[0]:] or "/" in gap)
            if gap and in_list:
                members.append(c)
                groups[-1] = ((psp[0], sp[1]), members)
                continue
        groups.append((sp, [c]))
    out = []
    for sp, members in groups:
        if len(members) == 1:
            c = dict(members[0])
        else:
            terms = []
            for m in members:
                for t in m.get("terms") or [m.get("name")]:
                    if t and t not in terms:
                        terms.append(t)
            c = {"name": " or ".join(m["name"] for m in members), "terms": terms[:10], "mesh": "",
                 "importance": max(m.get("importance", 2) for m in members)}
        if EXAMPLE_CUE.search(low[:sp[0]]):               # "...detection such as X": X is an example, loosen it first
            c["importance"] = min(c.get("importance", 2), 1)
        out.append(c)
    return out + unplaced


def _clean(t):
    return re.sub(r"[\[\]()]", " ", t).strip()


def group(c, tagged=True):
    parts = []
    for t in c["terms"]:
        t = _clean(t)
        if not t:
            continue
        parts.append((f'"{t}"[tiab]' if " " in t else f"{t}[tiab]") if tagged else (f'"{t}"' if " " in t else t))
    if c.get("mesh"):
        parts.append(f'"{_clean(c["mesh"])}"[mh]')
    return "(" + " OR ".join(dict.fromkeys(parts)) + ")" if parts else ""


def pubmed_query(concepts, tagged=True):
    return " AND ".join(g for g in (group(c, tagged) for c in concepts) if g)


def ladder(concepts):
    """Strictest first: every concept in title/abstract; then every concept with PubMed's own term mapping;
    then drop the least important concepts one at a time."""
    yield concepts, True, []
    yield concepts, False, []
    keep = list(concepts)
    dropped = []
    while len(keep) > 1:
        weakest = min(range(len(keep)), key=lambda k: (keep[k]["importance"], -k))
        dropped.append(keep[weakest]["name"])
        keep = keep[:weakest] + keep[weakest + 1:]
        yield keep, False, list(dropped)


def matches(text, concepts):
    text = (text or "").lower()
    hit = []
    for c in concepts:
        ok = False
        for t in c["terms"] + [c.get("mesh") or ""]:
            t = t.lower().strip()
            if not t:
                continue
            words = [w[:5] for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2]
            if t in text or (words and all(w in text for w in words)):
                ok = True
                break
        hit.append(ok)
    return hit


def search_papers(text, n=20, sort="relevance", min_year=None, reviews_only=False, exact_words=False):
    interp = fallback_interp(text) if exact_words else interpret(text)
    cs = interp["concepts"]
    exact_total, chosen, first_hit = 0, None, None
    steps = [(pubmed_query(keep, tagged), dropped) for keep, tagged, dropped in ladder(cs)]
    counts = src.pmap(lambda st: src.pubmed_count(st[0]), steps)      # every fallback level counted at once
    for (query, dropped), total in zip(steps, counts):
        if not dropped:
            exact_total = max(exact_total, total)
        if total and first_hit is None:
            first_hit = (query, dropped)
        if total >= 5:
            chosen = (query, dropped)
            break
    if not chosen and not first_hit:                   # nothing at any level: find the blocking word(s)
        n_g, q_g, d_g = greedy_relax([cs])
        if n_g:
            first_hit = (q_g, d_g)
    query, dropped = chosen or first_hit or (pubmed_query(cs, False), [])
    res, total = src.pubmed_search(query, n=min(n + 10, 50), sort=sort, min_year=min_year, reviews_only=reviews_only)
    if dropped and first_hit and not first_hit[1]:      # the few exact matches always come first
        exact, _ = src.pubmed_search(first_hit[0], n=5, sort=sort, min_year=min_year, reviews_only=reviews_only)
        seen = {r["pmid"] for r in exact}
        res = exact + [r for r in res if r["pmid"] not in seen]
    for r in res:
        m = matches(r["title"] + " " + r.get("abstract", ""), cs)
        r["match"] = {"hit": sum(m), "of": len(cs), "missing": [c["name"] for c, ok in zip(cs, m) if not ok]}
    if sort == "relevance":
        res.sort(key=lambda r: -r["match"]["hit"])      # stable: PubMed's order within the same match level
    return res[:n], total, {**interp, "query": query, "relaxed": dropped, "exact_total": exact_total or 0}


def epmc_query(concepts):
    return " AND ".join("(" + " OR ".join(f'"{_clean(t)}"' for t in c["terms"] if _clean(t)) + ")" for c in concepts if c["terms"])


def search_preprints(text, n=15, exact_words=False):
    interp = fallback_interp(text) if exact_words else interpret(text)
    cs, dropped, res, total = list(interp["concepts"]), [], [], 0
    exact_total = None
    while cs:
        res, total = src.preprints_search(epmc_query(cs), n=n)
        if exact_total is None:
            exact_total = total
        if total >= 3 or len(cs) == 1:
            break
        weakest = min(range(len(cs)), key=lambda k: (cs[k]["importance"], -k))
        dropped.append(cs[weakest]["name"])
        cs = cs[:weakest] + cs[weakest + 1:]
    for r in res:
        m = matches(r["title"] + " " + r.get("abstract", ""), interp["concepts"])
        r["match"] = {"hit": sum(m), "of": len(m), "missing": [c["name"] for c, ok in zip(interp["concepts"], m) if not ok]}
    return res, total, {**interp, "query": epmc_query(cs), "relaxed": dropped, "exact_total": exact_total or 0}


def search_trials(text, statuses=None, phases=None, n=25, exact_words=False):
    interp = fallback_interp(text) if exact_words else interpret(text)
    cond = interp.get("trial_condition") or ""
    intr = interp.get("trial_intervention") or ""
    other = interp.get("trial_other") or ""
    tries = [(cond, intr, other, []), (cond, intr, "", [other] if other else []),
             (cond, "", "", [x for x in (other, intr) if x]), ("", "", text, ["(searched your words as typed)"])]
    res, total, used, exact_total = [], 0, tries[0], None
    for t in tries:
        if not (t[0] or t[1] or t[2]):
            continue
        res, total = src.trials_search(t[0], t[1], t[2], statuses, phases, n=n)
        used = t
        if exact_total is None:
            exact_total = total
        if total >= 3:
            break
    return res, total, {**interp, "query": f"condition: {used[0] or '-'} | intervention: {used[1] or '-'} | other: {used[2] or '-'}",
                        "relaxed": used[3], "exact_total": exact_total or 0}


# ======================= robust counting: never score from a query that's broken or over-specific ===========
def concept(terms, importance=2, name=None):
    terms = [t.replace('"', "").strip() for t in (terms or []) if t and t.strip()][:6]
    return {"name": name or (terms[0] if terms else ""), "terms": terms, "mesh": "", "importance": importance}


def concepts_for(text, terms_lists=None):
    """Concepts for an intervention/target: the AI's synonym groups if given, else read the plain words."""
    out = [concept(t, 3 if k == 0 else 2) for k, t in enumerate(terms_lists or []) if isinstance(t, list) and t]
    if out:
        return out[:3]
    try:
        return [dict(c) for c in interpret(text)["concepts"]][:3]
    except Exception:
        return fallback_interp(text)["concepts"][:3]


def relaxed_count(parts, year_from=None, year_to=None):
    """Count papers matching every concept of every part. If that's 0, loosen one detail at a time
    (least important first, never emptying a part) until something matches.
    Returns (count, query used, names of concepts that were loosened)."""
    parts = [[c for c in p if c.get("terms")] for p in parts]
    parts = [p for p in parts if p]
    n, q, dropped = _ladder(parts, year_from, year_to)
    if n or not q:
        return n, q, dropped
    # still nothing: an over-long "term" (a whole keyword string) is the usual culprit, so re-read those as concepts
    if any(len(t.split()) > 4 for p in parts for c in p for t in c["terms"]):
        fixed = []
        for p in parts:
            new = []
            for c in p:
                if any(len(t.split()) > 4 for t in c["terms"]):
                    short = [t for t in c["terms"] if len(t.split()) <= 4]
                    new += ([dict(c, terms=short)] if short else []) + concepts_for(max(c["terms"], key=len))
                else:
                    new.append(c)
            fixed.append(new)
        n2, q2, d2 = _ladder(fixed, year_from, year_to)
        if n2:
            return n2, q2, dropped + d2
    n3, q3, d3 = greedy_relax(parts, year_from, year_to)
    if n3:
        return n3, q3, d3
    return n, q, dropped


def _ladder(parts, year_from=None, year_to=None):
    parts = [list(p) for p in parts]
    dropped = []
    while True:
        q = " AND ".join(group(c, tagged=False) for p in parts for c in p)
        n = src.pubmed_count(q, year_from, year_to) if q else 0
        if n or all(len(p) == 1 for p in parts):
            return n, q, dropped
        cand = [(c.get("importance", 2), -j, i, j) for i, p in enumerate(parts) if len(p) > 1 for j, c in enumerate(p)]
        _, _, i, j = min(cand)
        dropped.append(parts[i][j]["name"])
        parts[i] = parts[i][:j] + parts[i][j + 1:]


def rescue_count(query, year_from=None, year_to=None):
    """For AI-written queries: count it; if PubMed finds nothing, re-read it as concepts and loosen."""
    n = src.pubmed_count(query, year_from, year_to)
    if n:
        return n
    return relaxed_count([concepts_for(query)], year_from, year_to)[0]


# ======================= one way in: every search of free text goes through these =======================
_tq = {}


def topic_query(text):
    """A PubMed query for plain words or an AI-written query that is guaranteed not to be over-specific:
    alternatives are OR'd, details are loosened until PubMed finds something."""
    key = (text or "").strip().lower()
    if key in _tq:
        return _tq[key]
    looks_boolean = bool(re.search(r"\b(AND|OR)\b|\[(tiab|mh|pt|dp)\]", text or ""))
    if looks_boolean and src.pubmed_count(text):
        q = text
    else:
        n, q, _ = relaxed_count([concepts_for(text)])
        if not q:
            q = text
    _tq[key] = q
    return q


usable_query = topic_query


def find_papers(text, n=15, **kw):
    return src.pubmed_search(topic_query(text), n=n, **kw)


def find_preprints(text, n=10, since=None):
    cs = [dict(c) for c in concepts_for(text)]
    res, total = [], 0
    while cs:
        res, total = src.preprints_search(epmc_query(cs), n=n, since=since)
        if total or len(cs) == 1:
            break
        weakest = min(range(len(cs)), key=lambda k: (cs[k].get("importance", 2), -k))
        cs = cs[:weakest] + cs[weakest + 1:]
    return res, total


def find_trials(text, n=15, updated_since=None):
    try:
        it = interpret(text)
    except Exception:
        it = fallback_interp(text)
    cond, intr, other = it.get("trial_condition") or "", it.get("trial_intervention") or "", it.get("trial_other") or ""
    res, total = [], 0
    for c, i, o in ((cond, intr, other), (cond, intr, ""), (cond, "", ""), ("", "", text)):
        if not (c or i or o):
            continue
        res, total = src.trials_search(c, i, o, n=n, updated_since=updated_since)
        if total:
            break
    return res, total


def greedy_relax(parts, year_from=None, year_to=None, max_steps=6):
    """Last resort when even the step-by-step loosening finds nothing: test which single concept is blocking
    the results and remove that one, repeat. Prefers removing filler (importance 1), keeps the most specific match."""
    parts = [list(p) for p in parts if p]
    dropped = []
    for _ in range(max_steps):
        q = " AND ".join(group(c, tagged=False) for p in parts for c in p)
        n = src.pubmed_count(q, year_from, year_to) if q else 0
        if n:
            return n, q, dropped
        cands = [(i, j) for i, p in enumerate(parts) if len(p) > 1 for j in range(len(p))]
        if not cands:
            return 0, q, dropped
        trial = []
        for i, j in cands:
            rest = [[c for jj, c in enumerate(p) if not (ii == i and jj == j)] for ii, p in enumerate(parts)]
            trial.append(" AND ".join(group(c, tagged=False) for p in rest for c in p))
        got = src.pmap(lambda qq: src.pubmed_count(qq, year_from, year_to), trial)
        pos = [(parts[i][j].get("importance", 2), cnt, i, j) for (i, j), cnt in zip(cands, got) if cnt]
        if pos:                                        # filler first, then the removal that keeps results most specific
            _, _, i, j = min(pos)
        else:                                          # nothing helps yet: drop the concept that matches least on its own
            solo = src.pmap(lambda ij: src.pubmed_count(group(parts[ij[0]][ij[1]], tagged=False), year_from, year_to), cands)
            i, j = cands[min(range(len(cands)), key=lambda k: (solo[k], parts[cands[k][0]][cands[k][1]].get("importance", 2)))]
        dropped.append(parts[i][j]["name"])
        parts[i] = parts[i][:j] + parts[i][j + 1:]
    q = " AND ".join(group(c, tagged=False) for p in parts for c in p)
    return (src.pubmed_count(q, year_from, year_to) if q else 0), q, dropped
