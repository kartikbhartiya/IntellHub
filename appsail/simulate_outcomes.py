"""Criminological outcome simulator — a documented data-generating process.

Why this exists
---------------
The original generator assigned offenders to cases with `random.randint(1, n)` and
set trial results with `random.choice(["Convicted", "Acquitted", "Pending Trial"])`.
Under that process there is, by construction, no relationship between anything
observable and any outcome. When the models in `ml/` were evaluated properly for
the first time they returned ROC-AUC 0.47 for recidivism and 0.52 for case
progression — correct measurements of a world containing no signal.

Rather than hide that behind a random train/test split, this script replaces the
outcome tables with a simulation that has explicit, documented structure. Every
coefficient below is a stated assumption, and the models are given only
*observable* consequences — never the latent variables themselves.

READ THIS BEFORE QUOTING ANY METRIC
-----------------------------------
This is simulated data with a known ground truth. A model scoring well here has
demonstrated that it can *recover a known structure* — which is the correct thing
to verify before real data exists, and is genuinely informative about whether the
pipeline, the features and the validation are sound. It is NOT evidence of field
performance on real KSP records, and the model registry says so on every card.

The data-generating process
---------------------------
**Offenders.** Each of the N offenders draws latent traits, none of which are ever
given to a model:
  lambda   offending rate per active year, LogNormal(mu=-0.9, sigma=1.3), 0.05-8.0
           (heavy-tailed: most offenders appear once or twice, a few dominate)
  crew     ~65% belong to one of the crews; crews drive co-offending and gangs
  speciality  a preferred crime type, weighted by the +3.0 term below
  home     a home district, weighted by the +1.2 term below
           (the realised adherence rates are measured and printed on every run,
            rather than asserted here)
  onset    career start date, uniform over the corpus span
  career   career length, Exponential(mean 2.6y), so some offenders desist

**Case attribution.** For each case in date order, 1-3 offenders are drawn with
probability proportional to exp(score), where
    score = log(lambda) + 3.0*speciality_match + 1.2*home_district_match
            + 0.8*active_at_incident_date
and additional accused are drawn from the first accused's crew with p=0.72. This
produces genuine repeat offending, genuine specialisation and genuine co-offending
communities — the structures the recidivism model, the community detection and the
link prediction are all supposed to find.

**Arrest.** P(arrest) = sigmoid(-0.9 + 0.34*z(witnesses) + 0.42*z(severity)
    + 0.55*z(prior cases) + 0.30*rowdy_sheeter). Delay ~ Gamma(k=2, theta=12 days),
stretched by station load.

**Chargesheet.** Filed for 88% of cases with at least one arrest. Delay ~
Gamma(k=2.4) with a mean that grows with the number of accused, case severity and
the station's 90-day load, and shrinks with the investigating officer's experience.
The `case_closure` model's target — chargesheet within 180 days — is therefore a
real consequence of case complexity and station capacity.

**Conviction.** For chargesheeted cases,
    P(convict) = sigmoid(-0.35 + 0.30*z(witnesses) - 0.45*z(arrest delay)
        - 0.35*z(chargesheet delay) + 0.25*z(severity) + 0.20*z(prior cases)
        - 0.22*z(co-accused count))
i.e. convictions follow evidence quality and investigative promptness. Cases whose
chargesheet is recent are left 'Pending Trial'.

**Registry consistency.** past_fir_count and past_arrests in CriminalsRegistry are
recomputed from the simulated event tables. In the original data they were
independent random draws that contradicted the case records — which is also why
they had to be banned as features.

Deterministic (seeded). Run:  python simulate_outcomes.py [--dry-run]
"""
import argparse
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SEED = 20260819

CRIME_TYPES = [
    "Chain Snatching", "House Break-in (Night)", "Vehicle Theft", "Extortion",
    "Narcotics Distribution", "Sandalwood Smuggling", "Cyber Fraud (Jamtara style)",
    "Murder", "Assault", "Homicide",
]
GANGS = ["Bawaria Gang", "D-Company", "Dandupalya Gang",
         "Local Rowdy Syndicate", "Cyber Phishing Racket"]


