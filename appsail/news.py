"""Live News — public Karnataka police and public-safety headlines, joined to the corpus.

Why this exists
---------------
An SCRB analyst starts the day with the papers. A cyber-fraud wave in Bengaluru, a
ganja haul in Mangaluru, a protest in Belagavi that will need bandobast — the media
often carries it before a district report reaches headquarters. Reading it in another
browser tab means reading it without the one thing this platform can add: what our own
records say about the same place and the same crime. This module puts the headline and
the record side by side.

What it does
------------
* Pulls from public aggregators concurrently, each call with a short timeout:
  GDELT DOC 2.0 (an open index of world news), Google News RSS search feeds in English
  and Kannada, and — only when an operator has set a key — NewsAPI and GNews.
* Normalises every item to one shape, de-duplicates by canonical URL and by normalised
  headline, and tags it with categories, Karnataka districts and the corpus's crime
  types using the keyword rules below.
* Joins each item that names a district (and optionally a crime) to the FIR counts for
  it — the last 30 days of the corpus and all time — with a link into /cases.

What it does not do, stated plainly
-----------------------------------
* It never invents an article. When no source has ever answered, the response is an
  empty list with the per-source errors, so the page says "unreachable" instead of
  looking like a quiet news day. There is no sample feed, by design: a fabricated
  headline on a police screen is worse than a blank one.
* Nothing here is verified by KSP. Headlines are what outlets published. Categories,
  districts and crime types are keyword matches on the headline and summary, not
  reading comprehension: "Hassan" the district and Hassan the surname look the same to
  a keyword rule. Tags order attention; they establish nothing.
* The IntelHub counts describe our records, not the incident in the article. A Mysuru
  murder headline linked to "N murder FIRs in Mysuru" does not mean the article's case
  is among them — and the corpus is simulated, CCTNS-shaped data.
* Upstream limits apply. GDELT asks for at most one request every five seconds, Google
  News RSS carries no images and only a headline-sized description, NewsAPI and GNews
  free tiers are rate-limited and have no Kannada edition. A shared cache keeps a busy
  control room to one upstream round per source every ten minutes; a source that fails
  is retried after 90 seconds while its last good items are served, flagged stale.
* NEWS_FETCH=off disables every outbound call, for deployments on a closed network.
  The page then says the feed is disabled rather than empty.
"""
import concurrent.futures
import email.utils
import hashlib
import html
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from flask import Blueprint, Response, request

import karnataka
from copilot_engine import CRIME_SYNONYMS, DISTRICT_ALIASES

LOG = logging.getLogger("ksp.news")

bp = Blueprint("live_news", __name__)

_cache_getter = lambda: {}     # noqa: E731 — replaced by configure()


def configure(cache_getter):
    """Bind the data cache. main.py rebinds the corpus on reload, so it is read through
    a getter on every request rather than captured once at import."""
    global _cache_getter
    _cache_getter = cache_getter


# =============================================================================
# Tunables — declared, not buried
# =============================================================================
CACHE_TTL_S = int(os.environ.get("NEWS_CACHE_TTL", "600"))   # a healthy source is re-read every 10 min
RETRY_AFTER_ERROR_S = 90        # a failed source is retried sooner, its last good items served meanwhile
MIN_FORCE_INTERVAL_S = 60       # the refresh button cannot hammer upstream through an open endpoint
STALE_KEEP_S = 24 * 3600        # past this, "last good" items are too old to call live
FETCH_TIMEOUT = (4.0, 6.0)      # (connect, read) seconds per call
ROUND_DEADLINE_S = 9.0          # a whole round of concurrent calls never takes longer than this
MAX_BYTES = 3_000_000
MAX_AGE_DAYS = 7                # matches GDELT timespan=7d and Google's when:7d
WINDOW_DAYS = 30                # the IntelHub join: FIRs in the last 30 days of the corpus
DEFAULT_LIMIT = 60
MAX_LIMIT = 200
USER_AGENT = "KSP-IntelHub/1.0 (public-safety news aggregator)"

# The caller waits for the very first round only; after that a due source is refreshed
# in the background and the current snapshot is served at once. Tests turn it off so
# cache behaviour is deterministic.
BACKGROUND_REVALIDATE = True

NOTE = ("Headlines come from public news sources and aggregators. They are not verified by KSP. "
        "Categories, districts and crime types are keyword matches; IntelHub counts describe our "
        "own (simulated, CCTNS-shaped) records for that district, not the incident in the article.")

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_RSS_URL = "https://news.google.com/rss/search"
NEWSAPI_URL = "https://newsapi.org/v2/everything"
GNEWS_URL = "https://gnews.io/api/v4/search"

SOURCE_ORDER = ("Google News", "GDELT", "NewsAPI", "GNews")
# Merge precedence when two sources carry the same story: the one with a description
# and an image first, Google's headline-only RSS last.
MERGE_ORDER = ("NewsAPI", "GNews", "GDELT", "Google News")

GDELT_QUERY = {
    # GDELT requires OR-lists in parentheses; two lists are ANDed.
    "en": ("(police OR crime OR arrested OR cybercrime OR murder OR fraud OR drugs OR accident) "
           "(Karnataka OR Bengaluru OR Bangalore OR Mysuru OR Mangaluru OR Hubballi OR Belagavi OR Kalaburagi) "
           "sourcecountry:IN sourcelang:english"),
    # GDELT searches its machine translation of non-English articles, so the keywords stay English.
    "kn": ("(police OR crime OR arrested OR fraud OR murder) "
           "(Karnataka OR Bengaluru OR Mysuru OR Mangaluru OR Hubballi OR Belagavi) "
           "sourcecountry:IN sourcelang:kannada"),
}

GOOGLE_QUERIES = {
    "en": ["Karnataka police", "Bengaluru police", "Bengaluru crime", "Karnataka cyber fraud",
           "Karnataka drugs seized", "Mysuru police", "Mangaluru police", "Hubballi Dharwad police",
           "Belagavi police", "Kalaburagi police", "Bengaluru traffic police", "Karnataka High Court police"],
    "kn": ["ಕರ್ನಾಟಕ ಪೊಲೀಸ್", "ಬೆಂಗಳೂರು ಪೊಲೀಸ್", "ಬೆಂಗಳೂರು ಅಪರಾಧ", "ಸೈಬರ್ ವಂಚನೆ", "ಮೈಸೂರು ಪೊಲೀಸ್",
           "ಮಂಗಳೂರು ಪೊಲೀಸ್", "ಹುಬ್ಬಳ್ಳಿ ಪೊಲೀಸ್", "ಬೆಳಗಾವಿ ಪೊಲೀಸ್", "ಕಲಬುರಗಿ ಪೊಲೀಸ್", "ಗಾಂಜಾ ಬಂಧನ"],
}
GOOGLE_EDITION = {"en": {"hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                  "kn": {"hl": "kn", "gl": "IN", "ceid": "IN:kn"}}

NEWSAPI_QUERY = ("(police OR crime OR arrested OR fraud OR murder OR drugs) AND "
                 "(Karnataka OR Bengaluru OR Bangalore OR Mysuru OR Mangaluru OR Hubballi)")
GNEWS_QUERY = '"Karnataka police" OR "Bengaluru police" OR "Bengaluru crime" OR "Mangaluru police" OR "Mysuru police"'

# Publisher names for the domains GDELT reports, so a card says "Deccan Herald" rather
# than "deccanherald.com". Unknown domains are shown as the domain itself.
PUBLISHERS = {
    "thehindu.com": "The Hindu", "deccanherald.com": "Deccan Herald",
    "timesofindia.indiatimes.com": "The Times of India", "indianexpress.com": "The Indian Express",
    "newindianexpress.com": "The New Indian Express", "hindustantimes.com": "Hindustan Times",
    "ndtv.com": "NDTV", "thenewsminute.com": "The News Minute", "news18.com": "News18",
    "indiatoday.in": "India Today", "prajavani.net": "Prajavani", "vijaykarnataka.com": "Vijay Karnataka",
    "udayavani.com": "Udayavani", "publictv.in": "Public TV", "tv9kannada.com": "TV9 Kannada",
    "kannadaprabha.com": "Kannada Prabha", "vijayavani.net": "Vijayavani", "starofmysore.com": "Star of Mysore",
    "daijiworld.com": "Daijiworld", "bangaloremirror.indiatimes.com": "Bangalore Mirror",
    "economictimes.indiatimes.com": "The Economic Times", "deccanchronicle.com": "Deccan Chronicle",
    "theprint.in": "ThePrint", "scroll.in": "Scroll", "livemint.com": "Mint",
    "business-standard.com": "Business Standard", "oneindia.com": "Oneindia",
    "asianetnews.com": "Asianet News", "zeenews.india.com": "Zee News", "thequint.com": "The Quint",
}

KANNADA = re.compile(r"[\u0C80-\u0CFF]")
_ZW = re.compile(r"[\u200b-\u200d\ufeff]")


# =============================================================================
# Keyword rules — categories, districts, crime types
# =============================================================================
def _en_rx(pattern):
    """An English alternation matched on word boundaries over lowercased text."""
    return re.compile(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])")


def _kn_rx(terms):
    """Kannada terms as substrings. Kannada inflects by suffix (ಬಂಧನ, ಬಂಧನಕ್ಕೆ), and \\b is
    useless on it — Python treats the vowel signs as non-word characters — so these are
    plain substring matches on text with zero-width joiners removed."""
    return re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))) if terms else None


