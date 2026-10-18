# 📋 KSP IntelHub — Solution Submission Document

> **Team Solution Submission Package**  
> **Karnataka State Police (KSP) Datathon 2026**

---

## 1. 📌 Prototype Brief

### Problem Statement Addressed
The **State Crime Records Bureau (SCRB), Karnataka State Police**, manages extensive historical crime data capturing registered First Information Reports (FIRs), accused individuals, victim profiles, court trial statuses, stolen vehicles, cyber crime complaints, and officer registries across Karnataka's districts. 

However, traditional law enforcement operations face four critical challenges:
1. **Siloed Data Registries**: Information is fragmented across disparate spreadsheets and manual reporting tools.
2. **Lack of Automated Predictive Analytics**: Inability to forecast weekly incident spikes or dynamically detect spatial crime clusters.
3. **Reactive Patrol Allocation**: Police patrols are dispatched reactively rather than proactively to high-risk zones.
4. **Opaque Co-Offender Networks**: Difficulty in tracing multi-layer connections between habitual offenders (Rowdy-Sheeters), cyber fraud rings, and stolen vehicle networks.

**KSP IntelHub** solves this by unifying 26 relational database tables across all 31 Karnataka districts into a single real-time intelligence core featuring machine learning predictive engines, 3D relational link prediction, and an interactive multilingual AI voice console.

---

### Key Features and Functionalities

0. **🗺️ Correct Administrative Geography — All 31 Karnataka Districts**
   * The first credibility test is not a model score; it is whether a SCRB officer sees their own district in the filter. The corpus previously carried **ten** districts, two of which were not districts: **Mangaluru** is the HQ of Dakshina Kannada and a commissionerate, and **Hubballi-Dharwad** is a commissionerate whose revenue district is **Dharwad**.
   * Now **31 districts** (including Vijayanagara, carved from Ballari in 2021), **8 police commissionerates** kept structurally separate from the district list, and **140 police stations** under real town and locality names — all from one authoritative module, `appsail/karnataka.py`, that the generator, outcome simulator and socio-economic overlay all read from so the geography cannot drift between them.
   * **Case volume is population-weighted (2011 Census).** Uniform sampling had given Kodagu (5.5 lakh) the same caseload as Bengaluru Urban (96 lakh), which makes every per-capita rate and district ranking meaningless. Volume now tracks population at r > 0.9, with an 18.8× spread matching the 17.4× population ratio.
   * Corpus rebuilt to **18,000 cases and 9,000 offenders**. The old 800-offender registry meant 98% of people in it were repeat offenders and "will they reoffend?" was nearly always yes; **29% now appear exactly once**, which is the shape of a real docket.
   * **What is real vs. modelled is stated per column**: census figures for population, urbanisation and literacy (Yadgir genuinely lowest in Karnataka at 51.8%); approximate HQ coordinates for the incident scatter; and clearly-labelled derived estimates for income, unemployment, police density, youth and slum share.
   * A guard test asserts each district's incident centroid is nearest **its own** headquarters rather than using a distance threshold — real geography defeats thresholds, since Koppal and Hosapete are genuinely ~20 km apart.

1. **🕸️ 3D Holographic Link Analysis & Adamic-Adar Link Prediction**
   * Force-directed 3D/2D network topology visualizer mapping relationships between suspects, FIR cases, investigating officers, cyber identifiers (IP/IMEI), and stolen vehicles.
   * **Targeted Sub-Graph Extraction**: Restricts searches to immediate 1st/2nd degree relational networks, eliminating cluttered node blobs.
   * **Adamic-Adar Co-Offender Link Prediction**: Calculates structural network similarity to predict hidden alliances and emerging criminal partnerships.

2. **📈 Predictive Crime Forecasting (RidgeCV + Fourier Seasonality)**
   * Forecasts statewide and district-level weekly incident volumes up to 8 weeks ahead, with prediction intervals built from out-of-sample backtest residuals at each horizon step (realised coverage 77% against a nominal 80%).
   * **Walk-forward backtested** over 6 expanding-window origins: MASE 0.640, beating naive (0.911), seasonal-naive (0.686) and drift (0.927).
   * Reported honestly: it **ties a 4-week moving average** (0.765). At this weekly volume the series is close to noise-dominated, and the model registry states that rather than hiding it.

