"""Realistic FIR narrative corpus generator.

Why this file replaces `generate_narratives.py`
-----------------------------------------------
The previous generator wrote one lexically distinct template block per crime type
— its own docstring said the blocks were kept distinct "so TF-IDF can separate the
classes cleanly". That makes the downstream classifier circular: the label is
recoverable from the template fingerprint, so a reported ">90% accuracy" measures
the generator, not the model. It also embedded the legal section ("Booked under
IPC Sec 384"), which names the offence outright.

This generator builds a corpus a text classifier can be honestly evaluated on:

* **A shared police register.** Every narrative is assembled from the same
  scaffold and the same body of station vocabulary (mahazar, panchanama, seizure
  memo, IO, complainant, hobli) regardless of offence. Class-neutral words
  dominate the token count, as they do in real FIRs.
* **Overlapping, probabilistic vocabulary.** Distinctive phrasing is drawn with
  probability, not certainty, and confusable offences share a common pool. A
  narrative therefore *leans* toward its class rather than announcing it.
* **Genuinely confusable families.** Murder / Homicide / Assault sit in one pool
  and Chain Snatching / Vehicle Theft / House Break-in in another, because those
  heads genuinely overlap in the field. The resulting confusion matrix is a real
  criminological finding rather than an artefact to hide.
* **Terse and degraded reports.** ~16% of entries are two-line stubs and a small
  share carry transcription noise, mirroring how uneven station-level data entry
  actually is. These are the hard cases that keep the score honest.
* **No label giveaways.** Section numbers, crime-head names and the structured
  [METADATA:] tag are excluded from the narrative text.

The structured metadata tag is still appended to CaseMaster.BriefFacts, because
the loader parses severity/weapon/vehicle/target from it — but it is written
*after* the narrative and stripped before any text model sees it.

Deterministic (seeded). Run:  python generate_fir_narratives.py
"""
import argparse
import os
import random

import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SEED = 20260819

# ---------------------------------------------------------------- shared register
# These phrases carry no class signal. They are the bulk of every narrative, which
# is what forces the classifier to work for its score.
OPEN_FRAMES = [
    "A written complaint was received at {station} and a case came to be registered.",
    "The complainant appeared before {station} and lodged a report.",
    "On receipt of credible information, {station} registered the present case.",
    "A report was submitted at {station} and taken on file.",
    "The matter was reported to {station} by the aggrieved party.",
    "Information was received at {station} and the case was registered accordingly.",
    "A complaint petition was filed at {station} and numbered.",
]

TIME_FRAMES = [
    "The occurrence is stated to have taken place during the {tod} hours.",
    "As per the complaint the incident occurred in the {tod} period.",
    "The time of occurrence is placed in the {tod} hours.",
    "The incident is reported to have occurred during {tod}.",
]

PROCEDURE = [
    "The Investigating Officer visited the spot and drew up a mahazar in the presence of panchas.",
    "A spot panchanama was conducted and the scene was photographed.",
    "The IO proceeded to the place of occurrence and recorded the scene.",
    "A seizure memo was prepared for the articles recovered from the spot.",
    "Statements of the available witnesses were recorded under the relevant provisions.",
    "The place of occurrence was inspected and a rough sketch prepared.",
    "The complainant's statement was reduced to writing and read over to them.",
    "Necessary formalities were completed and the case diary opened.",
]

FOLLOWUP = [
    "CCTV footage from the surrounding area is being collected.",
    "Call detail records have been requisitioned from the service providers.",
    "The jurisdictional beat staff have been alerted.",
    "Enquiries are being made with known offenders of the area.",
    "A team has been constituted to trace the accused.",
    "The matter has been taken up for investigation by the jurisdictional IO.",
    "Further investigation is in progress.",
    "Efforts are on to identify and apprehend the persons involved.",
    "The complainant has been advised of the case number for follow-up.",
    "A wireless message was circulated to adjoining stations.",
]

WITNESS_LINE = [
    "{n} witness(es) have been examined so far.",
    "Statements of {n} witness(es) were recorded.",
    "{n} person(s) present at the spot have been cited as witnesses.",
]

ACCUSED_LINE = [
    "{n} person(s) have been named in the complaint.",
    "{n} accused stand named at this stage of the investigation.",
    "So far {n} individual(s) figure in the array of accused.",
]

