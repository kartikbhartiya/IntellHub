# Data Protection Impact Assessment — working draft

**Status: DRAFT. Not a completed assessment.**

This document records everything about data protection that can be established from
the system itself: what personal data it processes, where that data goes, what
controls exist, and what the residual risks are. Each of those is verifiable against
the code and is written here as fact.

What it deliberately does **not** contain is the half a DPIA that only the deploying
authority can supply — the lawful basis relied on, the identity of the data
fiduciary and its Data Protection Officer, the necessity and proportionality
determination, the outcome of consultation, and sign-off. Those sections are marked
**[AUTHORITY]** and left blank. A document that filled them in with plausible text
would be worse than no document: it would look like an assessment had been carried
out when none had.

Under the **Digital Personal Data Protection Act, 2023**, an assessment of this kind
is required before this platform processes real police records.

| | |
|---|---|
| System | KSP IntelHub — Crime Intelligence & Analytical Platform |
| Assessed against | Commit state of this repository; see `GOVERNANCE.md` for the live controls list |
| Data fiduciary | **[AUTHORITY]** |
| Data Protection Officer | **[AUTHORITY]** |
| Lawful basis relied on | **[AUTHORITY]** |
| Date of assessment | **[AUTHORITY]** |
| Reviewed by | **[AUTHORITY]** |

---

## 1. What the system does

KSP IntelHub reads police records and produces analysis over them: crime trend
forecasts, geographic hotspots, case-pendency projections, link analysis between
people and cases, and four person-level risk and outcome scores.

Every output is decision support. Nothing the system produces authorises an action
against an individual, and no threshold triggers anything automatically — each one
orders a queue for a person to work. This is a design constraint, stated in
`GOVERNANCE.md` and reflected in the code, not an aspiration.

**Current processing state:** the deployed corpus is *synthetic*. No real police
record has been processed. That fact bounds every risk rating below and must be
re-read when it changes.

## 2. Personal data processed

Thirty-eight tables. The categories that carry personal data:

| Category | Examples | Sensitivity |
|---|---|---|
| Accused and offender identity | Name, age, gender, address, aliases, photograph reference, fingerprint identifier | **High** — criminal-history data, special category under most regimes |
| Victim identity | Name, age, gender, contact, relationship to accused | **High** |
| Criminal history | Prior cases, arrests, chargesheets, convictions, acquittals, rowdy-sheeter and history-sheeter flags | **High** |
| Case detail | FIR narrative, crime type, location, severity, witness counts | **High** — narratives are free text and may contain anything |
| Officer records | Name, rank, station, caseload | Moderate — employment data |
| Derived assessments | Conviction likelihood, recidivism risk band, case-closure and pendency projections | **High** — inferences about named individuals |
| Access records | Who viewed which person's file, when, and the reason given | Moderate — employment/conduct data |

Note the last two rows. The system does not only *hold* personal data, it **creates**
it: a risk band attached to a named person is new personal data about them, and is
treated as such throughout — it is disclosable, contestable, and erasable.

## 3. Data flow

```
CCTNS extract (CSV today; a relational source is supported and tested)
        │
        ▼
  ml/dataset.py ── the single adapter boundary; the only module that
        │           knows the physical schema
        ├──────────► offline training (ml/train.py) ──► artifacts/*.joblib
        │                                               + model_registry.json
        ▼
  in-memory corpus (main.py, one process)
        │
        ├──────────► HTTP API ──► browser (static Next.js export)
        ├──────────► audit.jsonl      (who accessed whose record)
        ├──────────► predictions.jsonl (what each person was scored)
        ├──────────► outcomes.jsonl    (what actually happened)
        ├──────────► contests.jsonl    (challenges raised)
        │
        └──────────► third-party LLM (Groq, then NVIDIA as fallback)
                     ⚠ the only egress outside the deployment
```

### 3.1 Third-party egress — the highest-risk flow

The copilot and agent features send prompt text to **Groq** (`api.groq.com`) and
**NVIDIA** (`integrate.api.nvidia.com`), both outside India and outside the
authority's control. This is the single point where personal data leaves the
deployment.

**Control in place:** `privacy.py` replaces person names with stable aliases before
any prompt is built, and restores them in the reply on the way back. The provider
receives the structure of the question and pseudonymised identifiers.

**Residual risk, stated plainly:** pseudonymisation is not anonymisation. A free-text
FIR narrative can identify a person without naming them — an address, a vehicle, an
unusual offence in a small district. Alias mapping does not remove that, and no
automated check can reliably detect it.

**[AUTHORITY]** must decide whether third-party LLM inference is permitted at all
against real records, and if so under what contractual terms (retention by the
provider, training use, jurisdiction). If it is not permitted, the copilot must be
disabled or pointed at a self-hosted model; the code path is a single configuration
change, and the rest of the platform functions without it.

## 4. Controls implemented

Each of these is enforced in code and covered by tests. This is what the assessment
can assert rather than promise.

