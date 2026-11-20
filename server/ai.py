"""Claude: research briefs, opportunity finding, discovery radar, crossovers, project Q&A, watch digests."""
import os, re, json
import anthropic

MODEL = os.getenv("MEDR_MODEL", "claude-sonnet-5-5")
FAST = os.getenv("MEDR_FAST_MODEL", "claude-haiku-5-5")
FAST_MODE = False          # Settings > Speed: "Fastest" runs everything on the fast model


def pick(model=None):
    return model or (FAST if FAST_MODE else MODEL)
_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call(system, messages, max_tokens=6000):
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    with client().messages.stream(model=pick(), max_tokens=max_tokens, system=system, messages=messages) as s:
        return s.get_final_text()


def call_json(system, prompt, schema, max_tokens=4000, model=None):
    """Ask Claude for structured data, matching `schema`.
    1) offer a 'submit' tool (works on every model, unlike forced tool_choice);
    2) if Claude answers in text instead, read the JSON out of the text;
    3) if that still fails, ask once more for valid JSON only."""
    tool = {"name": "submit", "description": "Submit the final result. Always call this exactly once.",
            "input_schema": schema}
    ask = prompt + "\n\nWhen you're done, call the `submit` tool with the result. Don't answer in plain text."
    last_text = ""
    for attempt in range(2):
        msg = client().messages.create(model=pick(model), max_tokens=max_tokens, system=system,
                                       messages=[{"role": "user", "content": ask}], tools=[tool])
        for block in msg.content:
            if getattr(block, "type", "") == "tool_use" and isinstance(block.input, dict):
                return fix_strings(block.input, schema)
        last_text = "".join(getattr(b, "text", "") for b in msg.content)
        try:
            data = parse_json(last_text)
            if isinstance(data, dict):
                return data
            if isinstance(data, list):         # a bare list: wrap it under the schema's one array field
                key = next((k for k, v in schema["properties"].items() if v.get("type") == "array"), None)
                if key:
                    return {key: data}
        except Exception:
            pass
        ask = (prompt + "\n\nReturn ONLY valid JSON matching this JSON schema, with no other text:\n"
               + json.dumps(schema))
    raise ValueError(f"Claude didn't return usable structured data. It said: {last_text[:200]}")


def fix_strings(data, schema):
    """Faster models sometimes send a list or object as a JSON *string*. Turn those back into real data."""
    props = (schema or {}).get("properties", {})
    out = dict(data)
    for k, v in data.items():
        want = props.get(k, {})
        if isinstance(v, str) and want.get("type") in ("array", "object") and v.strip()[:1] in "[{":
            try:
                out[k] = json.loads(v)
            except Exception:
                try:
                    out[k] = parse_json(v)
                except Exception:
                    pass
        if want.get("type") == "array" and isinstance(out.get(k), list) and want.get("items", {}).get("type") == "object":
            fixed = []
            for it in out[k]:
                if isinstance(it, str) and it.strip()[:1] == "{":
                    try:
                        it = json.loads(it)
                    except Exception:
                        continue
                if isinstance(it, dict):
                    fixed.append(fix_strings(it, want["items"]))
            out[k] = fixed
    return out


def obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or list(props)}


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}


def parse_json(text):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if starts:
        t = t[min(starts):]
    return json.loads(t[:max(t.rfind("}"), t.rfind("]")) + 1])


SYSTEM = """You are a careful biomedical research analyst supporting a researcher who explores the medical
literature and clinical research. Write in a professional, neutral research register: precise, plain language,
no hype, no exclamation marks, no casual phrasing.

Rules:
- Only make claims supported by the SOURCES given. Cite every claim inline like [PMID 12345678],
  [NCT01234567] or [PPR 123456] (preprint). If the sources don't cover something, say so plainly.
- Preprints are not peer reviewed: label them as such when you use them.
- Separate established findings from early signals. Note study type and size when it matters
  (meta-analysis > RCT > cohort > case report > lab/animal study).
- Be specific with numbers, effect sizes, phases and dates when the sources give them.
- This is research support, not medical advice. Don't give dosing or treatment advice for a person.
- Clear markdown, short sections.

How to write (layered: easy first, then deeper; never dumbed down):
1. Lead with the bottom line in plain language: 1-2 sentences a smart non-specialist understands on first read.
   In markdown answers, start with a paragraph that begins "**In short:**".
2. Then the key details and reasoning.
3. Then the technical specifics for experts: study designs, sample sizes, effect sizes, measures, mechanisms, citations.
- Keep technical terms and numbers; don't replace them with vague words. The first time a technical term appears,
  add a brief plain gloss in parentheses, e.g. "HbA1c (average blood sugar over ~3 months)".
- Short sentences, active voice, one idea per sentence. No filler, no childish phrasing, no hype."""


def fmt_sources(papers=(), trials=(), preprints=(), max_chars=1600):
    out = []
    for p in papers:
        out.append(f"[PMID {p['pmid']}] {p.get('title')} — {p.get('journal')} {p.get('year')}. "
                   f"Type: {', '.join(p.get('types') or []) or 'n/a'}\n{(p.get('abstract') or '(no abstract)')[:max_chars]}")
    for p in preprints:
        out.append(f"[PPR {p['id']}] (PREPRINT, {p.get('server')}, {p.get('date')}) {p.get('title')}\n"
                   f"{(p.get('abstract') or '(no abstract)')[:max_chars]}")
    for t in trials:
        out.append(f"[{t['nct']}] TRIAL: {t['title']} | {t.get('status')} | "
                   f"{', '.join(t.get('phases') or []) or 'phase n/a'} | n={t.get('enrollment')} | "
                   f"interventions: {', '.join(x for x in t.get('interventions') or [] if x)} | "
                   f"sponsor: {t.get('sponsor')} | start {t.get('start')}")
    return "\n\n".join(out) or "(no sources found)"


def fmt_trend(trend):
    return ", ".join(f"{t['year']}: {t['count']}" for t in trend) if trend else "(not available)"


