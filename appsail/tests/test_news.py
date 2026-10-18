"""Live News: parsing, de-duplication, tagging, the IntelHub join, filters, the cache,
and what happens when the outside world is not there.

The network is never touched. `news._http_get` is the module's only network seam and
every test replaces it with a fake that serves fixture payloads shaped like the real
ones — a Google News RSS search feed (HTML descriptions, a "full coverage" cluster, the
" - Source" title suffix) and a GDELT DOC 2.0 artlist — or that fails the way upstream
fails. The clock is moved through `news._now`, so TTL and staleness are tested without
sleeping.
"""
import email.utils
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copilot_engine  # noqa: E402
import news  # noqa: E402

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
T0 = NOW.timestamp()


def rfc822(hours_ago):
    return email.utils.format_datetime(NOW - timedelta(hours=hours_ago), usegmt=True)


def gdelt_date(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%SZ")


def google_item(title, source, link, hours_ago, description=None, source_url="https://example.in"):
    desc = description if description is not None else (
        f'&lt;a href="{link}" target="_blank"&gt;{title}&lt;/a&gt;&amp;nbsp;&amp;nbsp;'
        f'&lt;font color="#6f6f6f"&gt;{source}&lt;/font&gt;')
    link_el = f"<link>{link}</link>" if link else ""
    return (f"<item><title>{title} - {source}</title>{link_el}<guid isPermaLink=\"false\">{link}</guid>"
            f"<pubDate>{rfc822(hours_ago)}</pubDate><description>{desc}</description>"
            f'<source url="{source_url}">{source}</source></item>')


def rss(*items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>'
            '<generator>NFE/5.0</generator><title>"Karnataka police when:7d" - Google News</title>'
            '<link>https://news.google.com/search?q=Karnataka+police</link><language>en-IN</language>'
            '<description>Google News</description>' + "".join(items) + "</channel></rss>").encode("utf-8")


EN_RSS = rss(
    google_item("Mysuru police arrest two in Hunsur murder case", "Star of Mysore",
                "https://news.google.com/rss/articles/CBMiMYSURU?oc=5", 3, source_url="https://starofmysore.com"),
    google_item("Bengaluru techie loses Rs 1.2 crore to digital arrest scam, CEN police register FIR",
                "Deccan Herald", "https://news.google.com/rss/articles/CBMiDIGITAL?oc=5", 5,
                description=("&lt;p&gt;The victim, a software engineer from Whitefield, was kept on a video call "
                             "for three days by fraudsters posing as CBI officers.&lt;/p&gt;")),
    google_item("Mangaluru CCB seizes MDMA worth Rs 12 lakh; three held", "The Hindu",
                "https://news.google.com/rss/articles/CBMiMDMA?oc=5", 8,
                description=("&lt;ol&gt;&lt;li&gt;&lt;a href=\"x\"&gt;Mangaluru CCB seizes MDMA&lt;/a&gt;&amp;nbsp;"
                             "&lt;font&gt;The Hindu&lt;/font&gt;&lt;/li&gt;&lt;li&gt;&lt;a href=\"y\"&gt;Three held "
                             "with drugs in Ullal&lt;/a&gt;&lt;/li&gt;&lt;/ol&gt;")),
    # The same Mysuru story again, as a second query's feed would carry it.
    google_item("Mysuru police arrest two in Hunsur murder case", "Star of Mysore",
                "https://news.google.com/rss/articles/CBMiMYSURU?oc=5", 3),
    google_item("Bengaluru traffic police book 3,200 drunk driving cases in a week",
                "The New Indian Express", "https://news.google.com/rss/articles/CBMiTRAFFIC?oc=5", 20),
    google_item("Hyderabad police bust fake call centre targeting US citizens", "The Hindu",
                "https://news.google.com/rss/articles/CBMiHYD?oc=5", 1),
    google_item("Karnataka cybercrime cases rise as fraudsters target senior citizens", "The Hindu",
                "https://news.google.com/rss/articles/CBMiCYBER?oc=5", 30),
    google_item("Old Belagavi chain snatching case solved", "Udayavani",
                "https://news.google.com/rss/articles/CBMiOLD?oc=5", 24 * 20),         # too old
    google_item("A headline with no link at all", "Nowhere", "", 2),                      # dropped
)

KN_RSS = rss(
    google_item("ಮೈಸೂರಿನಲ್ಲಿ ಕೊಲೆ ಪ್ರಕರಣ: ಇಬ್ಬರು ಆರೋಪಿಗಳ ಬಂಧನ", "ಪ್ರಜಾವಾಣಿ",
                "https://news.google.com/rss/articles/CBMiKNMYS?oc=5", 2),
    google_item("ಬೆಂಗಳೂರು: ಸೈಬರ್ ವಂಚನೆ, ₹40 ಲಕ್ಷ ಕಳೆದುಕೊಂಡ ಟೆಕ್ಕಿ", "ವಿಜಯ ಕರ್ನಾಟಕ",
                "https://news.google.com/rss/articles/CBMiKNBLR?oc=5", 4),
    google_item("Bengaluru police arrest two (English item in the Kannada edition)", "The Hindu",
                "https://news.google.com/rss/articles/CBMiKNEN?oc=5", 4),
)

GDELT_JSON = json.dumps({"articles": [
    {"url": "https://www.deccanherald.com/india/karnataka/bengaluru/techie-loses-rs-1-2-crore-digital-arrest-1.html",
     "url_mobile": "", "title": "Bengaluru techie loses Rs 1.2 crore to digital arrest scam, CEN police register FIR",
     "seendate": gdelt_date(4), "socialimage": "https://images.deccanherald.com/dh/digital-arrest.jpg",
     "domain": "deccanherald.com", "language": "English", "sourcecountry": "India"},
    {"url": "https://www.thehindu.com/news/national/karnataka/belagavi-prohibitory.ece?utm_source=rss&utm_medium=x",
     "url_mobile": "", "title": "Prohibitory orders in Belagavi after clash during procession | The Hindu",
     "seendate": gdelt_date(6), "socialimage": "", "domain": "thehindu.com", "language": "English",
     "sourcecountry": "India"},
    {"url": "https://www.thehindu.com/news/national/karnataka/hc-bail-kalaburagi.ece",
     "url_mobile": "", "title": "Karnataka High Court grants bail to accused in Kalaburagi murder case",
     "seendate": gdelt_date(10), "socialimage": "https://th-i.thgim.com/bail.jpg", "domain": "thehindu.com",
     "language": "English", "sourcecountry": "India"},
    {"url": "https://www.thehindu.com/news/national/karnataka/belagavi-prohibitory.ece",
     "url_mobile": "", "title": "Same Belagavi story, URL without tracking parameters",
     "seendate": gdelt_date(6), "socialimage": "", "domain": "thehindu.com", "language": "English",
     "sourcecountry": "India"},
]}).encode("utf-8")


def make_firs():
    """A small corpus whose counts are easy to check by hand. Latest record 2026-10-15."""
    end = pd.Timestamp("2026-10-15 12:00:00")
    rows = []

    def add(district, crime, days_before_end, n):
        for i in range(n):
            rows.append({"case_id": f"10000000000000{len(rows):04d}", "district": district, "crime_type": crime,
                         "timestamp": str(end - pd.Timedelta(days=days_before_end, hours=i)),
                         "police_station": f"{district} Town PS", "status": "Under Investigation",
                         "severity_score": 50})
    add("Mysuru", "Murder", 2, 3)          # 3 in the last 30 days
    add("Mysuru", "Murder", 200, 2)        # 2 older -> 5 total
    add("Mysuru", "Assault", 5, 1)
    add("Bengaluru Urban", "Cyber Fraud (Jamtara style)", 1, 4)
    add("Bengaluru Urban", "Cyber Fraud (Jamtara style)", 90, 6)
    add("Kalaburagi", "Murder", 3, 1)
    add("Belagavi", "Assault", 10, 2)
    add("Dakshina Kannada", "Narcotics Distribution", 4, 2)
    rows.append({"case_id": "100000000000009999", "district": "Mysuru", "crime_type": "Murder",
                 "timestamp": str(end), "police_station": "Hunsur PS", "status": "Closed", "severity_score": 70})
    return pd.DataFrame(rows)


@pytest.fixture
def cache():
    return {"firs": make_firs()}


class FakeUpstream:
    """Serves fixture payloads by URL, records every call, and can be told to fail."""

    def __init__(self):
        self.calls = []
        self.fail = set()          # source urls that raise
        self.payloads = {news.GOOGLE_RSS_URL: EN_RSS, news.GDELT_URL: GDELT_JSON}

    def __call__(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if url in self.fail:
            raise news.FetchError("unreachable (connection failed)")
        if url == news.GOOGLE_RSS_URL and (params or {}).get("hl") == "kn":
            return KN_RSS
        if url == news.GDELT_URL and "sourcelang:kannada" in (params or {}).get("query", ""):
            return b'{"articles": []}'
        return self.payloads[url]

    def count(self, url):
        return sum(1 for u, _p, _h in self.calls if u == url)


@pytest.fixture
def upstream(monkeypatch):
    news.reset()
    news._COUNTS.update(key=None, val=None)
    fake = FakeUpstream()
    clock = {"t": T0}
    monkeypatch.setattr(news, "_http_get", fake)
    monkeypatch.setattr(news, "_now", lambda: clock["t"])
    monkeypatch.setattr(news, "BACKGROUND_REVALIDATE", False)
    for env in ("NEWSAPI_KEY", "GNEWS_API_KEY", "NEWS_FETCH"):
        monkeypatch.delenv(env, raising=False)
    fake.clock = clock
    yield fake
    news.reset()


@pytest.fixture
def client(cache, upstream):
    app = Flask(__name__)
    app.register_blueprint(news.bp)
    news.configure(lambda: cache)
    with app.test_client() as c:
        yield c
    news.configure(lambda: {})


def by_title(payload, fragment):
    hits = [it for it in payload["items"] if fragment.lower() in it["title"].lower()]
    assert hits, f"no item containing {fragment!r}"
    return hits[0]


# ---------------------------------------------------------------------------- parsing
def test_google_rss_parsing_strips_suffix_and_reads_html():
    items = news.parse_google_rss(EN_RSS)
    titles = [i["title"] for i in items]
    assert "Mysuru police arrest two in Hunsur murder case" in titles          # " - Star of Mysore" gone
    assert not any(t.endswith(" - The Hindu") for t in titles)
    assert "A headline with no link at all" not in titles                    # no link -> dropped
    mysuru = next(i for i in items if i["title"].startswith("Mysuru police"))
    assert mysuru["source"] == "Star of Mysore"
    assert mysuru["summary"] is None              # description was only the headline and the outlet again
    assert mysuru["published"] == (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert mysuru["image"] is None                # Google RSS carries no images
    digital = next(i for i in items if "digital arrest" in i["title"])
    assert digital["summary"].startswith("The victim, a software engineer from Whitefield")
    assert "<" not in digital["summary"]
    mdma = next(i for i in items if "MDMA" in i["title"])
    assert mdma["summary"] is None                # a coverage cluster is other headlines, not a summary


def test_gdelt_parsing_maps_publisher_date_and_image():
    items = news.parse_gdelt(GDELT_JSON)
    bail = next(i for i in items if "bail" in i["title"])
    assert bail["source"] == "The Hindu"
    assert bail["image"] == "https://th-i.thgim.com/bail.jpg"
    assert bail["published"] == (NOW - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    belagavi = next(i for i in items if i["title"].startswith("Prohibitory"))
    assert belagavi["title"] == "Prohibitory orders in Belagavi after clash during procession"   # "| The Hindu" gone
    assert belagavi["image"] is None              # empty socialimage is not an image
    assert "utm_" not in belagavi["_canon"]


def test_gdelt_text_error_and_hostile_xml_are_refused():
    with pytest.raises(news.FetchError, match="instead of JSON"):
        news.parse_gdelt(b"Your query was too short or too long.")
    assert news.parse_gdelt(b"{}") == []          # GDELT's "nothing matched"
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
    with pytest.raises(news.FetchError, match="entities"):
        news.parse_google_rss(bomb)
    with pytest.raises(news.FetchError, match="not an RSS feed"):
        news.parse_google_rss(b"<!doctype html><html><body>consent</body></html>")


def test_canonical_url_ignores_tracking_and_www():
    a = news.canonical_url("http://www.thehindu.com/a/b/?utm_source=x&id=7#top")
    b = news.canonical_url("https://thehindu.com/a/b?id=7")
    assert a == b
    assert news.canonical_url("javascript:alert(1)") is None


# ---------------------------------------------------------------------------- tagging
@pytest.mark.parametrize("text,primary,also", [
    ("Bengaluru techie loses Rs 1.2 crore to digital arrest scam", "cybercrime", "crime"),
    ("Mangaluru CCB seizes MDMA worth Rs 12 lakh; three held", "narcotics", "crime"),
    ("Bengaluru traffic police book 3,200 drunk driving cases", "traffic", "policing"),
    ("Prohibitory orders in Belagavi after clash during procession", "law_order", None),
    ("Karnataka High Court grants bail to accused in murder case", "courts", "crime"),
    ("POCSO case: man held for sexual assault on minor girl", "women_child", None),
    ("Mysuru police arrest two in Hunsur murder case", "crime", None),
    ("New DGP takes charge; transfers of IPS officers likely", "policing", None),
])
def test_categories(text, primary, also):
    cats = news.classify(text)
    assert cats[0] == primary
    if also:
        assert also in cats


def test_unmatched_text_is_general():
    item = news.make_item("Monsoon session to begin next week", "https://x.in/a", "X", None)
    assert item["category"] == "general" and item["categories"] == ["general"]


@pytest.mark.parametrize("text,expected", [
    ("Mysuru: man murdered in Hunsur", ["Mysuru"]),
    ("Techie from Whitefield duped", ["Bengaluru Urban"]),
    ("Mangalore police and Bangalore Rural police", ["Dakshina Kannada", "Bengaluru Rural"]),
    ("Hubli-Dharwad commissionerate", ["Dharwad"]),
    ("ಮೈಸೂರಿನಲ್ಲಿ ಕೊಲೆ; ಬೆಂಗಳೂರು ಗ್ರಾಮಾಂತರ ಪೊಲೀಸರು", ["Mysuru", "Bengaluru Rural"]),
    ("ಸಿಂಹಾಸನ ಏರಿದ ರಾಜ", []),                         # ಹಾಸನ inside "throne" is not Hassan
    ("Hyderabad police bust call centre", []),
    ("Vijayanagar police station, Bengaluru", ["Bengaluru Urban"]),   # the station is not the district
])
def test_district_detection(text, expected):
    assert news.detect_districts(text) == expected


def test_crime_types_reuse_the_copilot_vocabulary_without_its_news_traps():
    assert news.detect_crime_types("Bengaluru techie loses money in online fraud")[0] == "Cyber Fraud (Jamtara style)"
    assert news.detect_crime_types("Two stabbed to death in Mysuru")[0] == "Murder"
    assert news.detect_crime_types("Chain snatching gang busted") == ["Chain Snatching"]
    assert news.detect_crime_types("Two killed in road accident") == []        # not a murder
    assert news.detect_crime_types("Gold smuggling at airport") == []          # not sandalwood
    assert news.detect_crime_types("Police get a break in the case") == []     # not a burglary
    assert news.detect_crime_types("Actress, heroine of the film, at a launch") == []
    assert "Assault" not in news.detect_crime_types("sexual assault on a minor")


# ---------------------------------------------------------------------------- feed
def test_feed_dedupes_merges_and_ranks(cache, upstream):
    r = news.feed(cache)
    titles = [it["title"] for it in r["items"]]
    assert titles.count("Mysuru police arrest two in Hunsur murder case") == 1
    digital = [it for it in r["items"] if "digital arrest" in it["title"]]
    assert len(digital) == 1                       # Google and GDELT carried it; one card
    d = digital[0]
    assert d["image"] == "https://images.deccanherald.com/dh/digital-arrest.jpg"   # from GDELT
    assert d["summary"].startswith("The victim")                                    # from Google
    assert set(d["via"]) == {"GDELT", "Google News"}
    belagavi = [it for it in r["items"] if "Belagavi" in it["title"] and "Prohibitory" in it["title"]]
    assert len(belagavi) == 1                      # same URL once tracking is stripped
    assert not any("Old Belagavi" in t for t in titles)      # older than seven days
    # Karnataka first: the Hyderabad story is the newest item and still ranks last.
    assert titles[-1].startswith("Hyderabad police")
    hyd = by_title(r, "Hyderabad")
    assert hyd["karnataka"] is False and hyd["intelhub"] is None
    # Twelve Google feeds and one GDELT call, concurrently, once.
    assert upstream.count(news.GOOGLE_RSS_URL) == len(news.GOOGLE_QUERIES["en"])
    assert upstream.count(news.GDELT_URL) == 1
    q = [p for u, p, _h in upstream.calls if u == news.GOOGLE_RSS_URL][0]
    assert q["hl"] == "en-IN" and q["ceid"] == "IN:en" and q["q"].endswith("when:7d")
    g = [p for u, p, _h in upstream.calls if u == news.GDELT_URL][0]
    assert "sourcecountry:IN" in g["query"] and g["mode"] == "artlist" and g["format"] == "json"


def test_items_link_to_the_corpus(cache, upstream):
    r = news.feed(cache)
    mysuru = by_title(r, "Hunsur murder")
    assert mysuru["districts"] == ["Mysuru"] and mysuru["crime_types"][0] == "Murder"
    ih = mysuru["intelhub"]
    # 3 recent + the one on the last day; 2 more older than 30 days.
    assert ih["district"] == "Mysuru" and ih["crime_type"] == "Murder"
    assert ih["cases_30d"] == 4 and ih["cases_total"] == 6
    assert ih["link"] == "/cases?district=Mysuru&crime_type=Murder"
    assert ih["as_of"] == "2026-10-15" and ih["window_days"] == 30
    cyber = by_title(r, "digital arrest")["intelhub"]
    assert cyber["district"] == "Bengaluru Urban" and cyber["crime_type"] == "Cyber Fraud (Jamtara style)"
    assert (cyber["cases_30d"], cyber["cases_total"]) == (4, 10)
    # Karnataka-wide story with a crime but no district: statewide counts.
    state = by_title(r, "Karnataka cybercrime cases rise")["intelhub"]
    assert state["district"] is None and state["cases_30d"] == 4
    assert state["link"] == "/cases?crime_type=Cyber+Fraud+%28Jamtara+style%29"
    # A district with no crime keyword links to the district's records.
    traffic = by_title(r, "drunk driving")["intelhub"]
    assert (traffic["district"], traffic["crime_type"]) == ("Bengaluru Urban", None)
    assert traffic["link"] == "/cases?district=Bengaluru+Urban" and traffic["cases_30d"] == 4
    assert r["dataset"] == {"records_up_to": "2026-10-15", "window_days": 30}


def test_no_corpus_means_no_join_not_an_error(upstream):
    r = news.feed({})
    assert r["items"] and all(it["intelhub"] is None for it in r["items"])
    assert r["dataset"] is None


def test_filters_and_facets(cache, upstream):
    r = news.feed(cache, category="cybercrime")
    assert r["items"] and all("cybercrime" in it["categories"] for it in r["items"])
    # Facets: category counts ignore the category filter, so every chip keeps its number.
    assert r["categories"]["narcotics"] >= 1 and r["facet_total"] > r["total"]
    r = news.feed(cache, district="Mysuru")
    assert [it["title"] for it in r["items"]] == ["Mysuru police arrest two in Hunsur murder case"]
    assert r["districts"]["Bengaluru Urban"] >= 2          # district facet ignores the district filter
    r = news.feed(cache, q="mdma")
    assert len(r["items"]) == 1 and "MDMA" in r["items"][0]["title"]
    r = news.feed(cache, q="  CRORE   techie ")
    assert len(r["items"]) == 1
    r = news.feed(cache, limit=2)
    assert len(r["items"]) == 2 and r["total"] > 2
    r = news.feed(cache, crime_type="Murder")
    assert {it["title"] for it in r["items"]} >= {"Mysuru police arrest two in Hunsur murder case"}


def test_route_shape_and_validation(client):
    r = client.get("/serverless/api/news?district=Mysore&category=crime")
    assert r.status_code == 200
    body = r.get_json()
    for key in ("items", "fetched_at", "sources", "stale", "categories", "districts", "status", "note"):
        assert key in body
    assert body["filters"]["district"] == "Mysuru"          # alias resolved
    it = body["items"][0]
    for key in ("id", "title", "summary", "url", "source", "published", "image", "lang", "category",
                "categories", "districts", "crime_types", "intelhub"):
        assert key in it
    assert not any(k.startswith("_") for k in it)
    assert len(body["district_options"]) == 31
    assert r.headers["Cache-Control"] == "no-store"
    assert client.get("/serverless/api/news?district=Atlantis").status_code == 400
    assert client.get("/serverless/api/news?category=sports").status_code == 400
    assert client.get("/serverless/api/news?lang=fr").status_code == 400
    assert client.get("/serverless/api/news?district=All%20Karnataka&category=all").status_code == 200
    assert client.get("/serverless/api/news?limit=abc").status_code == 200


def test_kannada_feed(cache, upstream):
    r = news.feed(cache, lang="kn")
    assert r["lang"] == "kn"
    assert r["items"] and all(it["lang"] == "kn" for it in r["items"])       # English items filtered out
    mys = by_title(r, "ಮೈಸೂರಿನಲ್ಲಿ")
    assert mys["districts"] == ["Mysuru"] and mys["crime_types"] == ["Murder"]
    assert mys["intelhub"]["link"] == "/cases?district=Mysuru&crime_type=Murder"
    blr = by_title(r, "ಸೈಬರ್")
    assert blr["category"] == "cybercrime" and blr["districts"] == ["Bengaluru Urban"]
    kn_calls = [p for u, p, _h in upstream.calls if u == news.GOOGLE_RSS_URL]
    assert kn_calls and all(p["hl"] == "kn" and p["ceid"] == "IN:kn" for p in kn_calls)
    src = {s["name"]: s for s in r["sources"]}
    assert src["NewsAPI"]["configured"] is False and "Kannada" in src["NewsAPI"]["error"]


# ---------------------------------------------------------------------------- cache & failure
def test_cache_is_shared_within_the_ttl(cache, upstream):
    news.feed(cache)
    first = len(upstream.calls)
    upstream.clock["t"] += news.CACHE_TTL_S - 5
    news.feed(cache, category="crime")
    news.feed(cache, district="Mysuru")
    assert len(upstream.calls) == first             # no upstream traffic inside the TTL
    upstream.clock["t"] += 10
    news.feed(cache)
    assert len(upstream.calls) == 2 * first         # every source re-read once it expires


def test_forced_refresh_is_rate_limited(cache, upstream):
    news.feed(cache)
    first = len(upstream.calls)
    upstream.clock["t"] += 20
    news.feed(cache, force=True)
    assert len(upstream.calls) == first             # the refresh button cannot hammer upstream
    upstream.clock["t"] += news.MIN_FORCE_INTERVAL_S
    news.feed(cache, force=True)
    assert len(upstream.calls) == 2 * first


def test_stale_while_error(cache, upstream):
    good = news.feed(cache)
    upstream.fail = {news.GOOGLE_RSS_URL, news.GDELT_URL}
    upstream.clock["t"] += news.CACHE_TTL_S + 1
    r = news.feed(cache)
    assert r["stale"] is True and r["status"] == "stale"
    assert [it["id"] for it in r["items"]] == [it["id"] for it in good["items"]]   # last good items, unchanged
    for s in r["sources"]:
        if s["configured"]:
            assert s["ok"] is False and s["stale"] is True and "connection failed" in s["error"]
            assert s["last_ok"] == good["fetched_at"]
    assert r["fetched_at"] == good["fetched_at"]   # the time of the data, not of the failed attempt
    assert r["checked_at"] != good["checked_at"]


def test_failed_source_is_retried_sooner_than_a_healthy_one(cache, upstream):
    upstream.fail = {news.GDELT_URL}
    r = news.feed(cache)
    assert r["status"] == "ok"                      # Google answered: a partial outage, not a stale feed
    gd = next(s for s in r["sources"] if s["name"] == "GDELT")
    assert gd["ok"] is False and gd["count"] == 0
    assert not any("Kalaburagi" in it["title"] for it in r["items"])   # GDELT-only stories absent
    upstream.fail = set()
    g0, d0 = upstream.count(news.GOOGLE_RSS_URL), upstream.count(news.GDELT_URL)
    upstream.clock["t"] += news.RETRY_AFTER_ERROR_S + 1
    r = news.feed(cache)
    assert upstream.count(news.GDELT_URL) == d0 + 1                 # the failed source, again
    assert upstream.count(news.GOOGLE_RSS_URL) == g0                # the healthy one, left alone
    assert any("Kalaburagi" in it["title"] for it in r["items"])


def test_partial_feed_failure_inside_one_source(cache, upstream):
    real = upstream.__call__
    broken = {"Mysuru police", "Belagavi police"}       # by query, so the outcome is not order-dependent

    def flaky(url, params=None, headers=None):
        if url == news.GOOGLE_RSS_URL and params["q"].replace(" when:7d", "") in broken:
            raise news.FetchError("HTTP 503")
        return real(url, params, headers)

    news._http_get = flaky       # restored by monkeypatch at teardown
    r = news.feed(cache)
    g = next(s for s in r["sources"] if s["name"] == "Google News")
    assert g["ok"] is True and g["error"].startswith("2 of 12 feeds failed") and "HTTP 503" in g["error"]
    assert r["status"] == "ok" and r["items"]


def test_all_sources_down_from_the_start(client, upstream):
    upstream.fail = {news.GOOGLE_RSS_URL, news.GDELT_URL}
    r = client.get("/serverless/api/news")
    assert r.status_code == 200
    body = r.get_json()
    assert body["items"] == [] and body["status"] == "unreachable" and body["stale"] is False
    assert body["fetched_at"] is None and body["checked_at"]
    errs = {s["name"]: s.get("error") for s in body["sources"] if s["configured"]}
    assert set(errs) == {"Google News", "GDELT"} and all(errs.values())
    assert body["categories"] == {} and body["districts"] == {}


def test_fetching_can_be_switched_off(cache, upstream, monkeypatch):
    monkeypatch.setenv("NEWS_FETCH", "off")
    r = news.feed(cache)
    assert upstream.calls == []
    assert r["status"] == "disabled" and r["items"] == []
    assert all("NEWS_FETCH=off" in s["error"] for s in r["sources"])


def test_optional_keyed_sources_and_key_hygiene(cache, upstream, monkeypatch):
    monkeypatch.setenv("NEWSAPI_KEY", "nk-secret-123456")
    monkeypatch.setenv("GNEWS_API_KEY", "gk-secret-654321")
    upstream.payloads[news.NEWSAPI_URL] = json.dumps({"status": "ok", "articles": [
        {"source": {"id": None, "name": "The Hindu"}, "title": "Udupi police seize ganja, two held - The Hindu",
         "description": "Acting on a tip-off, the Udupi CEN police seized 2 kg of ganja near Manipal.",
         "url": "https://www.thehindu.com/udupi-ganja.ece", "urlToImage": "https://th-i.thgim.com/u.jpg",
         "publishedAt": (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"source": {"name": "x"}, "title": "[Removed]", "url": "https://removed.com", "publishedAt": None},
    ]}).encode()

    def gnews_fail(url, params=None, headers=None):
        raise news.FetchError(f"HTTP 401 for https://gnews.io/api/v4/search?apikey={params['apikey']}")

    real = upstream.__call__

    def route(url, params=None, headers=None):
        if url == news.GNEWS_URL:
            upstream.calls.append((url, dict(params), dict(headers or {})))
            return gnews_fail(url, params, headers)
        return real(url, params, headers)

    news._http_get = route
    r = news.feed(cache)
    udupi = by_title(r, "Udupi police seize ganja")
    assert udupi["summary"].startswith("Acting on a tip-off") and udupi["image"] == "https://th-i.thgim.com/u.jpg"
    assert udupi["category"] == "narcotics" and udupi["districts"] == ["Udupi"]
    assert not any(it["title"] == "[Removed]" for it in r["items"])
    na = [h for u, _p, h in upstream.calls if u == news.NEWSAPI_URL][0]
    assert na["X-Api-Key"] == "nk-secret-123456"          # key in a header, not the URL
    src = {s["name"]: s for s in r["sources"]}
    assert src["NewsAPI"]["ok"] and src["GNews"]["ok"] is False
    body = json.dumps(r)
    assert "nk-secret-123456" not in body and "gk-secret-654321" not in body


def test_unconfigured_optional_sources_are_reported_not_hidden(cache, upstream):
    r = news.feed(cache)
    src = {s["name"]: s for s in r["sources"]}
    assert src["NewsAPI"] == {**src["NewsAPI"], "configured": False, "ok": False}
    assert "NEWSAPI_KEY" in src["NewsAPI"]["error"] and "GNEWS_API_KEY" in src["GNews"]["error"]
    assert r["status"] == "ok"                     # an unconfigured source is not an outage


# ---------------------------------------------------------------------------- copilot
def test_copilot_news_tool_and_composer(cache, upstream):
    res = copilot_engine.run_tool("news", {"district": "Mysore"}, cache)
    assert res["scope"] == "Mysuru" and res["headlines"]
    assert res["records"]["cases_30d"] == 5        # all Mysuru FIRs in the window (murders + assault)
    assert "name" not in json.dumps(res["sources"])  # the egress mask would alias a `name` as a person
    text = copilot_engine.compose([(("news", {"district": "Mysuru"}), res)])
    assert "Hunsur murder" in text and "not verified by KSP" in text
    cyber = copilot_engine.run_tool("news", {"crime_type": "cyber fraud"}, cache)
    assert cyber["topic"] == "Cybercrime" and cyber["headlines"]
    assert cyber["records"]["crime_type"] == "Cyber Fraud (Jamtara style)"


def test_copilot_reports_unreachable_feeds_honestly(cache, upstream):
    upstream.fail = {news.GOOGLE_RSS_URL, news.GDELT_URL}
    res = copilot_engine.run_tool("news", {}, cache)
    assert res["headlines"] == [] and res["status"] == "unreachable"
    text = copilot_engine.compose([(("news", {}), res)])
    assert "couldn't reach" in text.lower()


@pytest.mark.parametrize("question,args", [
    ("latest news in Bengaluru", {"district": "Bengaluru Urban"}),
    ("what's in the news about cyber fraud", {"crime_type": "Cyber Fraud (Jamtara style)"}),
    ("any headlines on traffic in Mysuru?", {"district": "Mysuru", "topic": "traffic"}),
    ("ಬೆಂಗಳೂರು ಸುದ್ದಿ", {"district": "Bengaluru Urban", "lang": "kn"}),
])
def test_planner_routes_news_questions(cache, question, args):
    idx = copilot_engine._index(cache)
    ents = copilot_engine.extract_entities(question, idx)
    calls = copilot_engine.plan(question, idx, ents)
    assert calls and calls[0][0] == "news"
    for k, v in args.items():
        assert calls[0][1].get(k) == v


def test_planner_leaves_record_questions_alone(cache):
    idx = copilot_engine._index(cache)
    for q in ("what's happening in Bengaluru", "how many murders in Mysuru"):
        calls = copilot_engine.plan(q, idx, copilot_engine.extract_entities(q, idx))
        assert calls and calls[0][0] != "news"