# ---------------------------------------------------------------- confusable families
# Offences within a family draw from a common pool, so their narratives genuinely
# resemble one another the way the underlying offences do.
FAMILY = {
    "Murder": "violent",
    "Homicide": "violent",
    "Assault": "violent",
    "Chain Snatching": "property",
    "Vehicle Theft": "property",
    "House Break-in (Night)": "property",
    "Extortion": "organised",
    "Narcotics Distribution": "organised",
    "Sandalwood Smuggling": "organised",
    "Cyber Fraud (Jamtara style)": "cyber",
}

FAMILY_POOL = {
    "violent": [
        "An altercation is stated to have preceded the occurrence.",
        "The parties are said to have been known to each other previously.",
        "A quarrel over a long-standing dispute is alleged to have escalated.",
        "The injured was shifted to the nearest hospital for treatment.",
        "The medical officer's opinion has been sought on the nature of the injuries.",
        "There is alleged to have been prior enmity between the two sides.",
        "The accused are stated to have assembled and confronted the complainant party.",
        "Physical force was used during the course of the incident.",
        "The wound certificate has been called for from the treating hospital.",
    ],
    "property": [
        "The complainant noticed the loss on returning to the premises.",
        "Articles belonging to the complainant were removed without consent.",
        "The value of the property involved is yet to be assessed.",
        "The complainant was unable to identify the persons involved.",
        "The offenders are stated to have fled immediately after the act.",
        "A list of the missing articles has been furnished by the complainant.",
        "The approach and escape route are being examined.",
        "Local receivers of stolen property are being verified.",
        "The complainant has been asked to produce ownership documents.",
    ],
    "organised": [
        "The activity appears to have been carried out in an organised manner.",
        "More than one person is stated to have been involved in the operation.",
        "The persons involved appear to have operated to a set pattern.",
        "The source of the consignment is under verification.",
        "Financial transactions connected to the matter are being examined.",
        "A network operating across jurisdictions is suspected.",
        "Antecedents of the persons named are being verified from station records.",
        "The role of intermediaries is under enquiry.",
    ],
    "cyber": [
        "The transaction trail is being obtained from the concerned bank.",
        "The complainant did not meet the perpetrator in person at any stage.",
        "The contact was established remotely over a telecommunication channel.",
        "The beneficiary account details have been sought from the branch manager.",
        "A request has been sent to the platform for subscriber information.",
        "The complainant was induced to part with sensitive credentials.",
        "The amount was transferred in several tranches.",
    ],
}

# ---------------------------------------------------------------- class-leaning cues
# Drawn with probability CUE_P, so a narrative leans toward its class without
# ever declaring it. Deliberately worded in field language, not category language.
CUE = {
    "Chain Snatching": [
        "The ornament worn around the neck of the complainant was pulled away.",
        "Two persons on a two-wheeler came from behind and grabbed the article.",
        "The complainant was walking on the roadside when the article was pulled off.",
        "A gold item worn by the complainant was taken away in a moving act.",
        "The pillion rider reached out and snatched the article before the vehicle sped away.",
    ],
    "Vehicle Theft": [
        "The vehicle parked at the spot was not found in its place.",
        "The registration particulars of the missing vehicle have been circulated.",
        "The lock of the parked vehicle appears to have been tampered with.",
        "The complainant had parked the vehicle the previous evening and found it missing.",
        "The chassis and engine numbers have been furnished for circulation.",
    ],
    "House Break-in (Night)": [
        "The rear door of the premises was found broken open.",
        "The household articles were found scattered and the almirah forced.",
        "Entry appears to have been effected after breaking the latch.",
        "The occupants were away when the premises were entered.",
        "The window grill of the house was found bent.",
    ],
    "Assault": [
        "The complainant sustained injuries on the body.",
        "The accused is alleged to have beaten the complainant with hands and legs.",
        "Abusive language was used and the complainant was manhandled.",
        "The complainant was pushed down during the scuffle.",
    ],
    "Murder": [
        "The body was found at the spot and sent for post-mortem examination.",
        "The deceased was found lying with grievous injuries.",
        "An inquest was conducted over the body in the presence of panchas.",
        "The post-mortem report has been called for from the government hospital.",
    ],
    "Homicide": [
        "The victim succumbed to the injuries sustained during the occurrence.",
        "Death occurred subsequently during the course of treatment.",
        "The body has been handed over to the relatives after the formalities.",
        "The cause of death is awaited from the medical authorities.",
    ],
    "Extortion": [
        "Repeated demands for money were made under threat.",
        "The complainant was warned of consequences if the amount was not paid.",
        "A demand for a periodic payment was made at the business premises.",
        "Threatening calls were received on the complainant's mobile number.",
    ],
    "Narcotics Distribution": [
        "Contraband substance was recovered and sent for chemical examination.",
        "The material seized has been forwarded to the forensic science laboratory.",
        "The person was found in possession of a quantity of prohibited substance.",
        "A sample was drawn from the seized material as per procedure.",
    ],
    "Sandalwood Smuggling": [
        "Logs of scented timber were found loaded in the vehicle.",
        "The forest produce was being transported without a valid permit.",
        "The timber recovered has been handed over to the forest department.",
        "The consignment was intercepted while being moved from the fringe area.",
    ],
    "Cyber Fraud (Jamtara style)": [
        "The complainant received a call from an unknown number claiming to be from a bank.",
        "A one-time password was shared under a false pretext and the amount was debited.",
        "A link was sent over a messaging application and the account was compromised.",
        "The complainant was asked to update the account details and lost the amount.",
    ],
}