# (slug, label, English pattern, Kannada terms), in priority order: an item's primary
# category is the first that matches, so a cybercrime arrest is "Cybercrime", not "Crime",
# and a bail order in a murder case is "Courts". `policing` is last because nearly every
# police story mentions a station or an officer.
CATEGORY_RULES = [
    ("cybercrime", "Cybercrime",
     r"cyber\w*|online (?:fraud|scam|cheat\w*)s?|digital arrests?|phishing|otp|upi (?:fraud|scam)s?|sextortion|"
     r"investment (?:fraud|scam)s?|crypto\w* (?:fraud|scam)s?|deep ?fakes?|hack(?:er|ers|ed|ing)|ransomware|"
     r"loan apps?|mule accounts?|fake (?:website|app)s?|sim swap\w*|(?:courier|job|task) scams?",
     ["ಸೈಬರ್", "ಆನ್ಲೈನ್ ವಂಚನೆ", "ಡಿಜಿಟಲ್ ಅರೆಸ್ಟ್", "ಒಟಿಪಿ"]),
    ("narcotics", "Narcotics",
     r"narcotic\w*|ndps|drugs|drug (?:peddl\w*|racket\w*|haul\w*|bust\w*|seiz\w*|traffick\w*|smuggl\w*|addict\w*|"
     r"abuse|menace|cartel\w*|syndicate\w*|mafia|part(?:y|ies))|peddl(?:er|ers|ing)|ganja|marijuana|cannabis|"
     r"hydro(?:ponic)? (?:ganja|weed)|mdma|cocaine|heroin|hashish|charas|opium|brown sugar|methamphetamine",
     ["ಗಾಂಜಾ", "ಮಾದಕ", "ಡ್ರಗ್ಸ್", "ಎಂಡಿಎಂಎ", "ಅಫೀಮು"]),
    ("women_child", "Women & child safety",
     r"pocso|rape[ds]?|raping|sexual(?:ly)? (?:assault\w*|harass\w*|abuse\w*)|molest\w*|dowry|"
     r"stalk(?:er|ers|ing|ed)|domestic violence|acid attacks?|eve[- ]teas\w*|minor (?:girl|boy)s?|"
     r"child (?:abuse|marriage|labour|trafficking|safety)|human trafficking|women'?s? safety|missing child\w*|"
     r"pink hoysala",
     ["ಪೋಕ್ಸೋ", "ಅತ್ಯಾಚಾರ", "ಲೈಂಗಿಕ ದೌರ್ಜನ್ಯ", "ಲೈಂಗಿಕ ಕಿರುಕುಳ", "ವರದಕ್ಷಿಣೆ", "ಬಾಲ್ಯ ವಿವಾಹ",
      "ಮಹಿಳಾ ಸುರಕ್ಷತೆ", "ಅಪ್ರಾಪ್ತ", "ಕಿರುಕುಳ"]),
    ("traffic", "Traffic & road safety",
     r"traffic|accident\w*|road safety|hit[- ]and[- ]run|drunk(?:en)? driv\w*|drink[- ]driv\w*|helmets?|"
     r"overspeed\w*|rash driv\w*|collision\w*|collid\w*|pile-?ups?|challans?|seat ?belts?|potholes?|"
     r"signal jump\w*|wrong[- ]side driv\w*|black spots?",
     ["ಅಪಘಾತ", "ಸಂಚಾರ", "ಟ್ರಾಫಿಕ್", "ಹೆಲ್ಮೆಟ್", "ರಸ್ತೆ ಸುರಕ್ಷತೆ", "ಡಿಕ್ಕಿ"]),
    ("courts", "Courts",
     r"courts?|high court|supreme court|hc|bail\w*|sentenc\w*|convict\w*|acquit\w*|verdicts?|"
     r"judge|judges|judgment\w*|judgement\w*|chargesheet\w*|charge[- ]sheet\w*|trial|hearings?|"
     r"life imprisonment|rigorous imprisonment|jailed|prosecut\w*|magistrates?",
     ["ನ್ಯಾಯಾಲಯ", "ಹೈಕೋರ್ಟ್", "ಕೋರ್ಟ್", "ಜಾಮೀನು", "ಶಿಕ್ಷೆ", "ತೀರ್ಪು", "ಆರೋಪಪಟ್ಟಿ", "ದೋಷಿ", "ಖುಲಾಸೆ"]),
    ("law_order", "Law & order / protests",
     r"protest\w*|agitat\w*|bandh|dharna|clash\w*|riot\w*|communal|lathi[- ]charg\w*|prohibitory orders?|"
     r"section 144|curfew|stone[- ]pelt\w*|tension|mobs?|lynch\w*|procession\w*|bandobast|law and order|"
     r"law & order|unrest|violence|stampede\w*",
     ["ಪ್ರತಿಭಟನೆ", "ಗಲಭೆ", "ಬಂದ್", "ಘರ್ಷಣೆ", "ನಿಷೇಧಾಜ್ಞೆ", "ಕೋಮು", "ಲಾಠಿ", "ಉದ್ವಿಗ್ನ", "ಕಾಲ್ತುಳಿತ",
      "ಬಂದೋಬಸ್ತ್"]),
    ("crime", "Crime",
     r"murder\w*|kill(?:ed|ing|er|ers)|homicide\w*|stabb\w*|robber\w*|robb(?:ed|ing)|theft\w*|thie(?:f|ves)|"
     r"stolen|steal\w*|burglar\w*|break-ins?|dacoit\w*|loot\w*|snatch\w*|extort\w*|ransom|kidnap\w*|abduct\w*|"
     r"assault\w*|arrest\w*|nabbed|booked|accused|abscond\w*|fir|firs|gangs?|gangsters?|rowd(?:y|ies)\w*|"
     r"smuggl\w*|sandalwood|red sanders|fraud\w*|cheat\w*|scam\w*|counterfeit|fake currency|"
     r"seiz(?:e|es|ed|ing|ure|ures)|"
     # "held" alone is a meeting; "three held" and "held for" are arrests.
     r"held (?:for|with|in connection)|(?:\d+|two|three|four|five|six|seven|eight|nine|ten|several) "
     r"(?:men |women |people |persons |youths |accused )?held|"
     r"crimes?|criminals?|suspects?|culprits?|custody|shot dead|found dead|body found",
     ["ಕೊಲೆ", "ಹತ್ಯೆ", "ಕಳ್ಳತನ", "ಕಳವು", "ದರೋಡೆ", "ಬಂಧನ", "ಬಂಧಿಸ", "ಆರೋಪಿ", "ಹಲ್ಲೆ", "ಅಪಹರಣ", "ಸುಲಿಗೆ",
      "ಅಪರಾಧ", "ವಂಚನೆ", "ಎಫ್ಐಆರ್", "ಪ್ರಕರಣ ದಾಖಲು", "ರೌಡಿ", "ಕಳ್ಳ", "ಇರಿತ"]),
    ("policing", "Policing & governance",
     r"police commissioner|commissioner of police|dgp|director general of police|igp|inspector general|"
     r"superintendent of police|sp|dcp|acp|ips officers?|transferr?\w*|home minister|home department|"
     r"police stations?|recruitment|constables?|sub-inspectors?|policing|patrol\w*|"
     r"emergency (?:response|helpline)|dial 112|112 helpline|erss|cctv|police reforms?|police department|"
     r"police force|karnataka state police|ksp|cops?|police personnel|police officers?|hoysala|traffic police",
     ["ಪೊಲೀಸ್ ಆಯುಕ್ತ", "ಡಿಜಿಪಿ", "ಐಜಿಪಿ", "ಎಸ್ಪಿ", "ವರ್ಗಾವಣೆ", "ಗೃಹ ಸಚಿವ", "ನೇಮಕಾತಿ", "ಠಾಣೆ",
      "ಕಾನ್ಸ್ಟೆಬಲ್", "ಪೊಲೀಸ್ ಇಲಾಖೆ"]),
]
CATEGORY_LABELS = {slug: label for slug, label, _en, _kn in CATEGORY_RULES}
CATEGORY_LABELS["general"] = "General"
_CATEGORY_RX = [(slug, _en_rx(en), _kn_rx(kn)) for slug, _label, en, kn in CATEGORY_RULES]

