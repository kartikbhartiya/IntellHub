"""What the copilot knows about the platform itself.

The assistant used to know the corpus only through a twenty-line aggregate briefing,
and knew nothing about the product it lives in — asked "what does the pendency page
do" or "how is the recidivism model validated" it guessed. This module is the
platform's own description, condensed from README.md, GOVERNANCE.md, TECH_STACK.md
and the model registry, served to the copilot as a lookup tool rather than pasted
into every prompt.

Keep it factual. Every statement here is something the repository already says; when
the code changes, this is the file to update with it.
"""
import re

TOPICS = {
    "overview": {
        "title": "KSP IntelHub — what it is",
        "keywords": ["intelhub", "platform", "project", "what is this", "about", "overview",
                     "purpose", "datathon", "scrb", "problem", "solution", "who built"],
        "text": (
            "KSP IntelHub is an AI crime-intelligence platform built for the State Crime "
            "Records Bureau (SCRB) of the Karnataka State Police for the KSP Datathon 2026. "
            "It replaces siloed, Excel-based reporting with one in-memory intelligence core "
            "over a 26-table CCTNS-shaped relational schema: 18,000 FIRs, 9,000 offenders, "
            "28,355 accused links, victims, complainants, arrests, chargesheets, court "
            "records, 85 officers, 700 stolen vehicles, 686 cyber-fraud cases, 320 missing "
            "persons, 500 CCTV cameras and 1,600 Dial-112 calls across all 31 Karnataka "
            "districts and 140 police stations. On top of it sit a knowledge graph "
            "(~28k nodes, ~61k edges), seven validated ML models, and this copilot. "
            "Core problems addressed: data silos and manual reporting; lack of advanced "
            "analytics; reactive rather than proactive policing; and missing relational "
            "intelligence across suspects, vehicles, cyber rings and rowdy-sheeter registries. "
            "IMPORTANT: the corpus is simulated, CCTNS-schema-shaped data with a documented "
            "generating process — not real KSP records."),
    },
    "pages": {
        "title": "Screens in the web client",
        "keywords": ["page", "pages", "screen", "screens", "navigate", "menu", "where can i",
                     "where do i", "dashboard", "ui", "interface", "tab"],
        "text": (
            "Overview (/) — state-wide KPIs, trend alerts, anomalies and charts. "
            "Cases (/cases) — searchable FIR directory filtered by district, crime type, status. "
            "Case dossier (/case?id=) — one FIR: narrative, accused, victim, IO, court status, "
            "similar cases (MO + proximity + narrative similarity) and AI suspect matching. "
            "Map (/map) — Google Maps or Leaflet heatmap with KDE predicted hotspots, DBSCAN "
            "zones and time-of-day filters. "
            "Network (/network) — force-directed 2D/3D link graph of suspects, FIRs, officers, "
            "cyber identifiers and vehicles, with predicted links and syndicates. "
            "Analytics (/analytics) — forecast, clusters, recidivism, link prediction, "
            "socio-economic correlation and the model registry. "
            "Registry (/intel) — criminals/rowdy-sheeters, vehicles, missing persons, cyber, "
            "courts, officers tabs. "
            "Offender dossier (/suspect?id=) — one person: record, linked cases, associates, "
            "risk drivers. "
            "Officers (/officers) — roster, closure rates, assignment recommendations. "
            "Lifecycle (/lifecycle) — FIR → arrest → chargesheet → trial → conviction funnel. "
            "Pendency (/pendency) — survival-model triage of the open docket. "
            "Dispatch (/dispatch) — district risk index and recommended patrol strength. "
            "Access (/access) — sign-in, roles and the audit log."),
    },
    "data": {
        "title": "Data model",
        "keywords": ["data", "dataset", "schema", "table", "tables", "csv", "cctns", "columns",
                     "fields", "database", "records", "corpus", "simulated", "real data"],
        "text": (
            "26 relational tables mirroring CCTNS: CaseMaster (FIRs: CrimeNo, registration "
            "date, station, crime heads, status, court, coordinates, BriefFacts), Accused, "
            "Victim, ComplainantDetails, ArrestSurrender, ChargesheetDetails, "
            "ActSectionAssociation, CriminalsRegistry (rowdy_sheeter, history_sheet, "
            "active_warrant, gang, priors, known associates), CourtCasesRegistry, Employee/"
            "OfficersRegistry, Unit (police stations), District, Court, Act/Section, CrimeHead, "
            "plus lookup masters. Extended registries: stolen_vehicles, cybercrime (UPI ids, "
            "bank accounts, phones, IPs, URLs, wallets), missing_persons, cctv, complaints "
            "(Dial-112), socioeconomic (2011 Census-derived district indicators), "
            "case_reports (FIR narratives). Case ids are 18-digit CrimeNo values; offenders are "
            "SUS-####; officers are KGID######; missing persons MP-####. Real: district names, "
            "8 commissionerates, census population/literacy. Approximate: HQ coordinates. "
            "Derived: income, unemployment, police density. Everything else is simulated."),
    },
    "models": {
        "title": "The seven ML models",
        "keywords": ["model", "models", "ml", "machine learning", "accuracy", "auc", "metric",
                     "validated", "validation", "baseline", "registry", "how good", "trained"],
        "text": (
            "All are scored on a held-out window later in time than training, beside the "
            "trivial baseline they must beat (registry: artifacts/model_registry.json, page "
            "/analytics). 1) FIR narrative classifier — TF-IDF + logistic regression, "
            "10 classes, accuracy 0.808 vs majority 0.103. 2) Crime volume forecast — RidgeCV + "
            "Fourier seasonality, 8 weeks ahead, MASE 0.640, 77% realised coverage of an 80% "
            "interval; beats naive and seasonal-naive but NOT a 4-week moving average (0.634). "
            "3) Recidivism risk — re-offence within 365 days, ROC-AUC 0.709, calibrated; "
            "statistically indistinguishable from the dated hand rubric (0.708). "
            "4) Trial outcome — conviction vs acquittal, ROC-AUC 0.674. 5) Case progression — "
            "chargesheet within 180 days, ROC-AUC 0.646. 6) Case pendency — discrete-time "
            "survival, C-index 0.611, integrated Brier 0.209 vs Kaplan-Meier 0.241. "
            "7) Geospatial hotspots — haversine DBSCAN with a 32-point parameter sweep, "
            "silhouette 0.638. Also: KDE predicted hotspots, Isolation Forest anomalies, "
            "Adamic-Adar link prediction, Louvain syndicate detection, TF-IDF MO matching."),
    },
    "copilot": {
        "title": "This assistant (Kavya)",
        "keywords": ["you", "kavya", "assistant", "copilot", "who are you", "what can you do",
                     "capabilities", "help", "how do you work", "voice", "language", "kannada"],
        "text": (
            "Kavya is the IntelHub investigative copilot. It answers from the live corpus "
            "through retrieval tools — full case dossiers, person profiles and their case "
            "history, co-offender networks and paths between people, officers, stolen "
            "vehicles, cyber identifiers, missing persons, court records, filtered counts and "
            "breakdowns, and every ML output (forecast, hotspots, trends, recidivism, pendency, "
            "dispatch, syndicates). It can also drive the interface — open a dossier, the map, "
            "the network — and works by voice or text in English and Kannada. Every case, "
            "offender and officer id it names is verified against the corpus; person names "
            "are aliased before any prompt leaves the server. It will not declare anyone guilty, "
            "recommend action against a named individual, or turn a risk score into a "
            "prediction about a person."),
    },
    "governance": {
        "title": "Governance",
        "keywords": ["governance", "ethic", "ethics", "bias", "fair", "fairness", "evidence",
                     "court", "responsible", "limits", "feedback loop", "contest", "dpia",
                     "retention", "erasure", "rights"],
        "text": (
            "Six constraints (GOVERNANCE.md): 1) no output is evidence; 2) nothing is stated as "
            "certainty — probabilities are calibrated and never 0% or 100%; 3) no output "
            "triggers an action automatically — thresholds order a queue for a person; "
            "4) every number is traceable to its source; 5) every model is measured against "
            "doing nothing (a baseline); 6) subgroup performance is checked (age band, gender). "
            "Contestability: /subject-report/<id> lists everything held about a person and "
            "/contest records a challenge, which marks the score on the serving path. "
            "Retention: /retention publishes schedules; erase and purge preview by default. "
            "Feedback-loop risk: recorded crime reflects enforcement as much as offending. "
            "Known gaps: challenge adjudication, scheduled retention, field outcomes and DPIA "
            "sign-off are absent and stated as such."),
    },
    "security": {
        "title": "Access control, audit and privacy",
        "keywords": ["security", "access", "login", "role", "roles", "analyst", "investigator",
                     "supervisor", "audit", "privacy", "pii", "alias", "password", "auth"],
        "text": (
            "Roles: analyst (aggregates only), investigator (cases, dossiers, network, "
            "copilot), supervisor (watchlist, audit log, settings, retention). Management "
            "statistics are open; aggregates containing person-level blocks are pseudonymised "
            "when signed out; dossiers return 403. Every person-level access is audited — "
            "actor, role, subject, stated reason (X-Access-Reason), IP, result — in a "
            "hash-chained (optionally HMAC-keyed) log verifiable at /audit-log/verify. "
            "Person names are replaced with [PERSON_n] aliases before any prompt reaches the "
            "LLM providers and restored locally. Passwords are scrypt-hashed with login "
            "lockout after 8 failures in 5 minutes."),
    },
    "tech": {
        "title": "Technology stack and deployment",
        "keywords": ["tech", "stack", "technology", "framework", "deploy", "deployment",
                     "catalyst", "appsail", "next", "react", "flask", "python", "llm", "groq",
                     "nvidia", "llama", "hosting", "architecture"],
        "text": (
            "Frontend: Next.js 16 (static export), React 19, Tailwind v4, Framer Motion, "
            "Recharts, Google Maps / Leaflet, react-force-graph + three.js. Backend: Python 3.13, "
            "Flask, Gunicorn on Zoho Catalyst AppSail, pandas/numpy, networkx, scikit-learn "
            "1.7.2 (the whole ML layer), CSV-backed in-memory store. LLM: Groq "
            "llama-3.3-70b-versatile first, NVIDIA as fallback, via OpenAI-compatible tool "
            "calling; a deterministic retrieval engine answers when no provider is reachable. "
            "Client and API are on separate Catalyst domains; models load from committed "
            "joblib artifacts."),
    },
    "domain": {
        "title": "Policing terms",
        "keywords": ["rowdy", "sheeter", "history sheet", "history-sheet", "warrant", "fir",
                     "chargesheet", "charge sheet", "ipc", "bns", "io", "investigating officer",
                     "commissionerate", "cctns", "mo", "modus operandi", "pending trial"],
        "text": (
            "FIR — First Information Report, the registration of a cognisable offence; here "
            "identified by an 18-digit CrimeNo. Rowdy-sheeter — a person on a station's rowdy "
            "register (Karnataka Police Manual) for habitual violence, extortion or gang activity; "
            "tracked as CriminalsRegistry.rowdy_sheeter. History-sheeter — habitual offender "
            "under surveillance (history_sheet). Active warrant — an outstanding court warrant "
            "(active_warrant). Chargesheet — the final report filed in court after "
            "investigation; status 'Charge Sheeted'. Pending trial — chargesheeted cases "
            "awaiting verdict in CourtCasesRegistry. IO — investigating officer (KGID). "
            "MO — modus operandi: weapon, vehicle, target type and time of day. "
            "IPC/BNS — Indian Penal Code and its 2023 successor, Bharatiya Nyaya Sanhita. "
            "Commissionerates (Bengaluru, Mysuru, Mangaluru, Hubballi-Dharwad, Belagavi, "
            "Kalaburagi, Ballari, ...) are police jurisdictions separate from the 31 districts."),
    },
}