def research_brief(question, papers, trials, preprints, trend, goal=""):
    prompt = f"""RESEARCH QUESTION: {question}
{f"PROJECT GOAL: {goal}" if goal else ""}
PUBMED PAPERS PER YEAR (topic momentum): {fmt_trend(trend)}

SOURCES:
{fmt_sources(papers, trials, preprints)}

Write a research brief:
## Bottom line
3-4 sentences answering the question as well as the evidence allows.
## What's known
Main findings grouped by theme, with citations.
## Momentum
Is this area growing, flat or cooling (use the per-year counts)? What do the newest preprints suggest?
## Clinical pipeline
What's being tested in humans: phases, status, sponsors. Say so if there's nothing.
## Strength of evidence
Study types, sizes, consistency, and the biggest gaps.
## Read first
The 3-5 most useful sources and why."""
    return call(SYSTEM, prompt)


def find_gaps(topic, papers, trials, preprints, trend, goal=""):
    prompt = f"""TOPIC: {topic}
{f"PROJECT GOAL: {goal}" if goal else ""}
PUBMED PAPERS PER YEAR: {fmt_trend(trend)}

SOURCES:
{fmt_sources(papers, trials, preprints, max_chars=1000)}

Find research opportunities in this area, grounded in the sources:
## Unanswered questions
Questions the sources raise but don't answer (cite what raises each one).
## Under-studied angles
Populations, outcomes, comparisons or settings that are missing from the trials and studies.
## Where AI could help
Concrete ways machine learning or data analysis could move this area forward (e.g. better patient
selection, outcome prediction, literature synthesis, trial matching), and what data it would need.
## Project ideas for students
3 realistic projects a student researcher could start (literature reviews, data analyses of public datasets,
tools), each with a first step.
Mark everything here as ideas, not established facts."""
    return call(SYSTEM, prompt)


def ask_project(question, history, papers, trials, preprints, notes, goal=""):
    context = f"""PROJECT GOAL: {goal or "(none set)"}

SAVED SOURCES:
{fmt_sources(papers, trials, preprints)}

SAVED NOTES / BRIEFS:
{notes[:12000] or "(none)"}"""
    msgs = [{"role": "user", "content": context + "\n\nI'll ask questions about this project."},
            {"role": "assistant", "content": "Got it. Ask away, and I'll answer from the saved sources and cite them."}]
    for h in history[-10:]:
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": question})
    return call(SYSTEM, msgs, max_tokens=3000)


def whats_new(topic, papers, trials, preprints, since):
    prompt = f"""WATCHED TOPIC: {topic}
NEW SINCE {since}:

{fmt_sources(papers, trials, preprints, max_chars=900)}

Write a short digest: ## Headline (1-2 sentences on the most important new thing),
## New papers, ## New preprints (label as not peer reviewed), ## Trial updates. One line per item with
its citation and why it matters. If nothing meaningful is new, say so in one line."""
    return call(SYSTEM, prompt, max_tokens=2500)


RADAR_PICK = """From these recent papers and preprints in the field, list the {n} most promising EMERGING
research topics: specific, new-ish ideas or approaches that are picking up (not broad old fields).
Favor topics where AI / computation meets biomedicine when the sources support it.

For each, write a PubMed search query that captures it (use quotes for phrases, OR for synonyms, keep it
specific). Submit using the tool, in this shape:
[{{"name": "short topic name", "why": "one line on what's new, citing a source", "query": "pubmed query"}}]

FIELD: {field}

SOURCES:
{sources}"""


def radar_candidates(field, papers, preprints, n=10):
    out = call_json(SYSTEM, RADAR_PICK.format(n=n, field=field, sources=fmt_sources(papers, (), preprints, max_chars=500)),
                    obj({"topics": {"type": "array", "items": obj({"name": STR, "why": STR, "query": STR})}}), 3000)
    return [t for t in out.get("topics", []) if isinstance(t, dict) and t.get("query")][:n]


def radar_report(field, topics, goal=""):
    rows = "\n".join(f"- {t['name']}: papers/yr {t['counts']} | growth {t['growth']}x | {t.get('why','')}"
                     for t in topics)
    prompt = f"""FIELD: {field}
{f"PROJECT GOAL: {goal}" if goal else ""}
EMERGING TOPICS, RANKED BY MEASURED GROWTH (PubMed papers per complete year; growth = last 2 years vs the
2 years before):
{rows}

Write the radar report:
## Biggest movers
The 3 fastest-rising topics: what they are in plain words, why they're taking off, and how solid the signal is
(small counts = early and noisy).
## Sleepers
Topics with small numbers but steep growth, worth watching early.
## Where AI fits
Which of these are driven by AI/computation, and where AI could push next.
## What to do next
3 concrete moves for two students who want to get in early (what to read, track or build).
Use the numbers. Mark predictions as predictions."""
    return call(SYSTEM, prompt, max_tokens=4000)


BRIDGE_PICK = """Literature-based discovery (Swanson's ABC method). Topic A and topic B. From the papers about A
and the papers about B, list {n} intermediate concepts C (biological processes, pathways, biomarkers,
mechanisms, data types or methods) that plausibly connect both. Prefer specific concepts.
Submit using the tool, in this shape: [{{"name": "concept", "query": "pubmed query for the concept"}}]

A: {a}
B: {b}

PAPERS ABOUT A:
{pa}

PAPERS ABOUT B:
{pb}"""


def bridge_candidates(a, b, papers_a, papers_b, n=8):
    out = call_json(SYSTEM, BRIDGE_PICK.format(n=n, a=a, b=b, pa=fmt_sources(papers_a, max_chars=400),
                                               pb=fmt_sources(papers_b, max_chars=400)),
                    obj({"concepts": {"type": "array", "items": obj({"name": STR, "query": STR})}}), 2500)
    return [c for c in out.get("concepts", []) if isinstance(c, dict) and c.get("query")][:n]