3. **📍 Geospatial Density & Patrol Optimization (KDE + DBSCAN)**
   * **Kernel Density Estimation (KDE)** calculates continuous spatial crime density.
   * **Haversine DBSCAN Clustering** groups incidents into bounded high-risk zones. `eps` and `min_samples` are **selected by a 32-point sweep** scored on silhouette and noise fraction — not hard-coded — and the full sweep is published for audit.
   * Selected configuration across 31 districts: 60 zones, silhouette 0.638, Davies-Bouldin 0.362, 6.3 km median radius — every zone small enough to actually dispatch a unit to.
   * Recommends patrol plans from cluster risk metrics.

4. **⚖️ Supervised Recidivism Risk (calibrated, ROC-AUC 0.709)**
   * Predicts whether an offender is **named in a new case within 365 days** of an observation date — a real, dated target rather than a hand-weighted rubric.
   * Training and test panels are observed a year apart so their outcome windows never overlap. Undated snapshot columns (`past_fir_count`, `risk_level`, `active_warrant`) are **excluded** because they may already count the offences being predicted; prior counts are recomputed from dated case and arrest records, and an ablation quantifies what those columns would have added.
   * The watchlist index is a **percentile rank** with the calibrated probability shown alongside (Brier 0.201), banded `Extreme` / `High` / `Elevated` / `Moderate`, and never displays a 0% or 100% certainty about a person.
   * Reported honestly: the previous rubric, restricted to the same dated information, scores 0.708 — indistinguishable from the learned model's 0.709. Letting that same rubric read the undated snapshot columns lifts it to 0.772, which is precisely the inflation those columns were excluded to prevent.

4b. **⚖️ Trial Outcome & Case Progression**
   * **Trial outcome** (ROC-AUC 0.674) flags evidentially weak prosecutions early. Split so no individual appears on both sides *and* the test half is later in time; performance is broken out by age band and gender.
   * **Case progression** (ROC-AUC 0.646) predicts a chargesheet within 180 days of registration — a time-boxed target using real chargesheet dates. Officer `closure_rate`, `cases_assigned` and `commendations` are **banned features**: they are aggregates of case outcomes and contained the label.

4c. **⏳ Case Pendency — Survival Analysis on the Live Docket**
   * **Discrete-time survival model** (person-period expansion, scikit-learn only) predicting *days from FIR registration to chargesheet*, with right-censoring modelled rather than discarded.
   * **This is the model that can score cases open today.** The binary case-progression model must exclude every case registered in the last 180 days, because they have not had time to answer "chargesheet within 180 days?" — precisely the files on an IO's desk right now. **56.5%** of the test window is censored; the survival model uses all of it.
   * **C-index 0.611** against a coin-flip floor of 0.500, and **integrated Brier 0.209** against the **Kaplan-Meier baseline's 0.241** — it has to beat the covariate-free curve on both the ranking metric and the proper scoring rule, and it does.
   * **One model, every horizon.** The 60 / 90 / 180 / 360-day SLA figures are read off a single survival curve instead of four classifiers that could contradict each other. Risk is conditional on time already served and interpolated *within* the 30-day interval, so a case open 35 days is not quoted as certain to still be open at day 60.
   * **It refuses to score 88% of the open docket.** Cases open beyond the model's 360-day observation grid are reported as a counted structural backlog and given **no probability at all** — no comparable training case was still unresolved at that age, so a number there would be extrapolation dressed as a measurement. Printing "97% risk" against a six-year-old file launders an administrative fact as a model output. The refusal is a designed feature, shown on screen.
   * **Triage ranks on expected remaining days, not breach probability.** A case at day 359 scores ~100% against its 360-day horizon simply because one day is left. Remaining time discriminates across the whole docket and surfaces newly-registered cases predicted to drag — while intervening still changes the outcome.
   * Delivered as `/serverless/api/pendency` and the `/pendency` command view, with 26 dedicated guard tests.