# Cross-class distractors: phrasing that belongs to a neighbouring offence but shows
# up occasionally, exactly as ambiguous real reports do.
DISTRACTOR_P = 0.18
CUE_P = 0.72          # probability a class-leaning cue is included at all
SPARSE_P = 0.16       # share of terse, low-information stubs
NOISE_P = 0.09        # share carrying transcription artefacts

WEAPON_LINE = [
    "A {weapon} is stated to have been used during the occurrence.",
    "The complainant states that a {weapon} was displayed.",
    "A {weapon} was allegedly carried by one of the persons involved.",
]
VEHICLE_LINE = [
    "A {vehicle} is stated to have been used to leave the spot.",
    "The persons involved are said to have arrived on a {vehicle}.",
    "A vehicle resembling a {vehicle} was noticed by a witness.",
]

TOD_WORDS = {
    "Morning (7AM-10AM)": "morning",
    "Afternoon (1PM-4PM)": "afternoon",
    "Evening (5PM-8PM)": "evening",
    "Late Night (1AM-4AM)": "late night",
}


def _noise(text, rng):
    """Transcription artefacts seen in station data entry."""
    ops = [
        lambda t: t.replace(" the ", " teh ", 1),
        lambda t: t.replace(".", " .", 1),
        lambda t: t.upper() if len(t) < 160 else t,
        lambda t: t.replace(", ", " , ", 1),
        lambda t: t + " (entry as recorded)",
        lambda t: t.replace("complainant", "complaintant", 1),
    ]
    return rng.choice(ops)(text)


def build_narrative(row, rng):
    ct = str(row["crime_type"])
    fam = FAMILY.get(ct, "property")
    station = str(row.get("police_station") or "the jurisdictional station")
    tod = TOD_WORDS.get(str(row.get("time_of_day")), "day")
    weapon = str(row.get("weapon_used") or "None")
    vehicle = str(row.get("vehicle_used") or "None")

    # --- terse stub: minimal information, genuinely hard to classify
    if rng.random() < SPARSE_P:
        parts = [rng.choice(OPEN_FRAMES).format(station=station)]
        if rng.random() < 0.55 and ct in CUE:
            parts.append(rng.choice(CUE[ct]))
        else:
            parts.append(rng.choice(FAMILY_POOL[fam]))
        parts.append(rng.choice(FOLLOWUP))
        text = " ".join(parts)
        return _noise(text, rng) if rng.random() < NOISE_P else text

    parts = [rng.choice(OPEN_FRAMES).format(station=station)]

    if rng.random() < 0.8:
        parts.append(rng.choice(TIME_FRAMES).format(tod=tod))

    # class-leaning cue, included only most of the time
    if ct in CUE and rng.random() < CUE_P:
        parts.append(rng.choice(CUE[ct]))

    # family context — shared with the confusable siblings
    parts.append(rng.choice(FAMILY_POOL[fam]))
    if rng.random() < 0.45:
        extra = rng.choice(FAMILY_POOL[fam])
        if extra not in parts:
            parts.append(extra)

    # a cue borrowed from a different offence — the ambiguity that keeps it honest
    if rng.random() < DISTRACTOR_P:
        other = rng.choice([c for c in CUE if c != ct])
        parts.append(rng.choice(CUE[other]))

    # Weapons and getaway vehicles are recorded for contact offences. A remote
    # fraud has neither, so quoting the randomly-assigned metadata there would
    # write nonsense into the corpus.
    if fam != "cyber":
        if weapon and weapon != "None" and rng.random() < 0.6:
            parts.append(rng.choice(WEAPON_LINE).format(weapon=weapon.lower()))
        if vehicle and vehicle != "None" and rng.random() < 0.5:
            parts.append(rng.choice(VEHICLE_LINE).format(vehicle=vehicle))

    fatalities = int(row.get("fatalities") or 0)
    injured = int(row.get("injured") or 0)
    if fatalities:
        parts.append(f"{fatalities} person(s) did not survive the incident.")
    elif injured:
        parts.append(f"{injured} person(s) sustained injuries and were treated.")

    parts.append(rng.choice(PROCEDURE))
    if rng.random() < 0.5:
        parts.append(rng.choice(PROCEDURE))

    wit = int(row.get("witnesses") or 0)
    if wit and rng.random() < 0.7:
        parts.append(rng.choice(WITNESS_LINE).format(n=wit))
    acc = int(row.get("accused_count") or 0)
    if acc and rng.random() < 0.7:
        parts.append(rng.choice(ACCUSED_LINE).format(n=acc))

    parts.append(rng.choice(FOLLOWUP))

    # de-duplicate while preserving order — repeated sentences are a giveaway
    seen, ordered = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    text = " ".join(ordered)
    return _noise(text, rng) if rng.random() < NOISE_P else text