def bridge_report(a, b, counts, links, papers_ab, goal=""):
    rows = "\n".join(f"- C = {l['name']}: A+C {l['ac']} papers, B+C {l['bc']} papers, A+B+C {l['abc']} papers, "
                     f"hidden-link score {l['score']}" for l in links)
    prompt = f"""A = {a}
B = {b}
{f"PROJECT GOAL: {goal}" if goal else ""}
PUBMED COUNTS: A alone {counts['a']}, B alone {counts['b']}, A and B together {counts['ab']} (last 3 years: {counts['ab_recent']}).

CANDIDATE BRIDGES (high score = both sides well studied, but rarely studied together with A and B):
{rows}

PAPERS ALREADY LINKING A AND B:
{fmt_sources(papers_ab, max_chars=600) or "(none)"}

Write a crossover report:
## How connected A and B already are
Use the counts and the linking papers.
## Hidden links
The 3 strongest bridges: the A→C link and the C→B link in plain words, and the untested idea it suggests.
These are HYPOTHESES generated from publication patterns, not findings. Say what evidence would test each.
## The opportunity
Why this crossover could matter, and what a first study or analysis could look like.
Cite the linking papers where you use them."""
    return call(SYSTEM, prompt, max_tokens=4000)


# ======================= Idea Lab =======================
IDEA_RULES = """Idea Lab rules:
- You propose RESEARCH HYPOTHESES for treating, preventing or detecting a condition, grounded in the sources.
- Ideas must build on existing, documented approaches: approved medicines used for a new purpose, combinations of
  existing treatments, devices, diagnostics, AI/digital tools, lifestyle or prevention strategies, and care models.
  Do not invent new chemical compounds, describe chemical structures, or give doses, synthesis or lab protocols.
- Stay therapeutic and patient-benefit focused. Every idea is a hypothesis for experts to evaluate, not advice.
- When the sources are thin or missing, don't stop: propose ideas from established biomedical knowledge and from
  what works in related conditions or fields, and label them clearly as speculative. Gaps are where breakthroughs are.

How to think (use all three lenses before you answer):
1. Direct evidence: what the sources show for THIS condition.
2. Borrowed from elsewhere: what already works for a related condition, a shared mechanism, another patient group,
   or another field (engineering, behavioral science, other diseases) and could transfer here.
3. First principles: reason from the biology, the patient's daily life and the care system to find the leverage point
   nobody is targeting yet.
"There's no research on this" is never the final answer: it's the starting point for an original approach.

Confidence grades (how sure we are the approach would actually help):
A = strong direct evidence for this condition (trials or multiple studies agree)
B = promising: some direct evidence plus solid support in related settings
C = plausible: a logical chain backed only by indirect evidence
D = original: little or no research yet; Medr's own reasoning, worth testing"""


def idea_mechanisms(condition, papers, trials, preprints, n=6):
    prompt = f"""{IDEA_RULES}

CONDITION: {condition}

SOURCES:
{fmt_sources(papers, trials, preprints, max_chars=700)}

Map the biology and the care gaps of this condition from the sources. List the {n} most important
processes, pathways, risk factors or unmet clinical needs that a new approach could target.
Submit using the tool, in this shape: [{{"name": "short name", "why": "one sentence with citation",
  "terms": ["2-5 short synonyms researchers use for it, 1-4 words each"]}}]"""
    out = call_json(SYSTEM, prompt, obj({"targets": {"type": "array", "items": obj({"name": STR, "why": STR, "terms": STRS}, ["name", "terms"])}}), 3000)
    return [m for m in out.get("targets", []) if isinstance(m, dict) and m.get("name")][:n]


def adjacent_topics(condition, n=3):
    """When direct research is thin: neighbouring conditions/mechanisms whose research could transfer."""
    prompt = f"""Very little research exists for: {condition}
Name {n} ADJACENT research areas whose findings could transfer to it: a related condition, a shared mechanism, the same
patient group with a different problem, or another field that solved a similar problem. For each: name, why it transfers
(one sentence), and a PubMed query for its reviews. Submit using the tool."""
    out = call_json(SYSTEM, prompt, obj({"areas": {"type": "array", "items": obj({"name": STR, "why": STR, "query": STR})}}), 1200, model=FAST)
    return [a for a in out.get("areas", []) if isinstance(a, dict) and a.get("query")][:n]


def idea_generate_all(condition, angle, papers, trials, preprints, adjacent=None, n=6):
    """One call instead of two: map the key targets AND generate ideas that act on them."""
    adj = ("ADJACENT EVIDENCE (related conditions / mechanisms, for borrowing ideas):\n"
           + fmt_sources(adjacent, max_chars=350) + "\n") if adjacent else ""
    prompt = f"""{IDEA_RULES}

THE USER'S IDEA OR CHALLENGE: {condition}
FOCUS: {ANGLES.get(angle, ANGLES['any'])}
If this is a specific idea (a way to use something to help someone), your FIRST idea must be the user's own idea,
sharpened: keep their core intent, make it precise and testable, and title it as theirs. The other ideas are strong
variations and alternatives that attack the same problem. If it's just a topic, generate ideas for that topic.

SOURCES:
{fmt_sources(papers, trials, preprints, max_chars=550) or "(very little direct research found)"}

{adj}
1. targets: the 5 most important processes, pathways, risk factors or unmet clinical needs a new approach could
   target. Each: name, why (one sentence with citation), terms (2-5 short synonyms researchers use).
2. ideas: {n} distinct, non-obvious hypotheses, each acting on one target. Use all three lenses (at least 2 borrowed
   from another condition or field, at least 1 from first principles). Each:
   title, type (Repurposing | Combination | AI / digital | Diagnostic | Prevention | Care model), intervention (2-6 words),
   intervention_terms (1-2 concepts, each a list of 2-5 short synonyms), target (exact target name),
   origin (Direct evidence | Borrowed from another field | First principles), borrowed_from,
   plain (ONE plain-language sentence: what the idea is and why it might help, no jargon),
   rationale (2 short technical sentences: mechanism and evidence, cite).
Keep every field short. Submit using the tool."""
    t_item = obj({"name": STR, "why": STR, "terms": STRS}, ["name", "terms"])
    i_item = obj({"title": STR, "type": {"type": "string", "enum": ["Repurposing", "Combination", "AI / digital", "Diagnostic",
                                                               "Prevention", "Care model"]},
                  "intervention": STR, "intervention_terms": {"type": "array", "items": STRS}, "target": STR, "rationale": STR,
                  "origin": {"type": "string", "enum": ["Direct evidence", "Borrowed from another field", "First principles"]},
                  "borrowed_from": STR, "plain": STR, "yours": {"type": "boolean"}}, ["title", "type", "intervention", "target", "plain", "rationale"])
    out = call_json(SYSTEM, prompt, obj({"targets": {"type": "array", "items": t_item}, "ideas": {"type": "array", "items": i_item}}), 5000)
    targets = [m for m in out.get("targets", []) if isinstance(m, dict) and m.get("name")][:6]
    ideas = [i for i in out.get("ideas", []) if isinstance(i, dict) and i.get("intervention")][:n]
    return targets, ideas