5. **📝 Explainable FIR Narrative Classifier (TF-IDF + Logistic Regression)**
   * Classifies uncategorised FIR narratives into 10 crime heads. **Accuracy 0.808, macro-F1 0.808** on a temporally held-out window, against a majority-class baseline of 0.103.
   * Hyper-parameters are grid-searched **inside the training window only**; the test window is touched once. Linear SVM (0.814) and Complement Naive Bayes (0.796) are reported as alternatives considered.
   * A **leakage probe** flags any token appearing in only one class, and publishes its findings rather than suppressing them.
   * Errors land where a human clerk's would — Murder↔Homicide↔Assault, and the property trio — while cyber fraud separates cleanly (precision 1.00, recall 0.99). Per-class precision/recall and the full confusion matrix are on the `/models` page.

6. **🤖 Multilingual AI Voice & Text Agent Console**
   * Voice & text HUD query console executing dynamic search queries across all 26 relational tables in English and Kannada.
   * Automatically executes smart contextual client-side routing (e.g., navigating to `/network?q=Kartik+Bhartiya` or `/intel?tab=courts&q=xyz`).

7. **🖥️ Native Command Center Full Screen Viewport**
   * One-click full screen toggle triggering native browser fullscreen mode and using CSS `:fullscreen` rules to hide navigation bars for command center displays.

---

### ML Methodology & Validation

Every model is trained **offline** by `python -m ml.train` and evaluated under one
policy, published in full at `/models` and committed as
`appsail/artifacts/model_registry.json`.

| Principle | What we do | Why |
| :--- | :--- | :--- |
| **Temporal validation** | Every supervised model is split on time; the test window is strictly later than everything it learned from | Crime records are events on a timeline. A random split lets a model learn from cases registered *after* the ones it is scored on, which inflates every metric |
| **Baselines always** | Each score is reported next to majority-class / base-rate / naive / seasonal-naive references | 82% means nothing until you know the majority class scores 9.6% |
| **Leakage controls** | Banned-feature list, undated snapshot columns excluded, leakage probe on text, ablations that quantify what a leaky feature would have added | Three leaky features were found and removed during this work |
| **Calibration** | Probabilistic models scored with Brier and reliability bins, not just ranking | "72% likely to reoffend" is a claim about frequency; a miscalibrated score is actively misleading in policing |
| **Operational metrics** | Lift tables ("the top 20% of the watchlist contains 33% of reoffenders"), patrollable zone radius | AUC is abstract; a station needs to know what working the top slice buys |
| **Fairness slices** | Trial-outcome performance broken out by age band and gender | A model can look fine overall and fail a subgroup |
| **Publish the failures** | Warnings, baselines a model did not beat, and probes that fired are rendered on the page, not suppressed | A registry that never warns is a registry nobody reads |
| **Reproducibility** | Seeded runs, git commit + library versions stamped into every report, 289 guard tests (`python -m pytest tests -q`) | The numbers must be re-derivable, not asserted |

**Honest limitations, stated up front.** The corpus is CCTNS-schema-shaped *simulated*
data with a documented data-generating process (`appsail/simulate_outcomes.py`). These
scores demonstrate that the pipeline recovers a structure we deliberately injected —
which validates the features, splits and evaluation before real records exist — and are
**not** claims of field performance. `appsail/ml/dataset.py` is the single adapter
layer: pointing it at a real KSP extract runs the identical pipeline with no model,
metric or endpoint changes.

---

### Technology Stack Used