# The two dataset crime types that have a news category of their own.
CRIME_CATEGORY = {"Cyber Fraud (Jamtara style)": "cybercrime", "Narcotics Distribution": "narcotics"}

KARNATAKA_RX = _en_rx(r"karnataka|ksp|kannada|kannadigas?")
KARNATAKA_KN = _kn_rx(["ಕರ್ನಾಟಕ", "ಕನ್ನಡ"])

# Crime types: the copilot's vocabulary (so a headline and a question resolve to the same
# corpus crime type) minus the words that mislead on news copy — "killed" is usually a road
# accident, "attack" a political one, "smuggling" more often gold than sandalwood, "a break
# in the case" is not a burglary — plus newsroom phrasing and Kannada.
_CRIME_EXCLUDE = {"killed", "killing", "attack", "hurt", "smuggling", "cyber", "drug", "beating", "break in"}
_CRIME_EXTRA = {
    "Murder": ["murdered", "hacked to death", "stabbed to death", "bludgeoned to death", "strangled to death",
               "beaten to death", "honour killing", "shot dead", "ಕೊಲೆ", "ಹತ್ಯೆ"],
    "Assault": ["assaulted", "attacked with", "beaten up", "stabbed", "ಹಲ್ಲೆ"],
    "Cyber Fraud (Jamtara style)": ["cyber fraud", "investment scam", "investment fraud", "cyber criminals",
                                    "cyber fraudsters", "sextortion", "ಸೈಬರ್ ವಂಚನೆ", "ಆನ್ಲೈನ್ ವಂಚನೆ",
                                    "ಸೈಬರ್ ಅಪರಾಧ", "ಡಿಜಿಟಲ್ ಅರೆಸ್ಟ್"],
    "Narcotics Distribution": ["drugs", "drug peddler", "drug racket", "hydro ganja", "heroin", "hashish",
                               "ಗಾಂಜಾ", "ಡ್ರಗ್ಸ್", "ಮಾದಕ"],
    "Vehicle Theft": ["bike thie", "two-wheeler thie", "vehicle thie", "car thie", "stolen bike", "stolen vehicle",
                      "ವಾಹನ ಕಳ್ಳತನ", "ಬೈಕ್ ಕಳ್ಳತನ", "ವಾಹನ ಕಳವು", "ಬೈಕ್ ಕಳವು"],
    "House Break-in (Night)": ["house break-in", "housebreaking", "house burglary", "ಮನೆಗಳ್ಳತನ", "ಮನೆ ಕಳ್ಳತನ",
                               "ಮನೆ ಕಳವು"],
    "Chain Snatching": ["chain snatcher", "chain-snatch", "ಸರಗಳ್ಳತನ", "ಸರ ಕಳವು"],
    "Extortion": ["ಸುಲಿಗೆ"],
    "Sandalwood Smuggling": ["ಶ್ರೀಗಂಧ"],
}


def _crime_terms():
    out = []
    for ct, syns in CRIME_SYNONYMS.items():
        for s in syns:
            if s not in _CRIME_EXCLUDE:
                out.append((s, ct))
    for ct, syns in _CRIME_EXTRA.items():
        out += [(s, ct) for s in syns]
    # Longest first, so "stabbed to death" (Murder) is tried before "stabbed" (Assault).
    return sorted(set(out), key=lambda t: -len(t[0]))


# English terms allow the usual inflections and nothing more, so "heroin" does not match
# "heroine" (film news) while "chain snatch" still matches "chain snatching".
_SUFFIX = r"(?:s|es|ed|d|ing|ings|er|ers|ion|ions|y|ies|f|ves)?(?![a-z])"
_CRIME_TERMS = [(t, ct, re.compile(re.escape(t)) if KANNADA.search(t)
                 else re.compile(r"(?<![a-z])" + re.escape(t) + _SUFFIX))
                for t, ct in _crime_terms()]

# Kannada district names. Matched on the stem (the final vowel sign dropped) because the
# locative and genitive endings change it: ಮೈಸೂರು → ಮೈಸೂರಿನಲ್ಲಿ. A left boundary keeps
# ಹಾಸನ (Hassan) from matching inside ಸಿಂಹಾಸನ (throne). ವಿಜಯನಗರ alone is not listed: it is
# also a police station in both Bengaluru and Mysuru, so only "ವಿಜಯನಗರ ಜಿಲ್ಲೆ" counts.
KN_DISTRICTS = {
    "Bagalkote": ["ಬಾಗಲಕೋಟೆ"], "Ballari": ["ಬಳ್ಳಾರಿ"], "Belagavi": ["ಬೆಳಗಾವಿ"],
    "Bengaluru Rural": ["ಬೆಂಗಳೂರು ಗ್ರಾಮಾಂತರ"], "Bengaluru Urban": ["ಬೆಂಗಳೂರು"], "Bidar": ["ಬೀದರ್"],
    "Chamarajanagara": ["ಚಾಮರಾಜನಗರ"], "Chikkaballapura": ["ಚಿಕ್ಕಬಳ್ಳಾಪುರ"], "Chikkamagaluru": ["ಚಿಕ್ಕಮಗಳೂರು"],
    "Chitradurga": ["ಚಿತ್ರದುರ್ಗ"], "Dakshina Kannada": ["ದಕ್ಷಿಣ ಕನ್ನಡ", "ಮಂಗಳೂರು"], "Davanagere": ["ದಾವಣಗೆರೆ"],
    "Dharwad": ["ಧಾರವಾಡ", "ಹುಬ್ಬಳ್ಳಿ"], "Gadag": ["ಗದಗ"], "Hassan": ["ಹಾಸನ"], "Haveri": ["ಹಾವೇರಿ"],
    "Kalaburagi": ["ಕಲಬುರಗಿ"], "Kodagu": ["ಕೊಡಗು", "ಮಡಿಕೇರಿ"], "Kolar": ["ಕೋಲಾರ"], "Koppal": ["ಕೊಪ್ಪಳ"],
    "Mandya": ["ಮಂಡ್ಯ"], "Mysuru": ["ಮೈಸೂರು"], "Raichur": ["ರಾಯಚೂರು"], "Ramanagara": ["ರಾಮನಗರ"],
    "Shivamogga": ["ಶಿವಮೊಗ್ಗ"], "Tumakuru": ["ತುಮಕೂರು"], "Udupi": ["ಉಡುಪಿ"],
    "Uttara Kannada": ["ಉತ್ತರ ಕನ್ನಡ", "ಕಾರವಾರ"], "Vijayanagara": ["ವಿಜಯನಗರ ಜಿಲ್ಲೆ", "ಹೊಸಪೇಟೆ"],
    "Vijayapura": ["ವಿಜಯಪುರ"], "Yadgir": ["ಯಾದಗಿರಿ"],
}