ANGLES = {
    "any": "a mix of all idea types",
    "repurpose": "new uses for medicines that are already approved for other conditions",
    "ai": "AI, machine learning and digital tools",
    "diagnostics": "diagnostics and early detection",
    "prevention": "prevention, lifestyle and risk reduction",
    "combination": "combinations of existing treatments",
}


def idea_generate(condition, angle, mechanisms, papers, trials, preprints, n=8, adjacent=None):
    adj = ("ADJACENT EVIDENCE (related conditions / mechanisms, for borrowing ideas):\n"
           + fmt_sources(adjacent, max_chars=400) + "\n") if adjacent else ""
    mech = "\n".join(f"- {m['name']}: {m.get('why', '')}" for m in mechanisms)
    prompt = f"""{IDEA_RULES}

CONDITION: {condition}
FOCUS: {ANGLES.get(angle, ANGLES['any'])}

KEY TARGETS (processes / needs):
{mech}

SOURCES:
{fmt_sources(papers, trials, preprints, max_chars=600) or "(very little direct research found)"}

{adj}
Generate {n} distinct, non-obvious hypotheses. Favor ideas that are under-explored for THIS condition but supported
in other contexts. Each must link to one of the key targets. Use all three lenses: include at least 2 ideas
borrowed from another condition or field and at least 1 from first principles.
Submit using the tool, in this shape:
[{{"title": "short headline",
   "type": "Repurposing" | "Combination" | "AI / digital" | "Diagnostic" | "Prevention" | "Care model",
   "intervention": "the existing approach, in plain words (2-6 words)",
   "intervention_terms": [["1-2 concepts that describe the approach; each concept is a list of 2-5 short synonyms
                           (1-4 words each) researchers would write, e.g. [["machine learning","deep learning","AI"],["retinal imaging","fundus photography"]]"]],
   "target": "exact name of the key target it acts through",
   "origin": "Direct evidence" | "Borrowed from another field" | "First principles",
   "borrowed_from": "if borrowed: the condition or field it comes from, else empty",
   "rationale": "2-3 short sentences: the approach and why it could work, with citations"}}]"""
    item = obj({"title": STR, "type": {"type": "string", "enum": ["Repurposing", "Combination", "AI / digital", "Diagnostic",
                                                             "Prevention", "Care model"]},
                "intervention": STR, "intervention_terms": {"type": "array", "items": STRS}, "target": STR, "rationale": STR,
                "origin": {"type": "string", "enum": ["Direct evidence", "Borrowed from another field", "First principles"]},
                "borrowed_from": STR}, ["title", "type", "intervention", "intervention_terms", "target", "rationale"])
    out = call_json(SYSTEM, prompt, obj({"ideas": {"type": "array", "items": item}}), 6000)
    return [i for i in out.get("ideas", []) if isinstance(i, dict) and i.get("intervention")][:n]


def idea_dossier(condition, ideas, counts_note):
    rows = "\n".join(
        f"#{k + 1} {i['title']} ({i['type']}) | intervention: {i['intervention']} | target: {i['target']} | "
        f"papers on idea+condition: {i['m']['co']} ({i['m']['co_recent']} in last 3 yrs) | "
        f"papers linking intervention to target: {i['m']['link_i']} | condition to target: {i['m']['link_c']} | "
        f"matching trials: {i['m']['trials']} | promise {i['m']['promise']}/100\nRationale: {i['rationale']}"
        for k, i in enumerate(ideas))
    prompt = f"""{IDEA_RULES}

CONDITION: {condition}
VALIDATION DATA ({counts_note}):
{rows}

Write the dossier. Submit using the tool, in this shape:
{{"summary_md": "markdown: start with a paragraph beginning **In short:** (2 plain sentences on the best leads), then ## Headline findings (3-4 bullets on the most promising ideas and why; refer to ideas by their
   title in bold, never by number), ## Caveats (what the numbers can and can't tell us)",
  "ideas": [{{"k": the idea's number from above (#1 = 1),
     "verdict": "Unexplored" | "Emerging" | "Being tested" | "Well studied",
     "grade": "A" | "B" | "C" | "D" (see the confidence grades above; be strict and use the real counts),
     "grade_reason": "one plain sentence explaining the grade, citing the numbers (e.g. 0 papers on this combo, 41 linking it to the target)",
     "chain": ["condition → target link, with citation if available", "target → intervention link, with citation if available"],
     "test_plan": ["exactly 3 short, concrete steps to pursue it, in order (e.g. systematic review of X, analysis of public
                   dataset Y, consult experts in Z, design a first comparison). No lab protocols or doses."],
     "proof": "the result that would show it works, in one sentence",
     "risks": ["1-2 main reasons it might fail"],
     "question": "the single most important open question"}}]}}
Keep every sentence short and plain."""
    item = obj({"k": {"type": "integer"}, "verdict": {"type": "string", "enum": ["Unexplored", "Emerging", "Being tested", "Well studied"]},
                "grade": {"type": "string", "enum": ["A", "B", "C", "D"]}, "grade_reason": STR,
                "chain": STRS, "test_plan": STRS, "proof": STR, "risks": STRS, "question": STR}, ["k", "grade", "grade_reason", "test_plan"])
    return call_json(SYSTEM, prompt, obj({"summary_md": STR, "ideas": {"type": "array", "items": item}}), 5000, model=FAST)