METADATA_TMPL = ("[METADATA: SEV={sev};WPN={wpn};VEH={veh};TGT={tgt};"
                 "FAT={fat};INJ={inj};WIT={wit}]")


def main():
    ap = argparse.ArgumentParser(description="Regenerate the FIR narrative corpus.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print samples and statistics without writing any file")
    args = ap.parse_args()

    from ml import dataset

    rng = random.Random(SEED)
    bundle = dataset.load_canonical(DATA)
    cases = bundle["cases"]

    narratives = {}
    for _, r in cases.iterrows():
        narratives[r["case_key"]] = build_narrative(r, rng)

    # --- report corpus statistics so the effect of a change is visible immediately
    lens = pd.Series({k: len(v) for k, v in narratives.items()})
    print(f"Generated {len(narratives)} narratives.")
    print(f"  length: mean={lens.mean():.0f} min={lens.min()} max={lens.max()}")
    vocab = set()
    for v in narratives.values():
        vocab.update(v.lower().split())
    print(f"  vocabulary: {len(vocab)} distinct tokens")
    print("\nSamples:")
    for ct in ["Murder", "Homicide", "Chain Snatching", "Cyber Fraud (Jamtara style)"]:
        sub = cases[cases["crime_type"] == ct]
        if sub.empty:
            continue
        key = sub.iloc[0]["case_key"]
        print(f"\n  [{ct}]\n  {narratives[key]}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    # --- write case_reports.csv (narrative only, no metadata tag)
    reports = pd.read_csv(os.path.join(DATA, "case_reports.csv"))
    key_by_case_id = dict(zip(cases["case_id"], cases["case_key"]))
    reports["case_id"] = reports["case_id"].astype(str)
    reports["report_text"] = reports["case_id"].map(
        lambda cid: narratives.get(key_by_case_id.get(cid), ""))
    reports.to_csv(os.path.join(DATA, "case_reports.csv"), index=False)

    # --- rewrite CaseMaster.BriefFacts, preserving the structured tag the loader parses
    cm = pd.read_csv(os.path.join(DATA, "CaseMaster.csv"))
    meta_by_key = cases.set_index("case_key")[
        ["severity_score", "weapon_used", "vehicle_used", "target_type",
         "fatalities", "injured", "witnesses"]].to_dict("index")

    def _brief(key):
        m = meta_by_key.get(key)
        if m is None:
            return ""
        tag = METADATA_TMPL.format(sev=m["severity_score"], wpn=m["weapon_used"],
                                   veh=m["vehicle_used"], tgt=m["target_type"],
                                   fat=m["fatalities"], inj=m["injured"], wit=m["witnesses"])
        return f"{narratives.get(key, '')} {tag}"

    cm["BriefFacts"] = cm["CaseMasterID"].map(_brief)
    cm.to_csv(os.path.join(DATA, "CaseMaster.csv"), index=False)

    print(f"\nWrote data/case_reports.csv and data/CaseMaster.csv "
          f"({len(reports)} narratives, metadata tag preserved).")


if __name__ == "__main__":
    main()
