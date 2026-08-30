"""Central configuration for the ML layer.

Anything a reviewer might want to challenge — split dates, horizons, which
features are banned as leaky — is declared here rather than buried in model code.
"""
import os

ML_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(ML_DIR)
DATA_DIR = os.path.join(APP_DIR, "data")
ARTIFACT_DIR = os.path.join(APP_DIR, "artifacts")
REGISTRY_PATH = os.path.join(ARTIFACT_DIR, "model_registry.json")

RANDOM_STATE = 42

# ---------------------------------------------------------------- evaluation policy
# Crime data is a time series of events. A random train/test split lets the model
# learn from the future to predict the past, which inflates every metric. Every
# supervised task below is therefore split on time: the model only ever sees cases
# registered strictly before the cutoff and is scored on cases after it.
TEST_FRACTION = 0.2          # last 20% of the timeline is held out
VALID_FRACTION = 0.15        # of the training window, for threshold / calibration

# Forecast backtesting
FORECAST_HORIZON = 8         # weeks predicted forward
BACKTEST_FOLDS = 6           # walk-forward origins
BACKTEST_MIN_TRAIN_WEEKS = 40

# Prediction-interval nominal coverage (used for both construction and scoring)
PI_COVERAGE = 0.80

# ---------------------------------------------------------------- leakage controls
# Officer aggregates are computed *from case outcomes*, including the very case
# being scored. Feeding them to a case-closure model is target leakage: the label
# is partly inside the feature. They are banned outright.
BANNED_ALLOCATION_FEATURES = {
    "closure_rate",       # = closed cases / assigned cases -> contains the label
    "cases_assigned",     # denominator of the above; scales with outcome history
    "commendations",      # awarded for closures -> post-outcome information
}

# Text spans that must never reach the narrative classifier. The synthetic corpus
# embeds a structured metadata tag and the generator's own category vocabulary; a
# model that reads either is scoring the generator, not the language.
NARRATIVE_STRIP_PATTERNS = [
    r"\[METADATA:[^\]]*\]",          # SEV/WPN/VEH/TGT/FAT/INJ/WIT tag
    r"Booked under [^.]*\.",         # legal section reveals the crime head verbatim
]

# ---------------------------------------------------------------- recidivism target
# "Recidivism" is only meaningful against a clock: we observe an offender's record
# up to OBSERVE_CUTOFF and ask whether they are named in a *new* case during the
# following FOLLOW_UP_DAYS. Both windows are data-derived at train time.
RECIDIVISM_FOLLOW_UP_DAYS = 365
RECIDIVISM_MIN_HISTORY_DAYS = 180

# ---------------------------------------------------------------- registry metadata
DATA_PROVENANCE = {
    "source": "synthetic",
    "generator": "convert_data.py / generate_data.py (seeded, deterministic)",
    "note": (
        "No production KSP records were used. The corpus is CCTNS-schema-shaped "
        "synthetic data. Metrics below are honest measurements against that corpus "
        "and are NOT claims about real-world field performance."
    ),
}


# ---------------------------------------------------------------- model cards
# The governance half of the registry. Metrics say how well a model does what it
# does; these say what it is for, what it must not be used for, who answers for it
# and at what score anyone is expected to act. Reviewed alongside the metrics, not
# separately — which is why they live here and are published into the same report.
#
# REVIEW_BY is deliberately a date, not "periodically". A cadence with no date is a
# cadence nobody misses.
MODEL_OWNER = "SCRB Data Science (prototype — no production owner assigned)"
REVIEW_BY = "2027-02-28"