def lookup(query):
    """Best-matching knowledge sections for a free-text topic, most relevant first."""
    q = (query or "").lower()
    scored = []
    for key, t in TOPICS.items():
        score = 0
        if key in q:
            score += 3
        for kw in t["keywords"]:
            if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", q):
                score += 2 if " " in kw else 1
        if score:
            scored.append((score, key))
    scored.sort(reverse=True)
    keys = [k for sc, k in scored[:2] if sc >= scored[0][0] - 1] or ["overview"]
    out = [{"topic": k, "title": TOPICS[k]["title"], "text": TOPICS[k]["text"]} for k in keys]
    # A definition question wants the sentence that defines the term, not the section.
    terms = [w for w in re.findall(r"[a-z][a-z-]{2,}", q) if w not in _STOP]
    if terms:
        sentences = re.split(r"(?<=\.)\s+(?=[A-Z])", out[0]["text"])
        hits = [s for s in sentences if any(t.rstrip("s") in s.lower() for t in terms)]
        if hits and len(hits) < len(sentences):
            out[0]["excerpt"] = " ".join(hits[:3])
    return out


_STOP = {"what", "is", "are", "the", "does", "mean", "meaning", "define", "explain", "who", "how",
         "tell", "about", "this", "that", "and", "for", "with", "you", "your", "can", "platform",
         "project", "intelhub", "of", "in", "a", "an", "me", "please", "kind", "type"}


def is_platform_question(text):
    """True when a question is about the product itself rather than the records."""
    q = (text or "").lower()
    cues = ("this platform", "this project", "this app", "intelhub", "intel hub", "the platform",
            "who are you", "what can you do", "what are you", "your capabilities", "tech stack",
            "how does this work", "how was this built", "what models", "which models",
            "governance", "what is this", "how do you work", "about the project",
            "how is the data", "is the data real", "what pages", "what screens", "datathon")
    return any(c in q for c in cues)
