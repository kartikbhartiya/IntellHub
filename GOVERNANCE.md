# Governance — KSP IntelHub

This document states what the platform is for, what it must not be used for, and the
limits it operates under. It is the human-readable half of a pair: the machine-readable
half lives in [`appsail/ml/config.py`](appsail/ml/config.py) (`MODEL_CARDS`,
`GOVERNANCE`) and is published with every training run into
[`appsail/artifacts/model_registry.json`](appsail/artifacts/model_registry.json), which
the `/models` page renders.

They are kept together deliberately. Governance in a separate document drifts from the
system it governs; governance served beside the metrics is read by the same person, at
the same moment, as the number it qualifies.

**Owner:** SCRB Data Science (prototype — no production owner assigned)
**Next review:** 2027-02-28

---

## The principle

**KSP IntelHub does not infer guilt.** It surfaces relationships and priorities in
recorded data for authorised operational review.

Everything below follows from that sentence.

---

## Six constraints

### 1. No output is evidence

Risk scores, conviction outlooks, link predictions and hotspot zones are decision
support for allocating attention. None of it is evidence, and none of it may be
presented to a court as such.

### 2. Nothing is stated as certainty

Person-level probabilities are calibrated and never display 0% or 100%. Where a model
is outside its supported range — a case older than its observation grid — the platform
**refuses to score it** rather than extrapolating, and reports how much of the docket
it declined.

### 3. No output triggers an action automatically

Every threshold in the model cards orders a queue for a person to work. None of them
authorises an action against an individual. A human decision sits between every score
and every consequence.

### 4. Every number is traceable

Each model publishes its split dates, baselines, calibration curve, feature
importances and the warnings its own training run raised. Every score handed to a
person is logged, every published model keeps its previous versions so a bad one can
be rolled back, and `/models/drift` reports how far the current population has moved
from the one each model learned on — naming the models it cannot monitor and why,
rather than omitting them so that silence reads as safety. The composite District
Operational Risk Index and the dispatch staffing recommendation publish their weights
and per-component arithmetic — a composite score with no visible arithmetic is the
easiest artefact in this domain to produce and the hardest to defend.

### 5. Measured against doing nothing

Every model is reported next to the trivial baseline it has to beat, **including where
it fails to**. The crime forecast still ties a 4-week moving average, and the registry
says so on the page rather than in a footnote.

### 6. Subgroup performance is checked

Trial-outcome accuracy is broken out by age band and gender, so a model that works
overall but fails a subgroup cannot pass unnoticed. A widening gap is a stop
condition, not a note.

---

## Contestability

Automated assessment without a route to challenge it is the failure mode every
framework for this exists to prevent. Three things are needed and all three exist:

* **Disclosure.** `GET /api/subject-report/<id>` (supervisor-only, audited) assembles
  everything the system holds and asserts about one person: every score with its date,
  the model's own stated limits and out-of-scope uses, who accessed the record and the
  reason they gave, and any challenge already raised. A person cannot contest an
  assessment they cannot see.
* **A recorded challenge.** `POST /api/contest` records one against a named subject
  and model. Grounds are required — an unreviewable challenge helps the person who
  raised it least of all — and the stated basis is kept verbatim. Resolutions are
  appended, never written over the original, so the history of a disputed assessment
  stays readable.
* **Consequence.** The contest register is consulted on the serving path, so a
  contested score reaches the officer marked wherever it is displayed. A challenge
  that changed nothing about what the officer sees would be a complaints box, not a
  control.

Filing does **not** suppress the score. Whether a challenge is well-founded is a
decision for the deploying authority, and this platform deliberately does not make it
silently — see the gaps table.

## Retention and erasure

Three runtime logs accumulate personal data: who accessed whose record, what score
each person was given, and what happened to them. Each has a published retention
period with a stated justification, readable at `GET /api/retention`.

**Erasure is deliberately partial, and the reason is on the record.** A subject's
*assessments* — their scores and recorded outcomes — are erased on request. The
*access log* is not. It records who viewed their file: evidence of official conduct
rather than an assessment of the subject. A log that a subject could delete would let
anyone who improperly viewed a record destroy the proof by filing an erasure request
in that subject's name. It expires on its own three-year schedule instead, and every
erasure report states plainly what was retained and why.

The audit log is also purged at whole-file granularity rather than per entry, because
removing individual lines would break the hash chain that makes it verifiable. A
tamper-evidence control that gets rebuilt whenever it is inconvenient proves nothing.

## Access, audit and PII

* **Aggregate surfaces are open.** District funnels, forecasts, hotspot zones and the
  model registry identify nobody, and the platform has to be legible to a reviewer
  with no credentials.