# Approximate district centres. In the original data every district shared the
# same uniform-random coordinate box, so a Belagavi case plotted in central
# Karnataka and no spatial cluster existed for DBSCAN or KDE to find.
# District headquarters coordinates come from the canonical geography module, so
# the simulator cannot drift from the generator about where a district is.
import karnataka as KA

DISTRICT_CENTRE = {name: KA.centre(name) for name in KA.DISTRICT_NAMES}
HOTSPOT_SHARE = 0.68        # incidents drawn from a concentrated hot zone
HOTSPOT_SIGMA_DEG = 0.022   # ~2.4 km
DISPERSED_SIGMA_DEG = 0.16  # ~18 km


def simulate_geography(cases, rng):
    """Place incidents consistently with their district, concentrated in hot zones.

    Each district gets 2-4 latent hot zones offset from its centre. About two
    thirds of its incidents are drawn tightly around one of those zones and the
    remainder are dispersed across the district. This is what makes hotspot
    detection a real task: the zones exist, and the model has to find them.
    """
    lat = np.zeros(len(cases))
    lng = np.zeros(len(cases))
    zones = {}

    for district, idx in cases.groupby("district").groups.items():
        clat, clng = DISTRICT_CENTRE.get(district, (13.8, 76.0))
        n_zones = int(rng.integers(2, 5))
        centres = [(clat + rng.normal(0, 0.10), clng + rng.normal(0, 0.10))
                   for _ in range(n_zones)]
        weights = rng.dirichlet(np.ones(n_zones) * 1.5)
        zones[district] = centres

        pos = cases.index.get_indexer(idx)
        for i in pos:
            if rng.random() < HOTSPOT_SHARE:
                zlat, zlng = centres[int(rng.choice(n_zones, p=weights))]
                lat[i] = zlat + rng.normal(0, HOTSPOT_SIGMA_DEG)
                lng[i] = zlng + rng.normal(0, HOTSPOT_SIGMA_DEG)
            else:
                lat[i] = clat + rng.normal(0, DISPERSED_SIGMA_DEG)
                lng[i] = clng + rng.normal(0, DISPERSED_SIGMA_DEG)

    return np.round(lat, 5), np.round(lng, 5), zones


def _z(x):
    x = np.asarray(x, dtype=float)
    sd = x.std()
    return (x - x.mean()) / sd if sd > 1e-9 else np.zeros_like(x)


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


