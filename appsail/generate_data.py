"""
KSP IntelHub — CCTNS-style synthetic data generator.

Augments the existing FIR table and produces an interconnected web of records
(criminals, officers, stolen vehicles, CCTV, complaints, cybercrime trails,
missing persons, court cases) so the knowledge graph and AI can correlate
across entities the way a real Crime & Criminal Tracking system does.

Deterministic (seeded) so results are reproducible. Run:  python generate_data.py
"""
import os
import random
import pandas as pd

random.seed(42)
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ---------------------------------------------------------------- reference data
RTO = {  # district -> plausible KA RTO codes
    "Bengaluru Urban": ["01", "02", "03", "05", "41", "51"],
    "Mysuru": ["09", "55"], "Mangaluru": ["19"], "Dakshina Kannada": ["19", "21"],
    "Udupi": ["20"], "Belagavi": ["22", "49"], "Hubballi-Dharwad": ["25"],
    "Ballari": ["34"], "Shivamogga": ["14"], "Tumakuru": ["06"],
}
STATIONS = {
    "Bengaluru Urban": ["Cubbon Park PS", "Koramangala PS", "Whitefield PS", "Yeshwanthpur PS",
                        "Electronic City PS", "Jayanagar PS", "Indiranagar PS", "KR Puram PS"],
    "Mysuru": ["Devaraja PS", "Lashkar PS", "Vijayanagar PS"],
    "Mangaluru": ["Mangaluru North PS", "Pandeshwar PS"], "Dakshina Kannada": ["Bantwal PS", "Puttur PS"],
    "Udupi": ["Udupi Town PS", "Malpe PS"], "Belagavi": ["Camp PS", "Tilakwadi PS"],
    "Hubballi-Dharwad": ["Vidyanagar PS", "Dharwad Town PS"], "Ballari": ["Cowl Bazaar PS", "Gandhi Nagar PS"],
    "Shivamogga": ["Doddapet PS", "Vinoba Nagar PS"], "Tumakuru": ["Kyathsandra PS", "Town PS"],
}
SECTIONS = {  # crime_type -> (IPC, BNS) section labels
    "Chain Snatching": "IPC 379/356 · BNS 304(2)/303",
    "House Break-in (Night)": "IPC 457/380 · BNS 331(4)/305",
    "Vehicle Theft": "IPC 379 · BNS 303(2)",
    "Extortion": "IPC 384/386 · BNS 308(2)",
    "Narcotics Distribution": "NDPS Act 20(b)/22",
    "Sandalwood Smuggling": "Karnataka Forest Act 86 · IPC 379",
    "Cyber Fraud (Jamtara style)": "IT Act 66C/66D · IPC 420 · BNS 318(4)",
}
CYBER_TYPE = "Cyber Fraud (Jamtara style)"
THEFT_TYPES = {"Vehicle Theft", "Chain Snatching", "House Break-in (Night)"}
RANKS = ["PC", "HC", "ASI", "PSI", "SI", "PI (Inspector)", "DySP"]
SPECIALIZATIONS = ["Property Crime", "Organized Crime", "Cybercrime", "Narcotics",
                   "Homicide", "Forest Offences", "Economic Offences"]
FIRST = ["Ravi", "Suresh", "Manjunath", "Lakshmi", "Anitha", "Vasanth", "Girish", "Deepa",
         "Prakash", "Naveen", "Bhavana", "Shankar", "Kiran", "Pooja", "Harish", "Sunil",
         "Rekha", "Mahesh", "Nagaraj", "Divya", "Imran", "Fatima", "Joseph", "Mary"]
LAST = ["Gowda", "Reddy", "Shetty", "Hegde", "Naik", "Patil", "Rao", "Kumar", "Murthy",
        "Achar", "Bhat", "Kulkarni", "Iyer", "Pujari", "Desai", "Swamy"]
BANKS = ["SBI", "Canara", "Karnataka Bank", "HDFC", "ICICI", "Union Bank", "Axis"]
COURTS = ["I Addl. CMM Court Bengaluru", "II ACMM Bengaluru", "Dist. & Sessions Court Mysuru",
          "JMFC Mangaluru", "Sessions Court Belagavi", "Spl. Court for NDPS Cases",
          "Dist. Court Ballari", "JMFC Tumakuru"]


def name():
    return f"{random.choice(FIRST)} {random.choice(LAST)}"


def phone():
    return "9" + "".join(random.choice("0123456789") for _ in range(9))