* **Person-level surfaces are gated**, and the two aggregates that contain a
  person-level block (`/stats`, `/analytics`) return that block pseudonymised rather
  than refused, so the control is visible without credentials.
* **Every person-level access is audited** — who, which subject, stated reason, result.
  Entries are hash-chained, so the log can be checked rather than trusted: any edit,
  reorder or deletion is detectable through `GET /api/audit-log/verify`. Truncation of
  the newest entries is caught against the writer's own count, not by the chain itself.
  The chain does not defend against a compromised application process, and needs
  `AUDIT_HMAC_KEY` set to hold against someone with write access to the file.
  Reading the audit log is itself audited. Access without a stated reason is recorded
  as such so it is visible in review rather than indistinguishable.
* **Person names never reach the LLM providers.** The copilot injects real records into
  prompts sent to third-party services outside India; names are aliased before egress
  and restored locally in the answer.

---

## The feedback-loop risk

Predictive policing systems carry a documented risk of entrenching the patterns in
their own training records: enforcement concentrates where past enforcement was
recorded, which generates the very data that justifies concentrating there. A hotspot
is a property of *recorded incidents*, and recorded incidents reflect where officers
have previously been sent.

Nothing in this platform is exempt from that. It is built to be audited — which is why
the registry publishes what each model failed at alongside what it achieved, and why
the hotspot card asks whether zones re-appear in the same places every cycle.

---

## Surveillance matching

Face and plate matching is the feature in this platform most able to harm a person who
has done nothing, so it carries the tightest controls:

* **A match is a lead.** Every P1/P2 sighting waits in a queue; verifying or rejecting it
  requires a written note of what was checked, and the decision is logged append-only with
  the officer's name. Nothing is triggered by a match.
* **Strangers stay strangers.** A face that does not match the registry is counted and
  discarded at detection time. The feed never carries its identity.
* **Error rates are published, not assumed.** The threshold was set for under 0.5 % false
  matches on look-alikes; true-identification falls at night and on low-resolution cameras,
  and the page says by how much. A deployment must re-measure all of it on its own cameras and
  population, by age band and gender, before operational use.
* **What is simulated:** the camera network, the face embeddings and the plate reads. The
  matching, thresholds and measured rates are real computations on that data.

---

## Known gaps

Stated because their absence is a gap, not an oversight.

| Gap | Status |
|---|---|
| **Retention enforcement between restarts** | The schedule is published with its justification (`GET /api/retention`); purge and erasure are implemented and tested; and a retention pass runs **on every container start** when `RETENTION_ENFORCE=1`, logging the backlog either way. AppSail has no scheduler, so a restart is the only recurring event this application can hook — a long-lived container will hold past-retention entries until it next restarts or an operator calls the endpoint. |
| **Adjudicating a challenge** | Challenges are recorded, disclosed and marked on the score, but nothing decides whether one is well-founded and no challenge automatically suppresses an assessment. That is a decision for the deploying authority — this platform must not make it silently — so the process, the reviewer and the service standard all remain to be defined by whoever operates it. |
| **Operational outcomes** | The feedback loop now runs end to end, but on a **replay**, not on use. `replay_outcomes.py` scores the held-out window through the serving artifact and feeds its real recorded results back in: 905 matured pairs for conviction, 3,343 for case closure, both consistent with published evaluation. That proves the serving path reproduces offline evaluation and that the loop works — it is **not** field performance. `realised()` reports the provenance of every outcome and warns when all of them are replayed, and no prediction made in service of a real decision has been checked yet. |
| **Automatic retraining** | Manual. Drift and realised performance are both measured and reported; nothing acts on either. |
| **DPIA sign-off** | `DPIA.md` is a working draft: it records the data categories, flows, controls and residual risks that can be established from the code. The half only the deploying authority can supply — lawful basis, necessity and proportionality, consultation, sign-off — is marked `[AUTHORITY]` and left blank. Filling those in with plausible text would make it look as though an assessment had been carried out when none had. |

---

## The data

**No production KSP records were used.** The corpus is CCTNS-schema-shaped *simulated*
data over the real 31 Karnataka districts, generated by a documented process in
[`appsail/simulate_outcomes.py`](appsail/simulate_outcomes.py).

Every score in this system demonstrates that the pipeline can recover structure that
was deliberately injected into the data. That is the right thing to verify before real
records exist — it validates the features, the splits and the evaluation — and it is
**not** a claim of field performance. `appsail/ml/dataset.py` is the single adapter
layer: pointing it at a real extract runs the identical pipeline with no model, metric
or endpoint changes, at which point every figure must be re-measured before it is
quoted.