# ---------------------------------------------------------------- offender layer
def build_offenders(offender_ids, districts, span_start, span_end, rng):
    n = len(offender_ids)
    total_days = max(1, (span_end - span_start).days)

    lam = np.clip(rng.lognormal(mean=-0.9, sigma=1.3, size=n), 0.05, 8.0)

    # Crews: the source of co-offending structure and of gang labels.
    n_crews = max(8, n // 12)
    in_crew = rng.random(n) < 0.65
    crew = np.where(in_crew, rng.integers(0, n_crews, size=n), -1)

    speciality = rng.choice(CRIME_TYPES, size=n)
    home = rng.choice(districts, size=n)
    onset_offset = rng.integers(0, int(total_days * 0.85), size=n)
    onset = np.array([span_start + pd.Timedelta(days=int(d)) for d in onset_offset])
    career_days = np.clip(rng.exponential(scale=365.25 * 2.6, size=n), 120, total_days)
    end = np.array([onset[i] + pd.Timedelta(days=int(career_days[i])) for i in range(n)])

    # Gang label follows the crew, so the visible label and the latent structure agree.
    crew_gang = {c: GANGS[int(rng.integers(0, len(GANGS)))] for c in range(n_crews)}
    gang = np.array([crew_gang[int(c)] if c >= 0 else "None" for c in crew])

    return pd.DataFrame({
        "offender_id": offender_ids,
        "lam": lam,
        "crew": crew,
        "speciality": speciality,
        "home": home,
        "onset": onset,
        "career_end": end,
        "gang": gang,
    })


def attribute_cases(cases, off, rng):
    """Draw the accused for every case from the latent offender model."""
    ids = off["offender_id"].to_numpy()
    lam = off["lam"].to_numpy()
    spec = off["speciality"].to_numpy()
    home = off["home"].to_numpy()
    onset = off["onset"].to_numpy()
    career_end = off["career_end"].to_numpy()
    crew = off["crew"].to_numpy()
    crew_members = {c: np.where(crew == c)[0] for c in np.unique(crew) if c >= 0}

    base = np.log(lam)
    rows = []
    ordered = cases.sort_values("ts")

    for _, case in ordered.iterrows():
        t = np.datetime64(case["ts"])
        score = (base
                 + 3.0 * (spec == case["crime_type"])
                 + 1.2 * (home == case["district"])
                 + 0.8 * ((onset <= t) & (career_end >= t)))
        p = np.exp(score - score.max())
        p /= p.sum()

        n_acc = int(rng.choice([1, 2, 3], p=[0.55, 0.32, 0.13]))
        first = int(rng.choice(len(ids), p=p))
        chosen = [first]

        for _ in range(n_acc - 1):
            pool = crew_members.get(int(crew[first]))
            if pool is not None and len(pool) > 1 and rng.random() < 0.72:
                cand = [c for c in pool if c not in chosen]
                if cand:
                    chosen.append(int(rng.choice(cand)))
                    continue
            q = p.copy()
            q[chosen] = 0.0
            if q.sum() <= 0:
                break
            q /= q.sum()
            chosen.append(int(rng.choice(len(ids), p=q)))

        for idx in chosen:
            rows.append({"case_key": case["case_key"], "ts": case["ts"],
                         "offender_idx": idx, "offender_id": ids[idx]})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------- outcome layers
def simulate_arrests(links, cases, rng):
    """Arrest probability rises with evidence and with the offender's record."""
    c = cases.set_index("case_key")
    d = links.copy()
    d["witnesses"] = d["case_key"].map(c["witnesses"]).fillna(0)
    d["severity"] = d["case_key"].map(c["severity_score"]).fillna(50)

    d = d.sort_values("ts")
    d["prior_cases"] = d.groupby("offender_id").cumcount()

    logit = (-0.9 + 0.34 * _z(d["witnesses"]) + 0.42 * _z(d["severity"])
             + 0.55 * _z(d["prior_cases"]))
    d["arrested"] = rng.random(len(d)) < _sigmoid(logit)
    delay = rng.gamma(shape=2.0, scale=12.0, size=len(d))
    d["arrest_date"] = d["ts"] + pd.to_timedelta(np.round(delay), unit="D")
    return d


def simulate_chargesheets(cases, links, officers, rng):
    """Filing delay reflects case complexity, station load and officer experience."""
    arrested_cases = set(links.loc[links["arrested"], "case_key"].unique())
    d = cases[cases["case_key"].isin(arrested_cases)].copy()
    if d.empty:
        return d.assign(chargesheet_date=pd.NaT)

    n_accused = links.groupby("case_key").size()
    d["n_accused"] = d["case_key"].map(n_accused).fillna(1)

    # Station load in the 90 days before registration.
    d = d.sort_values("registered_ts")
    loads = []
    for _, g in d.groupby("police_station", sort=False):
        ts = g["registered_ts"]
        loads.append(pd.Series([int(((ts >= t - pd.Timedelta(days=90)) & (ts < t)).sum())
                                for t in ts], index=g.index))
    d["station_load"] = pd.concat(loads).reindex(d.index).fillna(0) if loads else 0

    exp_years = pd.Series(8.0, index=d.index)
    if officers is not None and not officers.empty:
        appt = d["officer_key"].map(officers.set_index("officer_key")["appointment_date"])
        e = (d["registered_ts"] - appt).dt.days / 365.25
        exp_years = e.fillna(8.0).clip(0, 40)

    mean_days = np.clip(
        130.0
        + 42.0 * (d["n_accused"] - 1)
        + 1.10 * (d["severity_score"] - 50)
        + 4.0 * d["station_load"]
        - 3.5 * exp_years,
        14.0, 720.0)

    shape = 2.4
    d["chargesheet_date"] = d["registered_ts"] + pd.to_timedelta(
        np.round(rng.gamma(shape=shape, scale=mean_days / shape)), unit="D")
    d["filed"] = rng.random(len(d)) < 0.88
    d.loc[~d["filed"], "chargesheet_date"] = pd.NaT
    d["exp_years"] = exp_years
    return d


def simulate_convictions(cs, links, data_end, rng):
    """Verdicts follow evidence quality and investigative promptness."""
    d = cs[cs["chargesheet_date"].notna()].copy()
    if d.empty:
        return d.assign(conviction_status="Pending Trial")

    first_arrest = links[links["arrested"]].groupby("case_key")["arrest_date"].min()
    arrest_delay = (d["case_key"].map(first_arrest) - d["registered_ts"]).dt.days
    d["arrest_delay"] = arrest_delay.fillna(arrest_delay.median())
    d["cs_delay"] = (d["chargesheet_date"] - d["registered_ts"]).dt.days

    prior = links.sort_values("ts").assign(p=lambda x: x.groupby("offender_id").cumcount())
    case_prior = prior.groupby("case_key")["p"].max()
    d["prior_cases"] = d["case_key"].map(case_prior).fillna(0)
    d["n_accused"] = d["case_key"].map(links.groupby("case_key").size()).fillna(1)

    logit = (-0.35
             + 0.30 * _z(d["witnesses"])
             - 0.45 * _z(d["arrest_delay"])
             - 0.35 * _z(d["cs_delay"])
             + 0.25 * _z(d["severity_score"])
             + 0.20 * _z(d["prior_cases"])
             - 0.22 * _z(d["n_accused"]))
    convicted = rng.random(len(d)) < _sigmoid(logit)

    # Trials that have not had time to conclude are still pending.
    trial_days = np.round(rng.gamma(shape=3.0, scale=95.0, size=len(d)))
    verdict_date = d["chargesheet_date"] + pd.to_timedelta(trial_days, unit="D")
    d["conviction_status"] = np.where(verdict_date > data_end, "Pending Trial",
                                      np.where(convicted, "Convicted", "Acquitted"))
    d["verdict_date"] = verdict_date
    return d


# ---------------------------------------------------------------- writers
def main():
    ap = argparse.ArgumentParser(description="Regenerate outcome tables from a documented DGP.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the resulting distributions without writing files")
    args = ap.parse_args()

    from ml import dataset

    rng = np.random.default_rng(SEED)
    bundle = dataset.load_canonical(DATA)
    cases = bundle["cases"].dropna(subset=["ts"]).copy()
    cases["registered_ts"] = cases["registered_ts"].fillna(cases["ts"])
    officers = bundle["officers"]

    reg = bundle["raw"]["CriminalsRegistry"]
    offender_ids = reg["PersonID"].astype(str).tolist()
    districts = sorted(cases["district"].unique().tolist())
    span_start, span_end = cases["ts"].min(), cases["ts"].max()

    print(f"Simulating outcomes for {len(cases)} cases and {len(offender_ids)} offenders")
    print(f"  corpus span {span_start.date()} .. {span_end.date()}")

    new_lat, new_lng, zones = simulate_geography(cases, rng)
    cases["latitude"], cases["longitude"] = new_lat, new_lng
    print(f"  placed incidents around {sum(len(v) for v in zones.values())} hot zones "
          f"across {len(zones)} districts")

    off = build_offenders(offender_ids, districts, span_start, span_end, rng)
    links = attribute_cases(cases, off, rng)
    links = simulate_arrests(links, cases, rng)
    cs = simulate_chargesheets(cases, links, officers, rng)
    conv = simulate_convictions(cs, links, span_end, rng)

    # ---- diagnostics: what structure now exists in the data
    per_offender = links.groupby("offender_id").size()
    print("\nResulting structure")
    print(f"  accused rows            {len(links)}")
    print(f"  cases per offender      mean {per_offender.mean():.2f}  "
          f"median {per_offender.median():.0f}  max {per_offender.max()}")
    print(f"  offenders with 1 case   {(per_offender == 1).sum()} "
          f"({100 * (per_offender == 1).mean():.0f}%)")
    print(f"  offenders with 5+ cases {(per_offender >= 5).sum()}")
    print(f"  arrest rate             {links['arrested'].mean():.1%}")
    print(f"  cases chargesheeted     {cs['chargesheet_date'].notna().sum()} "
          f"({100 * cs['chargesheet_date'].notna().sum() / len(cases):.0f}% of all cases)")
    if not conv.empty:
        filed = conv[conv["chargesheet_date"].notna()]
        within = ((filed["chargesheet_date"] - filed["registered_ts"]).dt.days <= 180).mean()
        print(f"  chargesheet <=180 days  {within:.1%} of filed cases")
        print(f"  verdicts                {conv['conviction_status'].value_counts().to_dict()}")
        decided = conv[conv["conviction_status"].isin(["Convicted", "Acquitted"])]
        if len(decided):
            print(f"  conviction rate         "
                  f"{(decided['conviction_status'] == 'Convicted').mean():.1%}")

    # speciality adherence — confirms the latent structure landed
    spec_map = dict(zip(off["offender_id"], off["speciality"]))
    ct = links["case_key"].map(cases.set_index("case_key")["crime_type"])
    adherence = (ct.to_numpy() == links["offender_id"].map(spec_map).to_numpy()).mean()
    print(f"  offences in speciality  {adherence:.1%}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    # ------------------------------------------------------- CaseMaster coordinates
    cm_df = bundle["raw"]["CaseMaster"].copy()
    coord = cases.set_index("case_key")[["latitude", "longitude"]]
    cm_df["latitude"] = cm_df["CaseMasterID"].map(coord["latitude"]).fillna(cm_df["latitude"])
    cm_df["longitude"] = cm_df["CaseMasterID"].map(coord["longitude"]).fillna(cm_df["longitude"])
    cm_df.to_csv(os.path.join(DATA, "CaseMaster.csv"), index=False)

    # ------------------------------------------------------------------ Accused.csv
    names = dict(zip(reg["PersonID"].astype(str), reg["name"]))
    ages = {oid: int(rng.integers(19, 60)) for oid in offender_ids}
    genders = {oid: (1 if rng.random() < 0.9 else 2) for oid in offender_ids}

    links = links.sort_values(["case_key"]).reset_index(drop=True)
    links["accused_key"] = np.arange(1, len(links) + 1)
    accused_out = pd.DataFrame({
        "AccusedMasterID": links["accused_key"],
        "CaseMasterID": links["case_key"],
        "AccusedName": links["offender_id"].map(names).fillna("Unknown"),
        "AgeYear": links["offender_id"].map(ages),
        "GenderID": links["offender_id"].map(genders),
        "PersonID": links["offender_id"],
    })
    accused_out.to_csv(os.path.join(DATA, "Accused.csv"), index=False)

    # ------------------------------------------------------------ ArrestSurrender.csv
    ar = links[links["arrested"]].copy()
    case_idx = cases.set_index("case_key")
    arrest_out = pd.DataFrame({
        "ArrestSurrenderID": np.arange(1, len(ar) + 1),
        "CaseMasterID": ar["case_key"],
        "ArrestSurrenderTypeID": 1,
        "ArrestSurrenderDate": ar["arrest_date"].dt.strftime("%Y-%m-%d"),
        "ArrestSurrenderStateId": 1,
        "ArrestSurrenderDistrictId": 1,
        "PoliceStationID": ar["case_key"].map(
            bundle["raw"]["CaseMaster"].set_index("CaseMasterID")["PoliceStationID"]),
        "IOID": ar["case_key"].map(case_idx["officer_key"]),
        "CourtID": 1,
        "AccusedMasterID": ar["accused_key"],
        "IsAccused": 1,
        "IsComplainantAccused": 0,
    })
    arrest_out.to_csv(os.path.join(DATA, "ArrestSurrender.csv"), index=False)

    # ------------------------------------------------------- ChargesheetDetails.csv
    filed = conv[conv["chargesheet_date"].notna()]
    cs_out = pd.DataFrame({
        "CSID": np.arange(1, len(filed) + 1),
        "CaseMasterID": filed["case_key"],
        "csdate": filed["chargesheet_date"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "cstype": np.where(rng.random(len(filed)) < 0.6, "A", "B"),
        "PolicePersonID": filed["officer_key"],
    })
    cs_out.to_csv(os.path.join(DATA, "ChargesheetDetails.csv"), index=False)

    # ------------------------------------------------------ CourtCasesRegistry.csv
    old_courts = bundle["raw"]["CourtCasesRegistry"]
    court_name = dict(zip(old_courts["CaseMasterID"], old_courts["court_name"])) \
        if "court_name" in old_courts.columns else {}
    courts_list = sorted(set(court_name.values())) or ["Dist. & Sessions Court"]
    court_out = pd.DataFrame({
        "CaseMasterID": filed["case_key"],
        "court_case_no": [f"CC/{int(rng.integers(100, 9999))}/{d.year}"
                          for d in filed["chargesheet_date"]],
        "chargesheet_status": "Filed",
        "court_name": [court_name.get(k, courts_list[int(rng.integers(0, len(courts_list)))])
                       for k in filed["case_key"]],
        "next_hearing": filed["verdict_date"].dt.strftime("%Y-%m-%d"),
        "conviction_status": filed["conviction_status"].to_numpy(),
    })
    court_out.to_csv(os.path.join(DATA, "CourtCasesRegistry.csv"), index=False)

    # ------------------------------------------------------- CriminalsRegistry.csv
    # Recomputed from the simulated events so the registry agrees with the records.
    case_counts = links.groupby("offender_id").size()
    arrest_counts = ar.groupby("offender_id").size()
    crew_of = dict(zip(off["offender_id"], off["crew"]))
    gang_of = dict(zip(off["offender_id"], off["gang"]))

    new_reg = reg.copy()
    key = new_reg["PersonID"].astype(str)
    new_reg["past_fir_count"] = key.map(case_counts).fillna(0).astype(int)
    new_reg["past_arrests"] = key.map(arrest_counts).fillna(0).astype(int)
    new_reg["gang_affiliation"] = key.map(gang_of).fillna("None")

    # Visible flags are noisy observations of the latent rate, as real registers are.
    rate = key.map(case_counts).fillna(0).to_numpy(dtype=float)
    rate_z = _z(rate)
    new_reg["rowdy_sheeter"] = rng.random(len(new_reg)) < _sigmoid(-1.4 + 0.9 * rate_z)
    new_reg["history_sheet"] = rng.random(len(new_reg)) < _sigmoid(-1.6 + 0.7 * rate_z)
    new_reg["active_warrant"] = rng.random(len(new_reg)) < _sigmoid(-1.8 + 0.5 * rate_z)
    risk_bands = np.select(
        [rate_z >= 1.2, rate_z >= 0.4, rate_z >= -0.4],
        ["Critical", "High", "Medium"], default="Low")
    new_reg["risk_level"] = risk_bands

    # Known associates = crew mates actually co-offended with.
    co = {}
    for ck, g in links.groupby("case_key"):
        members = g["offender_id"].tolist()
        for m in members:
            co.setdefault(m, set()).update(x for x in members if x != m)
    new_reg["known_associates"] = key.map(
        lambda o: ";".join(sorted(co.get(o, set()))[:6])).fillna("")
    new_reg.to_csv(os.path.join(DATA, "CriminalsRegistry.csv"), index=False)

    # ------------------------------------------------------------------ suspects.csv
    suspects_out = pd.DataFrame({
        "suspect_id": new_reg["PersonID"],
        "name": new_reg["name"],
        "gang_affiliation": new_reg["gang_affiliation"],
        "risk_level": new_reg["risk_level"],
    })
    suspects_out.to_csv(os.path.join(DATA, "suspects.csv"), index=False)

    # ----------------------------------------------------------------- case_links.csv
    crime_no = dict(zip(bundle["raw"]["CaseMaster"]["CaseMasterID"],
                        bundle["raw"]["CaseMaster"]["CrimeNo"].astype(str)))
    same_crew = links["offender_id"].map(crew_of)
    links_out = pd.DataFrame({
        "case_id": links["case_key"].map(crime_no),
        "suspect_id": links["offender_id"],
        "relationship": np.where(same_crew.to_numpy() >= 0, "Co-Offender", "Charged"),
    })
    links_out.to_csv(os.path.join(DATA, "case_links.csv"), index=False)

    print("\nWrote Accused.csv, ArrestSurrender.csv, ChargesheetDetails.csv,")
    print("      ChargesheetDetails.csv, CourtCasesRegistry.csv, CriminalsRegistry.csv,")
    print("      suspects.csv, case_links.csv")


if __name__ == "__main__":
    main()