| Control | How | Verify |
|---|---|---|
| Role-based access | Person-level routes require `investigator`; administrative and disclosure routes require `supervisor`. Aggregate statistics are open. | `tests/test_api.py` sweeps every route unauthenticated and asserts no offender name appears in any response |
| Field-level redaction | Fingerprint identifiers, photographs, family and caller contacts are allowlisted per role, not passed through by default | `auth.redact_watchlist_row` |
| Audit trail | Every person-level access records actor, role, subject, stated reason, IP and result. Reading the audit log is itself audited. | `GET /api/audit-log` |
| Tamper-evidence | Audit entries are hash-chained; any edit, reorder or deletion is detectable. Keyed with `AUDIT_HMAC_KEY` so the chain cannot be recomputed by someone with file access. Truncation is caught against the writer's own count. | `GET /api/audit-log/verify`; `tests/test_audit_chain.py` runs six tampering attacks |
| Reason-for-access | Callers may supply `X-Access-Reason`; accesses without one are counted and reported | `audit.summary()` |
| Retention schedule | Published per sink with its justification; purge implemented, previews by default | `GET /api/retention` |
| Erasure | A subject's scores and recorded outcomes are erased on request | `POST /api/retention/erase` |
| Disclosure | Everything held and asserted about one person, assembled in one report | `GET /api/subject-report/<id>` |
| Contestability | Challenges recorded with grounds; a contested score reaches the officer marked | `POST /api/contest` |
| Credential handling | Passwords hashed with `scrypt`; login rate-limited with lockout; no password is ever served | `auth.py` |
| No fabricated output | Every surface renders an honest error rather than invented content when the API fails | `tests/test_api.py`, and the frontend has no `FALLBACK_*` module literals |

## 5. Risks and mitigations

Rated for the system **as it would be with real records**, not as currently deployed
with synthetic data.

| # | Risk | Likelihood | Impact | Mitigation | Residual |
|---|---|---|---|---|---|
| 1 | Personal data reaches a third-party LLM provider in identifiable form | Medium | **High** | Name aliasing before egress; gated behind a role | **High** — free-text narratives can identify without naming. **[AUTHORITY]** decision required |
| 2 | An officer browses records with no legitimate purpose | Medium | High | Every access audited with actor and stated reason; tamper-evident; supervisor review endpoint | Medium — the log detects, it does not prevent. Requires an actual review process **[AUTHORITY]** |
| 3 | A person is treated as guilty because a model scored them high | Medium | **High** | No output authorises an action; every score labelled decision support; published accuracy including where models fail to beat trivial baselines; contest route marks disputed scores | Medium — depends on operational discipline and training **[AUTHORITY]** |
| 4 | A model degrades and keeps being trusted | High | High | Drift monitoring on inputs; realised-performance endpoint measuring outcomes against a base-rate baseline | **High** — no outcomes have been recorded, so realised performance is unmeasured. Structural gap, see `GOVERNANCE.md` |
| 5 | Model unfairness across district, gender or age | Medium | High | Fairness slices computed and published per model in the registry | Medium — measured and disclosed, not corrected. **[AUTHORITY]** must set acceptable disparity |
| 6 | Data retained indefinitely | High | Medium | Schedule published with justification; purge implemented | Medium — **nothing runs it on a timer**; enforcement is an operator action |
| 7 | Audit records altered to conceal improper access | Low | **High** | Hash chain, HMAC-keyed, verifiable on demand | Low — but the chain assumes a single writer process and does not defend against a compromised application |
| 8 | Credentials compromised | Medium | High | scrypt hashing, rate limiting, lockout, no password disclosure | Medium — no MFA, no SSO integration **[AUTHORITY]** |
| 9 | Historic API keys in git history | **Certain** | High | Keys are known-exposed at three commits | **Open** — rotation is a console action and has not been confirmed. See `SECURITY.md` |
| 10 | A subject never learns they were assessed | High | Medium | Disclosure and contest routes exist | **High** — nothing proactively notifies anyone. **[AUTHORITY]** must decide notification policy |

## 6. Data principal rights

| Right (DPDP Act 2023) | Status |
|---|---|
| Access to information about processing | `GET /api/subject-report/<id>` assembles it. **No self-service channel** — a person must go through the authority. **[AUTHORITY]** must define how a request is received and verified |
| Correction and erasure | Assessments erased via `POST /api/retention/erase`. **Corrections to source records are not possible** — the system is read-only over an upstream extract; corrections must be made in CCTNS and re-ingested |
| Grievance redressal | Challenges recorded and marked on the score. **No adjudication process** — who reviews, on what standard, in what time **[AUTHORITY]** |
| Nomination | Not applicable to this system |

**The most significant limitation:** the access log is deliberately **not** erasable
on subject request. It records who viewed a person's file — evidence of official
conduct rather than an assessment of the subject — and a log a subject could delete
would let anyone who improperly viewed a record destroy the proof by filing in that
subject's name. It expires on a three-year schedule instead. Every erasure report
states this rather than reporting a complete erasure it did not perform.
**[AUTHORITY]** should confirm this balance is acceptable under its legal advice.

## 7. Necessity and proportionality

**[AUTHORITY]**

This section requires a determination that cannot be made from the code: whether each
category of processing is necessary for the stated policing purpose, and whether the
same outcome could be achieved with less personal data. The system's own position is
recorded in `GOVERNANCE.md` — that its outputs order queues rather than authorise
actions — but whether that processing is proportionate is a decision for the data
fiduciary.

Points that should inform it:

* Person-level risk scoring is the most intrusive processing here and the least
  accurate. The registry publishes where each model fails to beat a trivial baseline;
  that evidence should be read before deciding these scores are necessary at all.
* Aggregate analysis — trends, hotspots, pendency — carries most of the operational
  value and far less risk. It could be deployed without the person-level scores.
* The third-party LLM flow is a convenience feature. Nothing else depends on it.

## 8. Consultation

**[AUTHORITY]** — including whether the State Crime Records Bureau, the DPO, and any
external reviewer have been consulted, and what they said.

## 9. Conclusion and sign-off

**[AUTHORITY]**

This draft cannot conclude. What it can state:

* The controls in §4 exist and are tested.
* Risks 1, 4, 9 and 10 are **open and material**, and risk 9 (exposed API keys)
  requires action before any deployment holding real records.
* No real personal data has been processed to date.

---

*Maintained alongside `GOVERNANCE.md` and `SECURITY.md`. When a control changes, this
document changes with it — a DPIA that describes a previous version of the system is
worse than none.*