PLAN_PROMPT = """{rules}

CONDITION: {condition}
STUDENT'S GOAL: {goal}

IDEA LAB RESULTS (ranked; promise 0-100, real PubMed/trial counts):
{ideas}

EXTRA EVIDENCE FOUND FOR THE LEADING IDEAS:
{extra}

Write a BREAKTHROUGH PLAN for a motivated undergrad (computer engineering, can build software and AI).
Focus on ideas with high potential upside even if the evidence is still thin. Be honest about what is speculative.
Style: clean and scannable. Short sentences, no walls of text, every lead under 130 words. Markdown, exactly these sections:
## The leads worth chasing
For the top 3-4 leads, each as a ### heading (the lead's short name), then exactly these lines:
**In short:** one plain-language sentence a non-specialist understands.
**The bet:** one technical sentence: what it is and the non-obvious insight.
**Confidence: X**: one sentence why (X = A, B, C or D using the grades above; use the real counts).
**Approach:**
1. three numbered first steps a student can actually take (reviews, interviews, prototypes, public datasets,
   finding mentors; anything with patients needs a supervising expert and ethics approval)
**Proof point:** the one result that would show it's working.
## One idea of my own
A ### heading, then the same four lines. Grade it honestly (usually D).
## Leads to park
A short bullet per idea: the idea in bold, then why (one line).
## Your first move
Which lead to start with and the very first action this week, in 2-3 sentences.
Cite sources inline as [PMID x], [NCT...], or markdown links when given."""


def breakthrough_plan(condition, goal, ideas, extra_papers):
    rows = "\n".join(
        f"#{k + 1} {i.get('title')} ({i.get('type')}) promise {i.get('m', {}).get('promise')}/100 | "
        f"papers on idea {i.get('m', {}).get('co')} | link evidence {i.get('m', {}).get('link_i')} | "
        f"trials {i.get('m', {}).get('trials')} | verdict {i.get('verdict')} | confidence grade {i.get('grade', '?')}"
        f" ({i.get('origin') or 'n/a'})\n  {i.get('rationale', '')}\n"
        f"  test ideas: {'; '.join(i.get('test_plan') or [])}" for k, i in enumerate(ideas))
    prompt = PLAN_PROMPT.format(rules=IDEA_RULES, condition=condition, goal=goal or "(not set)", ideas=rows,
                                extra=fmt_sources(extra_papers, max_chars=700) if extra_papers else "(none)")
    return call(SYSTEM, prompt, max_tokens=3500)


# ======================= Smart search: read what the user actually asked =======================
INTERPRET = """You turn a person's search into precise biomedical search concepts.
Rules:
- Keep EVERY idea the person mentioned as its own concept. Never drop, merge or generalize one
  (if they say "children", "mental health" and "type 1 diabetes", that's three concepts).
- BUT alternatives are ONE concept: things joined by "or", or listed as examples ("such as", "like", "e.g.",
  "for example") go into a single concept whose terms include every alternative.
  "early-stage Alzheimer's or Parkinson's" = ONE concept with terms like Alzheimer's disease, Parkinson's disease,
  mild cognitive impairment, prodromal Parkinson's. Examples never become separate required concepts.
- Each term is short (1-4 words): a phrase researchers would actually write, never a whole sentence.
- Don't add concepts they didn't ask for.
- For each concept give 2-6 terms researchers actually use in titles/abstracts: synonyms, spellings,
  abbreviations, plural/child forms (e.g. "children", "pediatric", "paediatric", "adolescents", "youth").
- mesh: the single best-fitting MeSH heading, or "" if unsure.
- importance: 3 = the core subject, 2 = a key qualifier, 1 = a nice-to-have detail.
- trial_condition: the disease/condition (plain words), trial_intervention: a treatment if one is named else "",
  trial_other: any other words worth matching in trials (population, outcome) else "".
- meaning: one plain sentence saying what they're looking for."""

CONCEPT = obj({"name": STR, "terms": STRS, "mesh": STR, "importance": {"type": "integer"}})
INTERP_SCHEMA = obj({"meaning": STR, "concepts": {"type": "array", "items": CONCEPT},
                     "trial_condition": STR, "trial_intervention": STR, "trial_other": STR})


def interpret_search(text):
    prompt = f"{INTERPRET}\n\nSEARCH: {text}"
    last = None
    for model in (FAST, MODEL if not FAST_MODE else FAST):
        try:
            out = call_json(INTERPRET, prompt, INTERP_SCHEMA, 1500, model=model)
            cs = [c for c in out.get("concepts", []) if isinstance(c, dict) and (c.get("terms") or c.get("name"))]
            if cs:
                out["concepts"] = cs
                return out
        except Exception as e:
            last = e
    raise ValueError(f"couldn't read the search: {last}")


GAP_PROMPT = """{rules}

The student searched for: {q}
Meaning: {meaning}
Papers matching EVERYTHING they asked for: {exact}
Closest papers found (may only match part of the search):
{papers}

Little research answers this exact question. That's not a dead end: it's where breakthroughs come from.
Think through all three lenses (direct evidence, borrowed from elsewhere, first principles), then:
1. assessment: 2 short sentences: what IS known (cite close papers like [PMID x]) and the exact gap.
2. approaches: {n} original, testable approaches to fill the gap. Mix the lenses: at least one borrowed from another
   condition or field, at least one from first principles. For each:
   title (short headline), type, origin (the lens), borrowed_from (if borrowed, from where), grade (A-D, strict:
   with little direct research most will be C or D), grade_reason (one sentence), plain (ONE plain-language sentence:
   what it is and why it might help), approach (2 short technical sentences: mechanism and supporting evidence),
   steps (exactly 3 concrete first steps, in order),
   proof (the result that would show it works).
3. related_searches: 3 nearby searches likely to find useful evidence (label + query + why, one short line each).
Keep every sentence short and plain. Submit using the tool."""

GAP_SCHEMA = obj({"assessment": STR, "approaches": {"type": "array", "items": obj({
    "title": STR, "type": {"type": "string", "enum": ["Repurposing", "Combination", "AI / digital", "Diagnostic",
                                                      "Prevention", "Care model"]},
    "origin": {"type": "string", "enum": ["Direct evidence", "Borrowed from another field", "First principles"]},
    "borrowed_from": STR, "grade": {"type": "string", "enum": ["A", "B", "C", "D"]}, "grade_reason": STR,
    "plain": STR, "approach": STR, "steps": STRS, "proof": STR}, ["title", "type", "grade", "grade_reason", "plain", "approach", "steps", "proof"])},
    "related_searches": {"type": "array", "items": obj({"label": STR, "query": STR, "why": STR})}},
    ["assessment", "approaches"])