# Station localities (karnataka.STATIONS uses real town names) that are too generic or
# shared with other states to place a headline: "Camp", "Vijayanagar" (a station in both
# Bengaluru and Mysuru), "Brahmapur" (also Odisha), "Shahapur" (also Maharashtra), "Ron",
# "Indi" and "Aland", which collide with ordinary words and names.
_GENERIC_LOCALITIES = {"camp", "devaraja", "lashkar", "vidyanagar", "navanagar", "vijayanagar", "krishnaraja",
                       "doddapet", "cowl bazaar", "brahmapur", "vinoba nagar", "ron", "indi", "sira", "aland",
                       "shahapur", "manvi"}
_EXTRA_ALIASES = {
    "hubli-dharwad": "Dharwad", "hubballi-dharwad": "Dharwad", "dharwar": "Dharwad",
    "kolar gold fields": "Kolar", "yadgiri": "Yadgir", "gadag-betageri": "Gadag", "mercara": "Kodagu",
    "chikkaballapur": "Chikkaballapura", "chikkamagalur": "Chikkamagaluru", "chamrajnagar": "Chamarajanagara",
    "kalaburgi": "Kalaburagi", "bagalkote": "Bagalkote", "belagavi city": "Belagavi",
}


def _stem_kn(name):
    """Drop a trailing vowel sign or virama from the last word so inflected forms match."""
    return re.sub(r"[\u0CBE-\u0CCD]$", "", name)


def _district_tables():
    """Two alternations — English names, aliases and station localities; Kannada stems —
    and the lookup from a matched key to its district. One regex per script rather than
    two hundred: a refresh tags well over a thousand items."""
    canon = set(karnataka.DISTRICTS)
    en = {d.lower(): d for d in canon}
    for k, d in list(DISTRICT_ALIASES.items()) + list(_EXTRA_ALIASES.items()):
        if d in canon:
            en.setdefault(k, d)
    seen = {}
    for d, stations in karnataka.STATIONS.items():
        for s in stations:
            base = re.sub(r"\s+(?:town|north|market)?\s*ps$", "", s.lower()).strip()
            if base and base not in _GENERIC_LOCALITIES and base not in en:
                seen.setdefault(base, set()).add(d)
    for base, ds in seen.items():
        if len(ds) == 1:            # a locality claimed by two districts places nothing
            en[base] = next(iter(ds))
    en = {re.sub(r"[\s-]+", " ", k): d for k, d in en.items()}
    kn = {}
    for d, names in KN_DISTRICTS.items():
        for n in names:
            kn[_stem_kn(n)] = d
    # Longest alternative first: at any position the longer name wins, so "bengaluru rural"
    # is never read as "bengaluru" followed by noise.
    en_alt = "|".join(r"[\s-]+".join(re.escape(p) for p in k.split(" ")) for k in sorted(en, key=len, reverse=True))
    kn_alt = "|".join(re.escape(k) for k in sorted(kn, key=len, reverse=True))
    return (re.compile(r"(?<![a-z0-9])(" + en_alt + r")(?![a-z])"), en,
            re.compile(r"(?<![\u0C80-\u0CFF])(" + kn_alt + ")"), kn)


_EN_DISTRICT_RX, _EN_DISTRICT, _KN_DISTRICT_RX, _KN_DISTRICT = _district_tables()


def _norm_text(s):
    s = _ZW.sub("", html.unescape(s or ""))
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s).strip()


def detect_districts(text):
    """Karnataka districts named in text, in order of first mention."""
    low = _norm_text(text).lower()
    hits = [(m.start(), _EN_DISTRICT[re.sub(r"[\s-]+", " ", m.group(1))]) for m in _EN_DISTRICT_RX.finditer(low)]
    hits += [(m.start(), _KN_DISTRICT[m.group(1)]) for m in _KN_DISTRICT_RX.finditer(low)]
    out = []
    for _pos, d in sorted(hits):
        if d not in out:
            out.append(d)
    return out


def resolve_district(value):
    """A canonical district name from a canonical name, alias or city, or None."""
    if not value:
        return None
    v = str(value).strip()
    for d in karnataka.DISTRICTS:
        if d.lower() == v.lower():
            return d
    found = detect_districts(v)
    return found[0] if found else None


def classify(text):
    """Category slugs that match text, in priority order (the first is the primary)."""
    low = _norm_text(text).lower()
    out = []
    for slug, en, kn in _CATEGORY_RX:
        if en.search(low) or (kn is not None and kn.search(low)):
            out.append(slug)
    return out


