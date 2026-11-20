"""Health / medicine / biomed news: pulls public RSS feeds, cleans and de-duplicates them."""
import re, html, time, hashlib
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import requests

UA = {"User-Agent": "Mozilla/5.0 (Medr research hub; personal use)"}

# (key, name, group, url)
FEEDS = [
    ("stat", "STAT News", "News", "https://www.statnews.com/feed/"),
    ("gnews", "Google News: Health", "News", "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-US&gl=US&ceid=US:en"),
    ("fda", "FDA", "Agencies", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
    ("nih", "NIH", "Agencies", "https://www.nih.gov/news-releases/feed.xml"),
    ("who", "WHO", "Agencies", "https://www.who.int/rss-feeds/news-english.xml"),
    ("nejm", "NEJM", "Journals", "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss"),
    ("lancet", "The Lancet", "Journals", "https://www.thelancet.com/rssfeed/lancet_current.xml"),
    ("natmed", "Nature Medicine", "Journals", "https://www.nature.com/nm.rss"),
    ("nature", "Nature", "Journals", "https://www.nature.com/nature.rss"),
    ("fierce", "Fierce Biotech", "Biotech", "https://www.fiercebiotech.com/rss/xml"),
    ("endpts", "Endpoints News", "Biotech", "https://endpts.com/feed/"),
    ("sd", "ScienceDaily: Health", "Science", "https://www.sciencedaily.com/rss/health_medicine.xml"),
    ("mx", "Medical Xpress", "Science", "https://medicalxpress.com/rss-feed/"),
]


def strip(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def when(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child(el, *names):
    for c in el:
        if local(c.tag) in names:
            return c
    return None


def text(el, *names):
    c = child(el, *names)
    return (c.text or "").strip() if c is not None and c.text else ""


def parse(xml_bytes, feed):
    key, name, group, _ = feed
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = strip(text(el, "title"))
        link = text(el, "link")
        if not link:
            l = child(el, "link")
            link = l.get("href", "") if l is not None else ""
        summary = strip(text(el, "description", "summary", "content", "encoded"))
        date = when(text(el, "pubdate", "published", "updated", "date"))
        src = name
        if key == "gnews":                         # "Headline - Outlet"
            m = re.match(r"(.+) - ([^-]{2,60})$", title)
            if m:
                title, src = m.group(1).strip(), f"{m.group(2).strip()} (via Google News)"
            summary = ""
        if not title or not link:
            continue
        out.append({"id": hashlib.md5((link or title).encode()).hexdigest()[:12], "title": title, "link": link,
                    "summary": summary[:600], "date": date, "source": src, "feed": key, "group": group})
    return out


def fetch_feed(feed, timeout=12):
    r = requests.get(feed[3], headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse(r.content, feed)


def norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:80]


def fetch_all(days=7):
    """Returns (items newest first, status per source)."""
    status, items = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {f[0]: pool.submit(fetch_feed, f) for f in FEEDS}
        for f in FEEDS:
            try:
                got = futs[f[0]].result()
                items += got
                status.append({"key": f[0], "name": f[1], "group": f[2], "ok": True, "count": len(got)})
            except Exception as e:
                status.append({"key": f[0], "name": f[1], "group": f[2], "ok": False, "error": str(e)[:120]})
    cutoff = time.time() - days * 86400
    seen, keep = set(), []
    for it in sorted(items, key=lambda x: -(x["date"] or 0)):
        if it["date"] and it["date"] < cutoff:
            continue
        k = norm(it["title"])
        if k in seen:
            continue
        seen.add(k)
        keep.append(it)
    return keep, status


def topic_news(topic, n=12):
    """Recent news on one topic (Google News search)."""
    url = f"https://news.google.com/rss/search?q={quote_plus(topic + ' when:30d')}&hl=en-US&gl=US&ceid=US:en"
    feed = ("gnews", "Google News", "News", url)
    r = requests.get(url, headers=UA, timeout=12)
    r.raise_for_status()
    return parse(r.content, feed)[:n]