def propose_from_gap(q, meaning, papers, exact, n=3):
    prompt = GAP_PROMPT.format(rules=IDEA_RULES, q=q, meaning=meaning or q, exact=exact, n=n,
                               papers=fmt_sources(papers, max_chars=500) if papers else "(none found)")
    return call_json(SYSTEM, prompt, GAP_SCHEMA, 3000)


# ======================= Plan coach: one step at a time =======================
COACH_RULES = IDEA_RULES + """
Advisor rules:
- You are a senior research advisor supervising an undergraduate researcher (computer engineering; can code and build
  AI tools) working mostly alone. Write like a professional research document: precise, neutral, no hype, no
  cheerleading, no exclamation marks, no second-person pep talk. Task titles are short imperative phrases
  (e.g. "Map validated distress measures for adolescents with T1D").
- Give ONE concrete step at a time that takes about 1-5 hours and has a clear finish line.
- Good steps: reading specific reviews, searching PubMed/ClinicalTrials.gov with given queries, mapping what
  exists, finding public datasets, building a software prototype or analysis, writing a one-page proposal,
  finding and emailing researchers, clinicians or patient organizations, applying to research programs.
- Anything involving patients, families or health data needs a supervising expert and ethics (IRB) approval: say
  so in that step. No lab protocols, doses or medical advice.
- Guidance must be specific enough that a beginner is never lost: exact search queries, what to look for,
  what to write down, and what "done" looks like."""

STEP = obj({"title": STR, "why": STR, "how": STRS, "done_when": STR, "time": STR,
            "resources": {"type": "array", "items": obj({"label": STR, "kind": {"type": "string", "enum": ["pubmed", "trials", "web", "medr"]},
                                                          "query": STR})},
            "tips": STRS})
STEP_FIELDS = """A step has: title (imperative, short), why (1-2 plain sentences: why this task matters now),
how (3-6 numbered-style instructions, specific), done_when (the deliverable), time (e.g. "2 hours"),
resources (1-4 items: kind "pubmed"/"trials" with a search query, "medr" for a Medr search, "web" for a web search;
label = what it is), tips (1-3 short beginner tips or pitfalls)."""


def mission_start(condition, goal, plan_md, lead):
    prompt = f"""{COACH_RULES}

CONDITION: {condition}
STUDENT'S GOAL: {goal or "(not set)"}
THE PLAN:
{plan_md[:9000]}

The student chose to pursue: {lead}
Set up the mission:
- lead: the lead's short name
- mission: one sentence describing the breakthrough this mission is driving at
- roadmap: 5-7 milestone names from now to a real result (e.g. "Map the evidence", "Talk to 3 clinicians",
  "Build the prototype", "Pilot proposal with a mentor"); the first milestone is where step 1 lives
- step: the very first step, fully detailed.
{STEP_FIELDS}
Submit using the tool."""
    return call_json(SYSTEM, prompt, obj({"lead": STR, "mission": STR, "roadmap": STRS, "step": STEP}), 2500)


def _history(steps):
    out = []
    for k, s in enumerate(steps):
        out.append(f"Step {k + 1}: {s.get('title')} [{s.get('status')}]\n  done when: {s.get('done_when')}\n"
                   f"  student's notes: {s.get('note') or '(none)'}")
    return "\n".join(out)


def mission_next(condition, goal, plan_md, mission, steps, swap_reason=""):
    task = (f"The student wants a DIFFERENT step instead of the current one. Reason: {swap_reason or 'not given'}. "
            "Give a replacement that still moves the mission forward.") if swap_reason else (
            "The student just finished the last step. React to their notes (feedback: 1-3 sentences, specific, "
            "an advisor's review: neutral, specific, evidence-focused; state what the findings establish and what they don't; if they indicate a problem or a pivot, say so plainly). Then give the next step, "
            "building on what they found.")
    prompt = f"""{COACH_RULES}

CONDITION: {condition}
STUDENT'S GOAL: {goal or "(not set)"}
MISSION: {mission.get('mission')} (lead: {mission.get('lead')})
ROADMAP: {"; ".join(mission.get('roadmap') or [])}
PLAN (for context):
{plan_md[:5000]}

STEPS SO FAR:
{_history(steps)}

{task}
- roadmap: the roadmap, updated if what they learned changes the route (keep it 5-7 milestones)
- milestone: which roadmap milestone the new step belongs to
- finished: true only if the mission has reached a real result and the best next move is a new mission
{STEP_FIELDS}
Submit using the tool."""
    return call_json(SYSTEM, prompt, obj({"feedback": STR, "roadmap": STRS, "milestone": STR, "finished": {"type": "boolean"},
                                          "step": STEP}, ["feedback", "roadmap", "step"]), 2500)


def mission_help(condition, mission, step, question):
    prompt = f"""{COACH_RULES}

CONDITION: {condition}
MISSION: {mission.get('mission')} (lead: {mission.get('lead')})
CURRENT STEP: {step.get('title')}
How it was described: {" | ".join(step.get('how') or [])}
Done when: {step.get('done_when')}

The student asks: {question or "I'm stuck on this step. Walk me through it."}

Answer as a research advisor would: concrete and precise, short paragraphs or a numbered walkthrough,
example search queries or example wording where useful. Markdown. Under 300 words."""
    return call(SYSTEM, prompt, max_tokens=1000)


# ======================= News =======================
NEWS_CATS = ["Approvals & policy", "Clinical trials", "Research", "Biotech & pharma", "Public health", "AI & tech"]