def main():
    firs = pd.read_csv(os.path.join(DATA, "firs.csv"))
    suspects = pd.read_csv(os.path.join(DATA, "suspects.csv"))
    links = pd.read_csv(os.path.join(DATA, "case_links.csv"))

    # Idempotency: if a previous run already enriched firs.csv, drop the added
    # columns so we rebuild cleanly instead of duplicating them.
    added_cols = ["police_station", "ipc_bns_sections", "victim_name", "victim_age",
                  "victim_gender", "officer_id", "witnesses", "fatalities", "injured",
                  "accused_count"]
    firs = firs.drop(columns=[c for c in added_cols if c in firs.columns])

    # ---------------------------------------------------------- officers
    officers = []
    for d, sts in STATIONS.items():
        for st in sts:
            for _ in range(random.randint(2, 4)):
                oid = f"OFF-{random.randint(100, 999)}-{len(officers)}"
                officers.append({
                    "officer_id": oid, "name": name(), "rank": random.choice(RANKS),
                    "station": st, "district": d,
                    "specialization": random.choice(SPECIALIZATIONS),
                })
    off_df = pd.DataFrame(officers)
    off_ids = off_df["officer_id"].tolist()

    # ---------------------------------------------------------- enrich FIRs
    def station_for(d):
        return random.choice(STATIONS.get(d, ["District HQ PS"]))

    enrich = []
    for _, r in firs.iterrows():
        ct = r["crime_type"]
        sev = int(r["severity_score"])
        fatal = 0
        injured = 0
        if ct in ("Extortion", "Narcotics Distribution"):
            if sev > 88:
                fatal = random.choices([1, 2, 3, 4], weights=[4, 3, 2, 1])[0]
            elif sev > 70:
                fatal = random.choices([0, 1, 2], weights=[5, 3, 2])[0]
        elif ct in ("House Break-in (Night)", "Chain Snatching") and sev > 85:
            fatal = random.choices([0, 1, 2], weights=[6, 3, 1])[0]
        if ct in ("House Break-in (Night)", "Chain Snatching", "Extortion", "Narcotics Distribution"):
            injured = (random.choices([0, 1, 2, 3, 4, 5], weights=[4, 3, 2, 2, 1, 1])[0]
                       if sev > 45 else random.choice([0, 0, 1]))
        enrich.append({
            "police_station": station_for(r["district"]),
            "ipc_bns_sections": SECTIONS.get(ct, "IPC 34"),
            "victim_name": name(),
            "victim_age": random.randint(18, 78),
            "victim_gender": random.choice(["M", "F"]),
            "officer_id": random.choice(off_ids),
            "witnesses": random.randint(0, 4),
            "fatalities": fatal,
            "injured": injured,
        })
    fe = pd.DataFrame(enrich)
    # accused count from the link table
    acc = links.groupby("case_id").size().rename("accused_count")
    firs = pd.concat([firs.reset_index(drop=True), fe], axis=1)
    firs = firs.merge(acc, on="case_id", how="left")
    firs["accused_count"] = firs["accused_count"].fillna(0).astype(int)
    firs.to_csv(os.path.join(DATA, "firs.csv"), index=False)

    case_ids = firs["case_id"].tolist()
    coords = {r["case_id"]: (r["latitude"], r["longitude"], r["district"], r["timestamp"])
              for _, r in firs.iterrows()}

    # officer caseload
    load = firs.groupby("officer_id").size().rename("cases_assigned")
    off_df = off_df.merge(load, left_on="officer_id", right_index=True, how="left")
    off_df["cases_assigned"] = off_df["cases_assigned"].fillna(0).astype(int)
    off_df["closure_rate"] = [round(random.uniform(0.32, 0.86), 2) for _ in range(len(off_df))]
    off_df["commendations"] = [random.randint(0, 9) for _ in range(len(off_df))]
    off_df.to_csv(os.path.join(DATA, "officers.csv"), index=False)

    # ---------------------------------------------------------- criminal history (extends suspects)
    case_susp = links.groupby("case_id")["suspect_id"].apply(list).to_dict()
    susp_cases = links.groupby("suspect_id")["case_id"].apply(list).to_dict()
    crim = []
    for _, s in suspects.iterrows():
        sid = s["suspect_id"]
        mycases = susp_cases.get(sid, [])
        associates = set()
        for c in mycases:
            for other in case_susp.get(c, []):
                if other != sid:
                    associates.add(other)
        past = len(mycases)
        crim.append({
            "criminal_id": sid, "name": s["name"],
            "alias": random.choice(["", "", f"'{random.choice(['Anna','Bhai','Don','Chinna','Lambu','Kala'])}'"]),
            "gang_affiliation": s["gang_affiliation"], "risk_level": s["risk_level"],
            "fingerprint_id": f"FP-{random.randint(100000,999999)}",
            "photo_url": f"https://i.pravatar.cc/120?u={sid}",
            "past_fir_count": past,
            "past_arrests": max(0, past - random.randint(0, 1)),
            "known_associates": ";".join(sorted(associates)[:6]),
            "rowdy_sheeter": past >= 4 or s["risk_level"] in ("Critical", "High"),
            "history_sheet": past >= 6,
            "active_warrant": random.random() < 0.18,
        })
    pd.DataFrame(crim).to_csv(os.path.join(DATA, "criminals.csv"), index=False)

    # ---------------------------------------------------------- stolen vehicles
    veh = []
    theft_cases = [c for c in case_ids if coords[c] and firs.set_index("case_id").loc[c, "crime_type"] in THEFT_TYPES]
    fmap = firs.set_index("case_id")
    for i in range(700):
        linked = random.choice(theft_cases) if (theft_cases and random.random() < 0.55) else ""
        if linked:
            lat, lng, dist, ts = coords[linked]
        else:
            base = random.choice(list(RTO.keys()))
            row = fmap[fmap["district"] == base].sample(1).iloc[0]
            lat, lng, dist, ts = row["latitude"], row["longitude"], base, row["timestamp"]
        code = random.choice(RTO.get(dist, ["01"]))
        vnum = f"KA-{code}-{random.choice('ABCDEFGHJKLMNPQR')}{random.choice('ABCDEFGHJ')}-{random.randint(1000,9999)}"
        rec = random.random() < 0.41
        veh.append({
            "vehicle_number": vnum, "chassis_number": f"MD{random.randint(10**13,10**14-1)}",
            "make_model": random.choice(["Bajaj Pulsar", "Honda Activa", "TVS Apache", "Maruti Omni",
                                          "Hyundai Creta", "Royal Enfield", "Toyota Innova"]),
            "owner_name": name(), "owner_phone": phone(),
            "theft_district": dist, "lat": round(float(lat), 5), "lng": round(float(lng), 5),
            "theft_date": str(ts)[:10],
            "recovery_status": "Recovered" if rec else "Not Recovered",
            "linked_case_id": linked,
        })
    veh_df = pd.DataFrame(veh)
    veh_df.to_csv(os.path.join(DATA, "stolen_vehicles.csv"), index=False)

    # ---------------------------------------------------------- CCTV / evidence
    cctv = []
    for i in range(500):
        c = random.choice(case_ids) if random.random() < 0.7 else ""
        if c:
            lat, lng, dist, ts = coords[c]
            faces = ";".join(random.sample(case_susp.get(c, ["SUS-" + str(random.randint(1000, 1799))]),
                                           k=min(2, len(case_susp.get(c, [1])))) or [])
        else:
            row = firs.sample(1).iloc[0]
            lat, lng, dist, ts = row["latitude"], row["longitude"], row["district"], row["timestamp"]
            faces = ""
        cctv.append({
            "camera_id": f"CAM-{dist[:3].upper()}-{1000+i}", "district": dist,
            "lat": round(float(lat) + random.uniform(-0.01, 0.01), 5),
            "lng": round(float(lng) + random.uniform(-0.01, 0.01), 5),
            "timestamp": str(ts), "video_url": f"https://evidence.ksp.gov.in/clip/{10000+i}.mp4",
            "detected_faces": faces,
            "detected_vehicle": random.choice(veh_df["vehicle_number"].tolist()) if random.random() < 0.4 else "",
            "case_link": c,
        })
    pd.DataFrame(cctv).to_csv(os.path.join(DATA, "cctv.csv"), index=False)

    # ---------------------------------------------------------- complaints / call records
    comp = []
    etypes = ["Theft", "Assault", "Domestic Dispute", "Cyber Fraud", "Missing Person",
              "Public Nuisance", "Accident", "Suspicious Activity"]
    for i in range(1600):
        linked = random.choice(case_ids) if random.random() < 0.35 else ""
        dist = coords[linked][2] if linked else random.choice(list(RTO.keys()))
        comp.append({
            "complaint_id": f"DIAL112-{200000+i}", "caller_phone": phone(),
            "district": dist, "emergency_type": random.choice(etypes),
            "response_time_min": random.randint(4, 55),
            "assigned_officer": random.choice(off_ids),
            "resolution_status": random.choice(["Resolved", "Resolved", "Resolved", "Pending", "False Alarm"]),
            "linked_case_id": linked,
        })
    pd.DataFrame(comp).to_csv(os.path.join(DATA, "complaints.csv"), index=False)

    # ---------------------------------------------------------- cybercrime trails (with shared-identifier RINGS)
    cyber_cases = firs[firs["crime_type"] == CYBER_TYPE]["case_id"].tolist()
    # shared pools so multiple cases trace back to the same handlers -> fraud rings
    ring_phones = [phone() for _ in range(max(6, len(cyber_cases) // 12))]
    ring_upis = [f"{random.choice(FIRST).lower()}{random.randint(10,99)}@{random.choice(['oksbi','okhdfc','paytm','ybl'])}"
                 for _ in range(max(6, len(cyber_cases) // 10))]
    ring_accts = [f"{random.choice(BANKS)}-{random.randint(10**10,10**11-1)}" for _ in range(max(6, len(cyber_cases) // 14))]
    cyber = []
    for c in cyber_cases:
        cyber.append({
            "case_id": c,
            "upi_ids": ";".join(random.sample(ring_upis, k=random.randint(1, 3))),
            "bank_accounts": ";".join(random.sample(ring_accts, k=random.randint(1, 2))),
            "phone_numbers": ";".join(random.sample(ring_phones, k=random.randint(1, 3))),
            "ip_addresses": ";".join(f"{random.randint(49,123)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
                                     for _ in range(random.randint(1, 2))),
            "urls": random.choice(["kyc-update-sbi.in", "free-prize.xyz", "rbi-refund.help", "fastag-renew.co"]),
            "crypto_wallet": random.choice(["", "", f"0x{random.randint(10**15,10**16-1):x}"]),
            "fraud_amount": random.choice([15000, 48000, 125000, 250000, 67000, 890000, 33000]),
            "victim_count": random.randint(1, 12),
        })
    pd.DataFrame(cyber).to_csv(os.path.join(DATA, "cybercrime.csv"), index=False)

    # ---------------------------------------------------------- missing persons
    miss = []
    for i in range(320):
        dist = random.choice(list(RTO.keys()))
        row = fmap[fmap["district"] == dist].sample(1).iloc[0]
        status = random.choices(["Missing", "Found", "Deceased"], weights=[0.45, 0.45, 0.10])[0]
        miss.append({
            "person_id": f"MP-{5000+i}", "name": name(), "age": random.randint(4, 82),
            "gender": random.choice(["M", "F"]),
            "photo_url": f"https://i.pravatar.cc/120?u=MP{i}",
            "last_seen_district": dist,
            "lat": round(float(row["latitude"]), 5), "lng": round(float(row["longitude"]), 5),
            "date_missing": str(row["timestamp"])[:10],
            "family_contact": phone(), "found_status": status,
        })
    pd.DataFrame(miss).to_csv(os.path.join(DATA, "missing_persons.csv"), index=False)

    # ---------------------------------------------------------- court / chargesheet
    court = []
    charged = firs[firs["status"] != "Open"]["case_id"].tolist()
    for i, c in enumerate(charged):
        cs = random.choices(["Filed", "Pending", "Filed", "Not Filed"], weights=[0.5, 0.25, 0.15, 0.10])[0]
        conv = "Pending Trial"
        if cs == "Filed":
            conv = random.choices(["Convicted", "Acquitted", "Pending Trial"], weights=[0.42, 0.23, 0.35])[0]
        court.append({
            "court_case_no": f"CC/{random.randint(100,9999)}/{random.choice([2023,2024,2025])}",
            "fir_number": c, "chargesheet_status": cs,
            "court_name": random.choice(COURTS),
            "next_hearing": f"2026-{random.randint(6,12):02d}-{random.randint(1,28):02d}",
            "conviction_status": conv,
        })
    pd.DataFrame(court).to_csv(os.path.join(DATA, "court_cases.csv"), index=False)

    print("Generated / augmented:")
    for f in ["firs", "criminals", "officers", "stolen_vehicles", "cctv", "complaints",
              "cybercrime", "missing_persons", "court_cases"]:
        df = pd.read_csv(os.path.join(DATA, f + ".csv"))
        print(f"  {f:18s} {len(df):5d} rows  · cols: {len(df.columns)}")


if __name__ == "__main__":
    main()