MODEL_CARDS = {
    "narrative_classifier": {
        "intended_use": "Triage: suggest a crime head for an FIR whose narrative has "
                        "been filed without one, so a human can confirm it.",
        "out_of_scope": "Re-classifying a case already assigned a head by an officer, "
                        "and any use as the sole basis for a filing decision. The "
                        "model reads language, not evidence.",
        "decision_threshold": "No automatic action. A suggestion below 0.60 predicted "
                              "probability should be shown as 'uncertain' rather than "
                              "as a recommendation.",
        "monitoring": "Watch the share of predictions falling below 0.60 and the "
                      "confusion between Murder / Homicide / Assault; a shift there "
                      "usually means filing practice changed, not that the model did.",
        "retrain_cadence": "On any change to the narrative corpus, and at least "
                           "every 6 months.",
    },
    "crime_forecast": {
        "intended_use": "Anticipate district-level weekly incident volume to inform "
                        "rostering and patrol planning several weeks ahead.",
        "out_of_scope": "Any individual-level inference, and any claim about *where* "
                        "within a district an offence will occur. It forecasts counts.",
        "decision_threshold": "None. The interval, not the point estimate, is the "
                              "output — a plan that only works at the central value "
                              "is not a plan.",
        "monitoring": "Realised interval coverage against the nominal 80%, and MASE "
                      "against the moving-average baseline it currently ties.",
        "retrain_cadence": "Weekly once live; the seasonality terms need recent data.",
    },
    "recidivism_risk": {
        "intended_use": "Rank registered offenders for allocation of finite "
                        "supervisory attention.",
        "out_of_scope": "Bail, sentencing, parole, or any custodial decision. Also "
                        "any use against a person not already on the register. The "
                        "index is a ranking instrument, not a statement about a "
                        "person's character or intent.",
        "decision_threshold": "Percentile bands: top 5% Extreme, next 15% High, next "
                              "30% Elevated. Bands allocate review effort; none of "
                              "them authorises an action against an individual.",
        "monitoring": "Band composition over time, calibration drift (Brier), and "
                      "whether the learned model still beats the dated rubric — it "
                      "currently only ties it.",
        "retrain_cadence": "Quarterly, with a fresh observation panel each time.",
    },
    "conviction_outcome": {
        "intended_use": "Flag prosecutions that look evidentially weak early enough "
                        "for the investigating officer to strengthen the file.",
        "out_of_scope": "Presentation to a court in any form, advising a plea, or "
                        "any use suggesting a defendant is likely guilty. It predicts "
                        "case-file strength, not culpability.",
        "decision_threshold": "No automatic action. Intended as a queue for case "
                              "review, worked from the lowest predicted probability up.",
        "monitoring": "Fairness slices by age band and gender must be re-checked "
                      "every run; a widening gap is a stop condition, not a note.",
        "retrain_cadence": "Quarterly.",
    },
    "case_closure": {
        "intended_use": "Estimate which registered cases are unlikely to reach a "
                        "chargesheet within 180 days, to inform supervisory follow-up.",
        "out_of_scope": "Assessing an individual officer's performance. Station load "
                        "and case complexity dominate this target, and using it for "
                        "appraisal would penalise officers for their caseload.",
        "decision_threshold": "No automatic action. Used to order a review queue.",
        "monitoring": "Base rate drift — the target is time-boxed, so a change in "
                      "filing practice moves it without the model changing at all.",
        "retrain_cadence": "Quarterly.",
    },
    "case_pendency": {
        "intended_use": "Identify open FIRs likely to remain pending, and where "
                        "backlog is accumulating.",
        "out_of_scope": "Scoring cases beyond the observation grid — the model "
                        "refuses these rather than extrapolating, and that refusal "
                        "must not be worked around downstream.",
        "decision_threshold": "0.7 predicted probability of still being open at the "
                              "horizon marks a case for supervisory review.",
        "monitoring": "Share of the open docket the model declines to score; if that "
                      "grows, the grid needs extending rather than the refusal relaxing.",
        "retrain_cadence": "Quarterly.",
    },
    "hotspot_clusters": {
        "intended_use": "Identify concentrated incident zones small enough to patrol.",
        "out_of_scope": "Any inference about the people who live in a zone. A hotspot "
                        "is a property of recorded incidents, and recorded incidents "
                        "reflect where enforcement has previously been directed.",
        "decision_threshold": "None. Zones are ranked by incident count; the median "
                              "zone radius is held under 15 km so a zone is a place a "
                              "unit can actually be sent.",
        "monitoring": "Whether zones re-appear in the same places every cycle, which "
                      "would indicate the feedback loop rather than a stable pattern.",
        "retrain_cadence": "Monthly.",
    },
}

# The platform-level statement. Kept here so it is versioned with the code and
# served with the metrics, rather than existing only as UI copy.
GOVERNANCE = {
    "principle": ("KSP IntelHub does not infer guilt. It surfaces relationships and "
                  "priorities in recorded data for authorised operational review."),
    "no_output_is_evidence": ("Risk scores, conviction outlooks and link predictions "
                              "are decision support for allocating attention. None of "
                              "it is evidence and none of it may be presented to a "
                              "court as such."),
    "human_in_the_loop": ("No model output triggers an action automatically. Every "
                          "threshold above orders a queue for a person to work; none "
                          "authorises an action against an individual."),
    "feedback_loop_risk": ("Predictive policing systems carry a documented risk of "
                           "entrenching the patterns in their own training records: "
                           "enforcement concentrates where past enforcement was "
                           "recorded, generating the data that justifies concentrating "
                           "there. Nothing in this platform is exempt from that."),
    "contestability": ("Implemented. GET /api/subject-report/<id> discloses everything "
                       "held and asserted about one person - every score with its date, "
                       "each model's stated limits, and who accessed the record. "
                       "POST /api/contest records a challenge, and the register is read "
                       "on the serving path so a contested score reaches the officer "
                       "marked. What is NOT implemented is adjudication: nothing decides "
                       "whether a challenge is well-founded and no challenge suppresses "
                       "a score. That is the deploying authority's decision and this "
                       "platform must not make it silently."),
    "owner": MODEL_OWNER,
    "review_by": REVIEW_BY,
}