def news_top(items, n=8):
    rows = "\n".join(f"[{k}] {i['title']} ({i['source']}){' | ' + i['summary'][:180] if i.get('summary') else ''}"
                     for k, i in enumerate(items))
    prompt = f"""You're the editor of a daily briefing on health, medicine and biomedical science for a student
who wants to work on medical breakthroughs.

HEADLINES FROM THE LAST FEW DAYS:
{rows}

Pick the {n} BIGGEST stories: the ones that change what doctors do, what patients can get, or what scientists
think is possible (approvals, decisive trial results, major discoveries, big policy or funding shifts, outbreaks).
Skip lifestyle tips, opinion pieces, small single-center studies and company PR with no data.
If several headlines cover the same story, pick the best one and list the others in "also".
For each: k (headline number), category (one of {NEWS_CATS}), why ("why it matters" in one or two plain sentences),
importance (1-5, 5 = historic). Also: digest (2-3 sentences on the overall picture this week).
Submit using the tool."""
    item = obj({"k": {"type": "integer"}, "category": {"type": "string", "enum": NEWS_CATS}, "why": STR,
                "importance": {"type": "integer"}, "also": {"type": "array", "items": {"type": "integer"}}}, ["k", "category", "why", "importance"])
    return call_json(SYSTEM, prompt, obj({"digest": STR, "top": {"type": "array", "items": item}}), 3000, model=FAST)


# ======================= Breakthrough alerts =======================
JUDGE = """You decide whether new items are BREAKTHROUGHS for topics a student is researching. The bar is very high:
most days nothing qualifies, and that's the right answer.

level "breakthrough" ONLY for: a regulatory approval of a new treatment/test, a positive phase 3 or large randomized
trial, a large meta-analysis or guideline that changes standard care, a first-ever result (first-in-human success,
first cure/remission, a new mechanism confirmed in humans), or a trial on the topic finishing with reported results.
level "notable": clearly important progress worth a look, but not practice-changing.
level "none": everything else (small studies, reviews, early lab or animal work, commentary, routine news).

TOPICS THE STUDENT IS RESEARCHING:
{topics}

NEW ITEMS:
{items}

Return every item that is "breakthrough" or "notable" (skip "none"). For each: id (the item id), level, topic (which
topic it belongs to), headline (plain words, under 90 characters), why (1-2 sentences: what happened and why it matters
for that topic). Submit using the tool."""


def judge_breakthroughs(topics, items):
    rows = "\n\n".join(f"id {i['cid']} | {i['kind']} | {i['title']} | {i.get('meta', '')}\n{(i.get('text') or '')[:600]}"
                       for i in items)
    prompt = JUDGE.format(topics="\n".join(f"- {t}" for t in topics), items=rows)
    item = obj({"id": STR, "level": {"type": "string", "enum": ["breakthrough", "notable"]}, "topic": STR,
                "headline": STR, "why": STR})
    return call_json(SYSTEM, prompt, obj({"alerts": {"type": "array", "items": item}}), 2500, model=FAST).get("alerts", [])


# ======================= explain + rank search results =======================
EXPLAIN = """A student searched for: {q}
What they're looking for: {meaning}

For each result below, write in plain words a high-school student would follow:
- premise: one sentence: what the study (or trial) actually did and found, in plain words; keep the key number or design
  if it matters (e.g. "a 2-year trial of 400 teens") and gloss any technical term briefly.
- connection: one sentence: how it relates to what the student searched for, specifically. If it only touches part of
  the search, say which part and what's missing.
- relevance: 1-5, how directly it answers the student's search (5 = squarely on it, 3 = covers part of it,
  1 = only shares a keyword). Be strict.
- breakthrough: 1-5, how much it could move the field toward a breakthrough for this topic (5 = new method or decisive
  result, 3 = solid useful evidence, 1 = minor or descriptive).
Return one entry per result, using its k. Submit using the tool.

RESULTS:
{rows}"""


def explain_results(q, meaning, items):
    rows = "\n\n".join(f"id={i['id']} [{i.get('kind', 'paper')}] {i.get('title')}\n{(i.get('text') or '')[:900]}" for i in items)
    item = obj({"id": STR, "premise": STR, "connection": STR,
                "relevance": {"type": "integer"}, "breakthrough": {"type": "integer"}})
    prompt = EXPLAIN.format(q=q, meaning=meaning or q, rows=rows).replace("using its k", "using its exact id")
    out = call_json(SYSTEM, prompt, obj({"items": {"type": "array", "items": item}}),
                    min(8000, 600 + 300 * len(items)), model=FAST)
    got = out.get("items") or out.get("results") or []
    return got if isinstance(got, list) else []


# ======================= Assessment Finder (validated measures for measurement-based care) =======================
PURPOSES = {"both": "screening and tracking change over time (measurement-based care)", "screening": "screening / case finding",
            "monitoring": "tracking symptom change over time during treatment (measurement-based care)"}


def measure_candidates(population, purpose, setting, papers):
    prompt = f"""POPULATION / CONDITION: {population}
PURPOSE: {PURPOSES.get(purpose, PURPOSES['both'])}
SETTING: {setting or "any"}

SOURCES (recent papers on measurement in this population):
{fmt_sources(papers, max_chars=500) or "(few direct sources)"}

List the 6-9 most relevant VALIDATED, standardized assessment instruments (patient-reported, parent-reported or
clinician-rated scales) for this population and purpose. Prefer instruments widely used in measurement-based care and
collaborative care, and include population-specific ones when they exist (e.g. a disease-specific distress scale).
Only list real, published instruments. For each:
name (full name), abbr, construct (what it measures, plain words), ages (age range it is validated for, or "adults"),
length (number of items and rough minutes), respondent ("self", "parent/caregiver", "clinician" or a combination),
access ("public domain", "free with permission", "licensed/paid" or "unknown": say unknown if not sure),
terms (2-4 search terms: the abbreviation and full name variants used in papers).
Submit using the tool."""
    item = obj({"name": STR, "abbr": STR, "construct": STR, "ages": STR, "length": STR, "respondent": STR,
                "access": STR, "terms": STRS}, ["name", "abbr", "construct", "terms"])
    out = call_json(SYSTEM, prompt, obj({"measures": {"type": "array", "items": item}}), 2500, model=FAST)
    return [m for m in out.get("measures", []) if isinstance(m, dict) and m.get("name")][:9]