def resolve_category(value):
    """A category slug from a slug, a label or a loose word ("cyber", "drugs", "court")."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in CATEGORY_LABELS:
        return v
    for slug, label in CATEGORY_LABELS.items():
        if v == label.lower() or v.replace("&", "and") == label.lower().replace("&", "and"):
            return slug
    loose = {"cyber": "cybercrime", "cyber crime": "cybercrime", "cyber fraud": "cybercrime", "drugs": "narcotics",
             "drug": "narcotics", "ganja": "narcotics", "road safety": "traffic", "accidents": "traffic",
             "women": "women_child", "women safety": "women_child", "child safety": "women_child",
             "protests": "law_order", "protest": "law_order", "law and order": "law_order", "court": "courts",
             "judiciary": "courts", "governance": "policing", "police": "policing", "crimes": "crime"}
    if v in loose:
        return loose[v]
    found = classify(v)
    return found[0] if found else None


def detect_crime_types(text):
    """Dataset crime types named in text, most specific match first."""
    low = _norm_text(text).lower()
    # "Sexual assault" is not the dataset's Assault; the corpus has no sexual-offence head,
    # and linking one to Assault FIR counts would mislead.
    low = re.sub(r"sexual(?:ly)? assault\w*", " ", low)
    best = {}
    for term, ct, rx in _CRIME_TERMS:
        m = rx.search(low)
        if m and (ct not in best or len(term) > best[ct][0]):
            best[ct] = (len(term), m.start())
    return [ct for ct, _ in sorted(best.items(), key=lambda kv: (-kv[1][0], kv[1][1]))]


def is_karnataka(text, districts):
    low = _norm_text(text).lower()
    return bool(districts) or bool(KARNATAKA_RX.search(low)) or bool(KARNATAKA_KN.search(low))


# =============================================================================
# Normalisation
# =============================================================================
_TRACKING = re.compile(r"^(?:utm_.*|fbclid|gclid|ocid|cmpid|ref|ref_src|oc|ito|from|src|cmp)$")


def canonical_url(url):
    """The URL with scheme, www., tracking parameters, fragment and trailing slash
    normalised away — the de-duplication key. None for anything that is not http(s)."""
    try:
        p = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k.lower())]
    return urllib.parse.urlunsplit(("https", host, p.path.rstrip("/") or "/", urllib.parse.urlencode(q), ""))


def _domain(url):
    try:
        host = urllib.parse.urlsplit(url or "").netloc.lower()
    except ValueError:
        return None
    return host[4:] if host.startswith("www.") else (host or None)


def _publisher(domain):
    if not domain:
        return None
    if domain in PUBLISHERS:
        return PUBLISHERS[domain]
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):          # m.thehindu.com, kannada.oneindia.com
        tail = ".".join(parts[i:])
        if tail in PUBLISHERS:
            return PUBLISHERS[tail]
    return domain


def _http_url(u):
    u = (u or "").strip()
    return u if re.match(r"^https?://[^\s]+$", u, re.I) else None


# What an outlet's name looks like when a page title carries it as a suffix. Deliberately
# narrow: "Cops bust drug racket - India's biggest haul" must keep its tail.
_BRANDISH = re.compile(r"\bnews\b|\btimes\b|express|herald|\bhindu\b|india today|ndtv|\btv\s?\d*\b|mirror|"
                       r"\bdaily\b|\bpost\b|chronicle|prabha|vani\b|minute|theprint|\bmint\b|standard|"
                       r"\.com\b|\.in\b|\bpti\b|\bians\b", re.I)
_PUBLISHER_NAMES = {v.lower() for v in PUBLISHERS.values()}


def clean_title(title, source=None):
    """Headline without the outlet's name tacked on: Google appends " - Source", and page
    titles GDELT reports often end "| City News - Outlet"."""
    t = _norm_text(title)
    if source:
        for sep in (" - ", " | "):
            if t.endswith(sep + source):
                t = t[: -len(sep + source)].rstrip()
    for _ in range(2):
        m = re.match(r"^(.*\S)\s+[|\-]\s+([^|\-]{2,60})$", t)
        if not m:
            break
        head, tail = m.group(1), m.group(2)
        if len(head.split()) >= 4 and len(tail.split()) <= 6 and (
                (source and source.lower() in tail.lower()) or tail.strip().lower() in _PUBLISHER_NAMES
                or _BRANDISH.search(tail)):
            t = head
        else:
            break
    return t


def _title_key(title):
    t = re.sub(r"[^a-z0-9\u0C80-\u0CFF]+", " ", _norm_text(title).lower()).strip()
    return t if len(t) >= 20 else None


def _plain(fragment):
    """HTML description -> plain text."""
    if not fragment:
        return ""
    t = re.sub(r"<(?:br|/p|/li|/div)[^>]*>", " ", fragment, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return _norm_text(t)


def _summary(raw_html, title, source):
    """A summary worth showing, or None. Google's RSS description is usually just the
    headline and the outlet again, which is not a summary."""
    if raw_html and re.search(r"<li\b", raw_html, re.I):
        return None                  # a Google "full coverage" cluster: other headlines, not a summary
    text = _plain(raw_html)
    if not text:
        return None
    for chunk in (title, source):
        if chunk:
            text = re.sub(re.escape(chunk), " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|·•")
    if len(text) < 25:
        return None
    if len(text) > 280:
        text = text[:280].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _parse_rfc822(s):
    try:
        dt = email.utils.parsedate_to_datetime(s) if s else None
    except (TypeError, ValueError, IndexError):
        return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_gdelt_date(s):
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if s else None
    except (TypeError, ValueError):
        return None


def _parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_item(title, url, source, published, summary=None, image=None, origin=None):
    """One normalised, tagged item, or None when it has no usable headline or link."""
    url = _http_url(url)
    canon = canonical_url(url) if url else None
    title = clean_title(title, source)
    if not canon or not title:
        return None
    text = f"{title} {summary or ''}"
    districts = detect_districts(title)
    for d in detect_districts(summary or ""):
        if d not in districts:
            districts.append(d)
    cats = classify(text)
    return {
        "id": hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "summary": summary or None,
        "url": url,
        "source": source or _publisher(_domain(url)) or origin,
        "published": _iso(published),
        "image": _http_url(image),
        "lang": "kn" if KANNADA.search(title) else "en",
        "category": cats[0] if cats else "general",
        "categories": cats or ["general"],
        "districts": districts,
        "crime_types": detect_crime_types(text),
        "karnataka": is_karnataka(text, districts),
        "via": [origin] if origin else [],
        "also_in": [],
        "_canon": canon,
    }


# =============================================================================
# Parsers — one per source; each raises FetchError on a payload it cannot read
# =============================================================================
class FetchError(Exception):
    """A source could not be read. The message is shown to operators, so it says what
    happened in a few words and never carries a key or a full URL."""


def _bytes(payload):
    return payload.encode("utf-8") if isinstance(payload, str) else (payload or b"")


def _xml(payload):
    raw = _bytes(payload)
    # ElementTree does not resolve external entities, and the bundled expat (>= 2.4.1)
    # refuses entity-expansion bombs; an RSS feed has no business declaring entities at
    # all, so one that does is refused outright rather than parsed carefully.
    if b"<!ENTITY" in raw:
        raise FetchError("refused: feed declares XML entities")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        head = raw[:200].decode("utf-8", "replace").strip()
        raise FetchError("not an RSS feed" + (" (got HTML)" if head.lower().startswith(("<!doctype", "<html")) else ""))


def _json_payload(payload, label):
    text = _bytes(payload).decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text, strict=False)
    except ValueError:
        # GDELT reports query problems as a plain-text sentence with a 200.
        first = text.splitlines()[0][:140]
        raise FetchError(f"{label} returned text instead of JSON: {first}")


def parse_google_rss(payload, origin="Google News"):
    root = _xml(payload)
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        source = _norm_text(src.text) if src is not None and src.text else None
        if not source and src is not None:
            source = _publisher(_domain(src.get("url")))
        title = clean_title(it.findtext("title") or "", source)
        item = make_item(title, it.findtext("link"), source, _parse_rfc822(it.findtext("pubDate")),
                         summary=_summary(it.findtext("description"), title, source), origin=origin)
        if item:
            out.append(item)
    return out


def parse_gdelt(payload):
    data = _json_payload(payload, "GDELT")
    if not isinstance(data, dict):
        raise FetchError("GDELT returned an unexpected shape")
    out = []
    for a in data.get("articles") or []:     # GDELT answers {} when nothing matched
        domain = (a.get("domain") or _domain(a.get("url")) or "").lower()
        domain = domain[4:] if domain.startswith("www.") else domain
        item = make_item(a.get("title") or "", a.get("url"), _publisher(domain),
                         _parse_gdelt_date(a.get("seendate")), image=a.get("socialimage"), origin="GDELT")
        if item:
            out.append(item)
    return out


def parse_newsapi(payload):
    data = _json_payload(payload, "NewsAPI")
    if data.get("status") != "ok":
        raise FetchError(f"NewsAPI: {str(data.get('code') or data.get('message') or 'error')[:120]}")
    out = []
    for a in data.get("articles") or []:
        if (a.get("title") or "").strip() == "[Removed]":
            continue
        source = (a.get("source") or {}).get("name")
        title = clean_title(a.get("title") or "", source)
        item = make_item(title, a.get("url"), source, _parse_iso(a.get("publishedAt")),
                         summary=_summary(a.get("description"), title, source), image=a.get("urlToImage"),
                         origin="NewsAPI")
        if item:
            out.append(item)
    return out


def parse_gnews(payload):
    data = _json_payload(payload, "GNews")
    if data.get("errors"):
        errs = data["errors"]
        raise FetchError(f"GNews: {str(errs[0] if isinstance(errs, list) else errs)[:120]}")
    out = []
    for a in data.get("articles") or []:
        source = (a.get("source") or {}).get("name")
        title = clean_title(a.get("title") or "", source)
        item = make_item(title, a.get("url"), source, _parse_iso(a.get("publishedAt")),
                         summary=_summary(a.get("description"), title, source), image=a.get("image"),
                         origin="GNews")
        if item:
            out.append(item)
    return out


# =============================================================================
# Fetch layer — the only code that touches the network (tests replace _http_get)
# =============================================================================
def _http_get(url, params=None, headers=None):
    """GET url and return the body as bytes, bounded in time and size."""
    import requests

    h = {"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.5"}
    h.update(headers or {})
    deadline = time.monotonic() + ROUND_DEADLINE_S
    try:
        with requests.get(url, params=params, headers=h, timeout=FETCH_TIMEOUT, stream=True) as r:
            if r.status_code != 200:
                raise FetchError(f"HTTP {r.status_code}" + (" (rate limited)" if r.status_code == 429 else ""))
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FetchError("response larger than 3 MB")
                if time.monotonic() > deadline:
                    raise FetchError("timed out reading the response")
            return b"".join(chunks)
    except requests.exceptions.ProxyError:
        raise FetchError("blocked by the outbound proxy")
    except requests.exceptions.SSLError:
        raise FetchError("TLS handshake failed")
    except requests.exceptions.Timeout:
        raise FetchError("timed out")
    except requests.exceptions.ConnectionError:
        raise FetchError("unreachable (connection failed)")
    except requests.exceptions.RequestException as e:
        raise FetchError(type(e).__name__)


def _fetch_enabled():
    return os.environ.get("NEWS_FETCH", "on").strip().lower() not in ("off", "0", "false", "no", "disabled")


def _source_plan(lang):
    """{source: (feeds, reason_if_unconfigured)} where feeds is [(label, callable)]."""
    if not _fetch_enabled():
        off = "outbound fetching disabled (NEWS_FETCH=off)"
        return {s: ([], off) for s in SOURCE_ORDER}
    since = datetime.fromtimestamp(_now() - MAX_AGE_DAYS * 86400, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def google(q):
        params = dict(GOOGLE_EDITION[lang], q=f"{q} when:{MAX_AGE_DAYS}d")
        return lambda: parse_google_rss(_http_get(GOOGLE_RSS_URL, params=params))

    def gdelt():
        params = {"query": GDELT_QUERY[lang], "mode": "artlist", "format": "json", "maxrecords": "75",
                  "sort": "datedesc", "timespan": f"{MAX_AGE_DAYS}d"}
        return parse_gdelt(_http_get(GDELT_URL, params=params))

    plan = {"Google News": ([(q, google(q)) for q in GOOGLE_QUERIES[lang]], None),
            "GDELT": ([("artlist", gdelt)], None)}

    key = os.environ.get("NEWSAPI_KEY", "").strip()
    if lang != "en":
        plan["NewsAPI"] = ([], "no Kannada edition")
    elif not key:
        plan["NewsAPI"] = ([], "not configured (set NEWSAPI_KEY)")
    else:
        def newsapi():
            params = {"q": NEWSAPI_QUERY, "language": "en", "sortBy": "publishedAt", "pageSize": "50", "from": since}
            return parse_newsapi(_http_get(NEWSAPI_URL, params=params, headers={"X-Api-Key": key}))
        plan["NewsAPI"] = ([("everything", newsapi)], None)

    gkey = os.environ.get("GNEWS_API_KEY", "").strip()
    if lang != "en":
        plan["GNews"] = ([], "no Kannada edition")
    elif not gkey:
        plan["GNews"] = ([], "not configured (set GNEWS_API_KEY)")
    else:
        def gnews():
            params = {"q": GNEWS_QUERY, "lang": "en", "country": "in", "max": "50", "sortby": "publishedAt",
                      "from": since, "apikey": gkey}
            return parse_gnews(_http_get(GNEWS_URL, params=params))
        plan["GNews"] = ([("search", gnews)], None)
    return plan


def _scrub(msg):
    """Keys must never reach a response or a log line, whatever an exception carried."""
    msg = str(msg or "error")
    for env in ("NEWSAPI_KEY", "GNEWS_API_KEY"):
        k = os.environ.get(env, "").strip()
        if k:
            msg = msg.replace(k, "***")
    return re.sub(r"https?://\S+", "<url>", msg)[:200]


# =============================================================================
# Snapshot cache — one per language, shared by every request in the process
# =============================================================================
_SNAP = {}
_LOCKS = {"en": threading.Lock(), "kn": threading.Lock()}


def _now():
    """Wall-clock seconds. One seam, so tests can move time without patching `time`."""
    return time.time()


def reset():
    """Forget every snapshot (tests; an operator rotating keys)."""
    _SNAP.clear()


def _due(entry, now, force):
    """Whether one source should be read again now."""
    if entry is None or not entry.get("configured"):
        return True
    age = now - (entry.get("attempt_at") or 0)
    if force:
        return age >= MIN_FORCE_INTERVAL_S
    return age >= (CACHE_TTL_S if entry.get("ok") else RETRY_AFTER_ERROR_S)


def _merge(lists):
    """Concatenate source lists, folding duplicates (same canonical URL or same headline)
    into the first copy and filling its gaps — image, summary — from the others."""
    out, by_url, by_title = [], {}, {}
    for items in lists:
        for it in items:
            tk = _title_key(it["title"])
            hit = by_url.get(it["_canon"]) or (by_title.get(tk) if tk else None)
            if hit is not None:
                for k in ("image", "summary", "published"):
                    if not hit.get(k) and it.get(k):
                        hit[k] = it[k]
                for v in it.get("via") or []:
                    if v not in hit["via"]:
                        hit["via"].append(v)
                if it.get("source") and it["source"] != hit.get("source") and it["source"] not in hit["also_in"]:
                    hit["also_in"].append(it["source"])
                continue
            copy = dict(it, via=list(it.get("via") or []), also_in=list(it.get("also_in") or []))
            out.append(copy)
            by_url[copy["_canon"]] = copy
            if tk:
                by_title[tk] = copy
    return out


def _run_round(feeds):
    """Run [(source, label, fn)] concurrently under one deadline.
    Returns {source: {"items": [...], "errors": [...], "feeds": n, "ok_feeds": n}}."""
    res = {}
    if not feeds:
        return res
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(feeds)), thread_name_prefix="news")
    futs = {ex.submit(fn): (src, label) for src, label, fn in feeds}
    for src, _label, _fn in feeds:
        r = res.setdefault(src, {"items": [], "errors": [], "feeds": 0, "ok_feeds": 0})
        r["feeds"] += 1
    done, pending = concurrent.futures.wait(futs, timeout=ROUND_DEADLINE_S)
    for f in done:
        src, label = futs[f]
        try:
            items = f.result()
            res[src]["items"].extend(items)
            res[src]["ok_feeds"] += 1
        except FetchError as e:
            res[src]["errors"].append(f"{label}: {_scrub(e)}" if res[src]["feeds"] > 1 else _scrub(e))
        except Exception as e:                       # a parser bug must not take the page down
            LOG.warning("news source %s/%s failed: %s", src, label, _scrub(e))
            res[src]["errors"].append(_scrub(f"{type(e).__name__}"))
    for f in pending:
        src, label = futs[f]
        res[src]["errors"].append(f"{label}: timed out" if res[src]["feeds"] > 1 else "timed out")
    ex.shutdown(wait=False, cancel_futures=True)
    return res


def _refresh(lang, force=False):
    """Read every due source once and swap in a new snapshot for the language."""
    now = _now()
    prev = _SNAP.get(lang) or {"sources": {}}
    plan = _source_plan(lang)
    due_feeds, entries = [], {}
    for src in SOURCE_ORDER:
        feeds, reason = plan[src]
        old = prev["sources"].get(src)
        if not feeds:
            entries[src] = {"name": src, "configured": False, "ok": False, "items": [], "error": reason,
                            "attempt_at": now, "ok_at": None, "stale": False}
        elif _due(old, now, force):
            due_feeds += [(src, label, fn) for label, fn in feeds]
        else:
            entries[src] = old
    results = _run_round(due_feeds)
    for src, r in results.items():
        old = prev["sources"].get(src) or {}
        if r["ok_feeds"]:
            err = (f"{len(r['errors'])} of {r['feeds']} feeds failed — {r['errors'][0]}" if r["errors"] else None)
            entries[src] = {"name": src, "configured": True, "ok": True, "items": _merge([r["items"]]),
                            "error": err, "attempt_at": now, "ok_at": now, "stale": False}
        else:
            err = r["errors"][0] if len(r["errors"]) == 1 else (
                f"all {r['feeds']} feeds failed — {r['errors'][0]}" if r["errors"] else "no response")
            keep = old.get("items") if old.get("ok_at") and now - old["ok_at"] <= STALE_KEEP_S else []
            entries[src] = {"name": src, "configured": True, "ok": False, "items": keep or [], "error": err,
                            "attempt_at": now, "ok_at": old.get("ok_at"), "stale": bool(keep)}
            LOG.info("news source %s unreachable (%s)%s", src, err, "; serving last good items" if keep else "")
    ordered = [entries[s] for s in MERGE_ORDER if s in entries]
    snap = {"lang": lang, "sources": entries, "items": _merge([e["items"] for e in ordered]),
            "built_at": now}
    _SNAP[lang] = snap
    return snap


def _refresh_in_background(lang, lock):
    try:
        _refresh(lang)
    except Exception as e:
        LOG.warning("news background refresh failed: %s", e)
    finally:
        lock.release()


def snapshot(lang="en", force=False):
    """The current snapshot for a language, refreshing what is due.

    Only the first request ever waits for upstream. After that a due source is read in
    a background thread and the current snapshot is served immediately — a control-room
    screen should not stall for nine seconds because GDELT is slow. A forced refresh
    (the page's refresh button) waits, so the officer sees what they asked for.
    """
    lang = "kn" if lang == "kn" else "en"
    now = _now()
    snap = _SNAP.get(lang)
    plan_due = snap is None or any(_due(snap["sources"].get(s), now, force) for s in SOURCE_ORDER
                                   if (snap["sources"].get(s) or {}).get("configured", True))
    if not plan_due:
        return snap
    lock = _LOCKS[lang]
    if snap is not None and not force and BACKGROUND_REVALIDATE:
        if lock.acquire(blocking=False):
            threading.Thread(target=_refresh_in_background, args=(lang, lock), daemon=True,
                             name=f"news-refresh-{lang}").start()
        return snap
    if lock.acquire(timeout=ROUND_DEADLINE_S + 3):
        try:
            cur = _SNAP.get(lang)
            if cur is snap or cur is None:          # nobody refreshed while we waited
                _refresh(lang, force=force)
        except Exception as e:                      # serve what we have rather than a 500
            LOG.warning("news refresh failed: %s", e)
        finally:
            lock.release()
    return _SNAP.get(lang) or {"lang": lang, "sources": {}, "items": [], "built_at": now}


# =============================================================================
# The IntelHub join — FIR counts for a district / crime
# =============================================================================
_COUNTS = {"key": None, "val": None}


def _corpus_counts(cache):
    firs = (cache or {}).get("firs")
    if firs is None or not hasattr(firs, "empty") or firs.empty or "district" not in firs:
        return None
    key = (id(firs), len(firs))
    if _COUNTS["key"] == key:
        return _COUNTS["val"]
    import pandas as pd

    ts = pd.to_datetime(firs["timestamp"], errors="coerce")
    end = ts.max()
    if pd.isna(end):
        return None
    # The same window tool_situation reports ("last 30 days, records up to <date>"), so the
    # number on a news card and the number Kavya says for a district agree.
    recent = ts > end - pd.Timedelta(days=WINDOW_DAYS)
    d = firs["district"].astype(str)
    c = firs["crime_type"].astype(str) if "crime_type" in firs else pd.Series([""] * len(firs), index=firs.index)
    val = {
        "as_of": f"{end:%Y-%m-%d}",
        "d_total": d.value_counts().to_dict(), "d_recent": d[recent].value_counts().to_dict(),
        "dc_total": pd.DataFrame({"d": d, "c": c}).value_counts().to_dict(),
        "dc_recent": pd.DataFrame({"d": d[recent], "c": c[recent]}).value_counts().to_dict(),
        "c_total": c.value_counts().to_dict(), "c_recent": c[recent].value_counts().to_dict(),
        "crime_types": set(c.unique().tolist()),
    }
    _COUNTS.update(key=key, val=val)
    return val


def _cases_link(district=None, crime_type=None):
    q = {k: v for k, v in (("district", district), ("crime_type", crime_type)) if v}
    return "/cases" + ("?" + urllib.parse.urlencode(q) if q else "")


def dataset_link(cache, district=None, crime_type=None):
    """FIR counts in the corpus for a district (and/or a crime type), with a /cases link.
    None when the corpus is not loaded or neither is given."""
    counts = _corpus_counts(cache)
    if counts is None or not (district or crime_type):
        return None
    if crime_type and crime_type not in counts["crime_types"]:
        crime_type = None
        if not district:
            return None
    if district and crime_type:
        n30, n = counts["dc_recent"].get((district, crime_type), 0), counts["dc_total"].get((district, crime_type), 0)
    elif district:
        n30, n = counts["d_recent"].get(district, 0), counts["d_total"].get(district, 0)
    else:
        n30, n = counts["c_recent"].get(crime_type, 0), counts["c_total"].get(crime_type, 0)
    return {"district": district, "crime_type": crime_type, "cases_30d": int(n30), "cases_total": int(n),
            "window_days": WINDOW_DAYS, "as_of": counts["as_of"], "link": _cases_link(district, crime_type)}


def _intelhub_for(item, cache, focus_district=None):
    ds = item.get("districts") or []
    d = focus_district if focus_district in ds else (ds[0] if ds else None)
    ct = (item.get("crime_types") or [None])[0]
    if d:
        return dataset_link(cache, d, ct)
    if ct and item.get("karnataka"):
        return dataset_link(cache, None, ct)          # statewide: "N cyber-fraud FIRs in Karnataka"
    return None


# =============================================================================
# Query
# =============================================================================
def _age_hours(item, now):
    dt = _parse_iso(item.get("published"))
    return (now - dt.timestamp()) / 3600.0 if dt else None


def _rank_key(item, now):
    """Karnataka-specific and recent first. A Karnataka headline from yesterday outranks an
    out-of-state one from this morning; within Karnataka, newer wins. Undated items last."""
    age = _age_hours(item, now)
    if age is None:
        return (1, 0.0)
    score = max(age, 0.0) - (24.0 if item.get("karnataka") else 0.0) - (6.0 if item["category"] != "general" else 0.0)
    return (0, score)


def _matches_q(item, words):
    if not words:
        return True
    hay = _norm_text(f"{item['title']} {item.get('summary') or ''} {item.get('source') or ''}").lower()
    return all(w in hay for w in words)


def _status(sources):
    conf = [s for s in sources.values() if s.get("configured")]
    if not conf:
        return "disabled" if not _fetch_enabled() else "unreachable"
    if not any(s["ok"] for s in conf) and not any(s.get("items") for s in conf):
        return "unreachable"
    if any(s.get("stale") for s in conf):
        return "stale"
    return "ok"


def feed(cache=None, lang="en", category=None, district=None, q=None, crime_type=None, limit=DEFAULT_LIMIT,
         force=False, now=None):
    """The /news payload: filtered, ranked items plus facet counts and source status."""
    lang = "kn" if lang == "kn" else "en"
    snap = snapshot(lang, force=force)
    now = _now() if now is None else now
    words = [w for w in re.split(r"\s+", _norm_text(q).lower()) if w] if q else []

    pool = []
    for it in snap["items"]:
        if it["lang"] != lang:
            continue
        age = _age_hours(it, now)
        if age is not None and age > MAX_AGE_DAYS * 24:
            continue
        pool.append(it)
    pool.sort(key=lambda it: _rank_key(it, now))

    def keep(it, use_cat=True, use_district=True):
        if use_cat and category and category not in it["categories"]:
            return False
        if use_district and district and district not in it["districts"]:
            return False
        if crime_type and crime_type not in it["crime_types"]:
            return False
        return _matches_q(it, words)

    cat_counts, dist_counts = {}, {}
    facet_total = 0
    for it in pool:
        if keep(it, use_cat=False):
            facet_total += 1
            for c in it["categories"]:
                cat_counts[c] = cat_counts.get(c, 0) + 1
        if keep(it, use_district=False):
            for d in it["districts"]:
                dist_counts[d] = dist_counts.get(d, 0) + 1
    matched = [it for it in pool if keep(it)]
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    items = []
    for it in matched[:limit]:
        out = {k: v for k, v in it.items() if not k.startswith("_")}
        out["intelhub"] = _intelhub_for(it, cache, district)
        items.append(out)

    sources = []
    for s in SOURCE_ORDER:
        e = snap["sources"].get(s)
        if e is None:
            continue
        row = {"name": s, "ok": bool(e["ok"]), "configured": bool(e["configured"]), "count": len(e["items"]),
               "stale": bool(e.get("stale")), "last_ok": _iso(datetime.fromtimestamp(e["ok_at"], tz=timezone.utc))
               if e.get("ok_at") else None}
        if e.get("error"):
            row["error"] = e["error"]
        sources.append(row)

    ok_times = [e["ok_at"] for e in snap["sources"].values() if e.get("ok_at")]
    tries = [e["attempt_at"] for e in snap["sources"].values() if e.get("attempt_at")]
    status = _status(snap["sources"]) if snap["sources"] else "unreachable"
    counts = _corpus_counts(cache)
    return {
        "items": items,
        "total": len(matched),
        "facet_total": facet_total,
        "fetched_at": _iso(datetime.fromtimestamp(max(ok_times), tz=timezone.utc)) if ok_times else None,
        "checked_at": _iso(datetime.fromtimestamp(max(tries), tz=timezone.utc)) if tries else None,
        "status": status,
        "stale": status == "stale",
        "sources": sources,
        "categories": dict(sorted(cat_counts.items(), key=lambda kv: -kv[1])),
        "category_labels": CATEGORY_LABELS,
        "districts": dict(sorted(dist_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "district_options": karnataka.DISTRICT_NAMES,
        "lang": lang,
        "filters": {"category": category, "district": district, "q": q or None, "crime_type": crime_type},
        "dataset": {"records_up_to": counts["as_of"], "window_days": WINDOW_DAYS} if counts else None,
        "cache_ttl_s": CACHE_TTL_S,
        "note": NOTE,
    }


def _relative(iso, now):
    dt = _parse_iso(iso)
    if not dt:
        return "undated"
    mins = max(0, int((now - dt.timestamp()) // 60))
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} h ago"
    return f"{mins // 1440} days ago"


def brief(cache, district=None, category=None, crime_type=None, q=None, lang="en", limit=6, now=None):
    """The copilot's view of Live News: a few headlines, the matching IntelHub counts for
    the scope asked about, and the state of the sources. No `name` keys — the copilot's
    egress mask treats those as person names."""
    now = _now() if now is None else now
    # "News about cyber fraud" reads better as the Cybercrime category than as the headlines
    # that happen to contain one of the dataset's cyber-fraud keywords; other crime types
    # have no category of their own and filter on the crime type itself. The IntelHub
    # counts use the crime type either way.
    cat = category or CRIME_CATEGORY.get(crime_type)
    r = feed(cache, lang=lang, category=cat, district=district, q=q, crime_type=None if cat else crime_type,
             limit=max(1, min(int(limit or 6), 12)), now=now)
    category = cat
    heads = [{"title": it["title"], "source": it["source"], "published": it["published"],
              "when": _relative(it["published"], now), "category": CATEGORY_LABELS.get(it["category"], it["category"]),
              "districts": it["districts"], "url": it["url"]} for it in r["items"]]
    out = {"scope": district or "Karnataka", "topic": CATEGORY_LABELS.get(category) if category else None,
           "crime_type": crime_type, "query": q or None, "lang": r["lang"], "status": r["status"],
           "stale": r["stale"], "fetched_at": r["fetched_at"], "total_matching": r["total"], "headlines": heads,
           "records": dataset_link(cache, district, crime_type) if (district or crime_type) else None,
           "sources": [{"source": s["name"], "ok": s["ok"], "configured": s["configured"], "items": s["count"],
                        **({"error": s["error"]} if s.get("error") else {})} for s in r["sources"]],
           "page": "/news" + ("?" + urllib.parse.urlencode({"district": district}) if district else ""),
           "note": "Public headlines, not verified by KSP; not records."}
    if not heads and (district or category or crime_type or q) and r["status"] not in ("unreachable", "disabled"):
        latest = feed(cache, lang=lang, limit=3, now=now)
        out["latest_statewide"] = [{"title": it["title"], "source": it["source"],
                                    "when": _relative(it["published"], now)} for it in latest["items"]]
    return out


# =============================================================================
# HTTP
# =============================================================================
def _clean_json(obj):
    if isinstance(obj, dict):
        return {k: _clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_json(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return obj.item()
        except Exception:
            return str(obj)
    return obj


def _json(payload, status=200):
    resp = Response(json.dumps(_clean_json(payload), ensure_ascii=False), status=status,
                    mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


_ALL = ("", "all", "karnataka", "all karnataka", "statewide")


@bp.route("/serverless/api/news", methods=["GET"])
def news_route():
    """Open endpoint: public headlines and aggregate FIR counts, no person-level data."""
    a = request.args
    lang = (a.get("lang") or "en").strip().lower()
    if lang not in ("en", "kn"):
        return _json({"error": "lang must be 'en' or 'kn'"}, 400)
    raw_cat = (a.get("category") or "").strip()
    category = None
    if raw_cat.lower() not in _ALL:
        category = resolve_category(raw_cat)
        if not category:
            return _json({"error": f"unknown category '{raw_cat[:40]}'",
                          "categories": sorted(CATEGORY_LABELS)}, 400)
    raw_d = (a.get("district") or "").strip()
    district = None
    if raw_d.lower() not in _ALL:
        district = resolve_district(raw_d)
        if not district:
            return _json({"error": f"unknown district '{raw_d[:40]}'",
                          "districts": karnataka.DISTRICT_NAMES}, 400)
    crime_type = (a.get("crime_type") or "").strip() or None
    try:
        limit = int(a.get("limit") or DEFAULT_LIMIT)
    except ValueError:
        limit = DEFAULT_LIMIT
    q = (a.get("q") or "").strip()[:120] or None
    force = (a.get("refresh") or "").strip().lower() in ("1", "true", "yes")
    try:
        cache = _cache_getter() or {}
    except Exception:
        cache = {}
    return _json(feed(cache, lang=lang, category=category, district=district, q=q, crime_type=crime_type,
                      limit=limit, force=force))