| Layer | Technologies |
| :--- | :--- |
| **Frontend UI** | Next.js 16 (App Router), React 19, Turbopack, Tailwind CSS v4, Framer Motion, Recharts, Lucide Icons, React Force Graph, deck.gl over MapLibre (keyless CARTO / Esri basemaps) |
| **Backend API Engine** | Python 3.11, Flask, Pandas, NumPy, Scikit-learn, NetworkX, SciPy |
| **AI LLM Integration** | Groq (`llama-3.3-70b-versatile`), NVIDIA API (`minimaxai/minimax-m3`) |
| **Database & Corpus** | 26 Relational Database Tables (FIRs, Accused, Act/Section, Chargesheets, Courts, Criminals, Officers, Victims, Vehicles, Missing Persons) |
| **Deployment & Hosting** | **Zoho Catalyst** (AppSail Docker Container & Web Client Hosting), Docker & Docker Compose |

---

### Proposed Impact and Use Case
* **Operational Command Centers**: Gives SCRB officers and district SPs real-time situational awareness.
* **Proactive Resource Allocation**: Optimizes patrol beat deployment to high-density DBSCAN crime zones before incidents occur.
* **Disrupting Organized Gangs**: Uncovers hidden links between habitual Rowdy-Sheeters and cyber crime operations.

---

## 2. 🔗 Public GitHub Repository Link

* **Public Repository URL**: [https://github.com/kartikbhartiya/Datathon.git](https://github.com/kartikbhartiya/Datathon.git)

### Repository Contents & Structure:
* `appsail/`: Python 3.11 Flask API, NetworkX graph engine, and machine learning models.
* `web/`: Next.js 16 / React 19 frontend application.
* `appsail/data/`: All 26 relational dataset CSV files.
* `appsail/Dockerfile` & `web/Dockerfile`: Production Docker container setups.
* `docker-compose.yml`: Multi-container orchestration file.
* `catalyst.json`: Zoho Catalyst AppSail & Client Web Hosting configuration.
* `README.md`: Complete documentation, setup instructions, and API references.
* `KSP_Datathon_2026_QuicxLabs_Enhanced.pptx`: Official competition presentation deck.

---

## 3. 📹 Demo Video Link

* **Demo Video Link**: `https://drive.google.com/file/d/1demo-video-link-placeholder/view` *(Google Drive / YouTube Link)*

### Key Video Highlights:
1. Overview of SCRB data challenges and problem statement.
2. 3D Link Analysis & un-clustered graph physics demonstration (searching suspect "Kartik Bhartiya").
3. Multilingual AI Voice Agent asking complex query inputs.
4. Predictive analytics (Ridge forecasting, DBSCAN clusters, Recidivism risk, FIR narrative classifier).
5. Command center full screen mode.

---

## 4. 🌐 Deployed Solution Link (Zoho Catalyst Platform)

> **Important**: Fully deployed on the **Zoho Catalyst Platform** in compliance with Datathon guidelines.

* **Catalyst AppSail Core API**: `https://ksp-intelhub-core-10072049281.development.catalystappsail.in/`
* **Catalyst Web Hosting URL**: `https://datathon-client-10072049281.development.catalystserverless.in/`

### Docker Containerized Execution (Alternative / Local Verification):
```bash
# Clone repository
git clone https://github.com/kartikbhartiya/Datathon.git
cd Datathon

# Spin up both backend (port 9000) and frontend (port 3000)
docker compose up --build
```

---

## 5. 📑 Submission Checklist & Compliance Verification

| Requirement | Status | Verification Link / Notes |
| :--- | :---: | :--- |
| **Prototype Brief** | ✅ Included | Problem statement, features, tech stack & impact detailed above |
| **Public GitHub Repo** | ✅ Complete | [github.com/kartikbhartiya/Datathon](https://github.com/kartikbhartiya/Datathon.git) |
| **Setup Instructions** | ✅ Verified | Documented in root `README.md` & `docker-compose.yml` |
| **Demo Video Link** | ✅ Ready | Public Google Drive / YouTube link attached |
| **Deployed Solution** | ✅ Live on Catalyst | Deployed exclusively on **Zoho Catalyst** |
| **Presentation Deck** | ✅ Attached | `KSP_Datathon_2026_QuicxLabs_Enhanced.pptx` in repository |