def measures_report(population, purpose, setting, measures, papers):
    rows = "\n".join(f"- {m['abbr'] or m['name']} ({m['name']}): measures {m.get('construct')}; ages {m.get('ages')}; "
                     f"{m.get('length')}; respondent {m.get('respondent')}; access {m.get('access')} | "
                     f"papers using it in this population: {m['n_pop']} | validation papers in this population: {m['n_val']}"
                     f"{' (counted without: ' + ', '.join(m['loosened']) + ')' if m.get('loosened') else ''}"
                     for m in measures)
    prompt = f"""{IDEA_RULES}

POPULATION / CONDITION: {population}
PURPOSE: {PURPOSES.get(purpose, PURPOSES['both'])}
SETTING: {setting or "any"}

CANDIDATE INSTRUMENTS WITH REAL PUBMED COUNTS:
{rows}

SOURCES (validation and measurement papers):
{fmt_sources(papers, max_chars=700)}

Write an assessment recommendation a behavioral-health program could act on.
1. summary_md: markdown: a paragraph beginning **In short:** (plain words: which questionnaires to use and why), then
   3-5 bullets with the technical detail: the recommended core set (which instruments, for what), and the main caveat.
2. measures: for EACH candidate (use its abbr): fit_grade (A-D, using the confidence grades: A = validated in this
   population with multiple studies; D = little or no validation here), fit_reason (one sentence, cite counts or sources),
   use_for ("Screening", "Tracking change" or "Both"), thresholds (cutoff scores and how response/remission are usually
   defined for this instrument, cited [PMID x] when the sources support it; if not established for this population say
   exactly that), cadence (how often to administer in measurement-based care), cautions (one sentence).
3. gaps: 2-4 measurement gaps for this population (e.g. no validated instrument for a construct or age group), each with
   an approach to close it and a confidence grade.
Professional register, short sentences. Never invent cutoff numbers: only give numbers you are confident are standard
for that instrument or that the sources state. Submit using the tool."""
    m_item = obj({"abbr": STR, "fit_grade": {"type": "string", "enum": ["A", "B", "C", "D"]}, "fit_reason": STR,
                  "use_for": {"type": "string", "enum": ["Screening", "Tracking change", "Both"]},
                  "thresholds": STR, "cadence": STR, "cautions": STR}, ["abbr", "fit_grade", "fit_reason"])
    g_item = obj({"gap": STR, "approach": STR, "grade": {"type": "string", "enum": ["A", "B", "C", "D"]}}, ["gap", "approach"])
    return call_json(SYSTEM, prompt, obj({"summary_md": STR, "measures": {"type": "array", "items": m_item},
                                          "gaps": {"type": "array", "items": g_item}}, ["summary_md", "measures"]), 4500, model=FAST)


# ======================= Discover: the connection map =======================
DISCOVER_RULES = """You map how a health goal connects to the research, so a researcher can spot unexplored links.
Stay at the level of research hypotheses about EXISTING things: approved or widely used medicines, behavioural and
psychological therapies, devices and wearables, diagnostics, AI/digital tools, lifestyle factors, care models and policies.
Never propose new chemical compounds, structures, doses, synthesis or lab protocols.
Every PubMed query must be simple and broad: 1-4 quoted synonyms joined with OR, each tagged [tiab], optionally one [mh]
MeSH term. Example: ("sleep deprivation"[tiab] OR "insufficient sleep"[tiab] OR "Sleep Deprivation"[mh]). No AND, no NOT,
no years. Names must be short (1-4 words) and plain."""

QUERY_ITEM = obj({"name": STR, "plain": STR, "query": STR})


def discover_map(goal, n_mech=8, n_appr=10):
    """The goal (A), the mechanisms that drive it (B) and approaches that could act on it (C)."""
    prompt = f"""{DISCOVER_RULES}

GOAL: {goal}

1. target: the condition or outcome at the centre of this goal (name, plain = one sentence a non-expert understands,
   query).
2. mechanisms: {n_mech} distinct things that drive or worsen the target: biological processes, psychological or
   behavioural factors, social or environmental factors, biomarkers. Mix types. Each: name, plain (one sentence: what it
   is and how it links to the target), query.
3. approaches: {n_appr} existing approaches that could act on those mechanisms. Include about 3 that are already
   standard or common for the target, and about 7 that are established in OTHER conditions or fields but act on one or
   more of the mechanisms above (that is where unexplored links hide). Each: name, kind (Medicine | Therapy | Device |
   Digital / AI | Lifestyle | Diagnostic | Care model), plain (one sentence: what it is), query."""
    schema = obj({"target": QUERY_ITEM, "mechanisms": {"type": "array", "items": QUERY_ITEM},
                  "approaches": {"type": "array", "items": obj({"name": STR, "kind": STR, "plain": STR, "query": STR})}})
    out = call_json(SYSTEM, prompt, schema, 3500, model=FAST)
    ok = lambda x: isinstance(x, dict) and x.get("name") and x.get("query")
    return {"target": out.get("target") if ok(out.get("target")) else {"name": goal, "plain": "", "query": goal},
            "mechanisms": [m for m in out.get("mechanisms", []) if ok(m)][:n_mech],
            "approaches": [a for a in out.get("approaches", []) if ok(a)][:n_appr]}


def discover_explain(goal, target, leads, as_of=None):
    """Plain-first explanations for the most promising unexplored links, plus a short summary."""
    when = f"Counts only include papers published up to {as_of}. " if as_of else ""
    rows = "\n".join(
        f"- {l['name']} ({l['kind']}): {l['ac']} papers on it with {target}. Paths: "
        + "; ".join(f"{p['via']} ({p['ab']} papers link {p['via']} to {target}, {p['bc']} link it to {l['name']})" for p in l["paths"])
        for l in leads)
    prompt = f"""{DISCOVER_RULES}

GOAL: {goal}
TARGET: {target}
{when}These approaches are strongly connected to the target THROUGH a mechanism, but barely studied for the target
directly. They are publication patterns, not findings.
{rows}

For each lead (same order, same name): why = 2 sentences. First sentence in plain words a non-expert gets. Second sentence
names the mechanism path more technically. test = one sentence on the first study that could check it (e.g. a
retrospective analysis of existing records, a small pilot, a secondary analysis of trial data). caution = one short
sentence on the main reason it might not work.
Then summary: 2-3 sentences, plain words, on what the map shows overall. Do not overclaim."""
    schema = obj({"leads": {"type": "array", "items": obj({"name": STR, "why": STR, "test": STR, "caution": STR})},
                  "summary": STR})
    return call_json(SYSTEM, prompt, schema, 2000, model=FAST)
