import os
import random
import pandas as pd
import numpy as np

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DATA_DIR, "data")

random.seed(42)
np.random.seed(42)

# Geography comes from the canonical module so the generator, the outcome
# simulator and the socio-economic overlay can never disagree about what a
# district is. See karnataka.py for what is authoritative vs. approximate.
import karnataka as KA

DISTRICTS = KA.DISTRICT_NAMES
STATIONS = KA.STATIONS
DISTRICT_WEIGHTS = [KA.population_weights()[d] for d in DISTRICTS]

CRIME_TYPES = ["Chain Snatching", "House Break-in (Night)", "Vehicle Theft", "Extortion", "Narcotics Distribution", "Sandalwood Smuggling", "Cyber Fraud (Jamtara style)", "Murder", "Assault", "Homicide"]
WEAPONS = ["Knife", "Crowbar", "Pistol", "Stick", "Iron Rod", "Sickle", "None"]
VEHICLES = ["Bajaj Pulsar", "Honda Activa", "TVS Apache", "Maruti Omni", "Hyundai Creta", "Royal Enfield", "None"]
TARGETS = ["Solitary Elderly", "Residential", "Commercial Establishment", "Transit Hub", "Public Area"]
RANKS = ["PC", "HC", "ASI", "PSI", "SI", "PI (Inspector)", "DySP"]
SPECIALIZATIONS = ["Property Crime", "Organized Crime", "Cybercrime", "Narcotics", "Homicide", "Forest Offences", "Economic Offences"]
FIRST_NAMES = ["Ravi", "Suresh", "Manjunath", "Lakshmi", "Anitha", "Vasanth", "Girish", "Deepa", "Prakash", "Naveen", "Bhavana", "Shankar", "Kiran", "Pooja", "Harish", "Sunil", "Rekha", "Mahesh", "Nagaraj", "Divya", "Imran", "Fatima", "Joseph", "Mary"]
LAST_NAMES = ["Gowda", "Reddy", "Shetty", "Hegde", "Naik", "Patil", "Rao", "Kumar", "Murthy", "Achar", "Bhat", "Kulkarni", "Iyer", "Pujari", "Desai", "Swamy"]
COURTS = ["I Addl. CMM Court Bengaluru", "II ACMM Bengaluru", "Dist. & Sessions Court Mysuru", "JMFC Mangaluru", "Sessions Court Belagavi", "Spl. Court for NDPS Cases", "Dist. Court Ballari", "JMFC Tumakuru"]

GANGS = ["Bawaria Gang", "D-Company", "Dandupalya Gang", "Local Rowdy Syndicate", "Cyber Phishing Racket", "None"]

def get_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

def get_phone():
    return "9" + "".join(random.choice("0123456789") for _ in range(9))

OPENERS = {
    "Chain Snatching": [
        "A daylight chain-snatching was reported when {victim}, aged {age}, was accosted while walking.",
        "The complainant {victim} lost a gold mangalsutra to two-wheeler-borne snatchers.",
        "A gold-chain robbery occurred as the victim {victim} returned from the market.",
    ],
    "Extortion": [
        "An extortion complaint was lodged by {victim} after repeated threatening calls demanding protection money.",
        "The proprietor {victim} reported being coerced to pay hafta under threat of violence.",
        "A businessman, {victim}, alleged extortion by a local syndicate demanding a monthly cut.",
    ],
    "Narcotics Distribution": [
        "A narcotics-peddling network was detected following a tip-off near {district}.",
        "Contraband substances were seized and {victim} named as a distribution link.",
        "An anti-narcotics action uncovered a supply chain operating out of {district}.",
    ],
    "Vehicle Theft": [
        "A motor-vehicle theft was reported after {victim} found the parking bay empty.",
        "The registered owner {victim} reported the vehicle stolen overnight.",
        "An organised vehicle-lifting incident was reported from {district}.",
    ],
    "House Break-in (Night)": [
        "A nocturnal house-breaking was reported after {victim} discovered a forced rear entry.",
        "Burglars broke into the residence of {victim} while the family was away.",
        "A night burglary was reported when {victim} awoke to find valuables missing.",
    ],
    "Sandalwood Smuggling": [
        "A sandalwood-smuggling consignment was intercepted en route from a forest fringe.",
        "Forest offenders felled and transported red sandalwood logs, {victim} named as carrier.",
        "An illegal sandalwood haul was traced to a racket moving timber out of {district}.",
    ],
    "Cyber Fraud (Jamtara style)": [
        "A cyber-fraud complaint was filed after {victim} was deceived over the phone by fake agents.",
        "The victim {victim} lost money to a digital-arrest / KYC-update scam call.",
        "An online financial fraud was reported when {victim}'s account was drained after a phishing call.",
    ],
    "Murder": [
        "A homicide investigation was launched after the body of {victim}, aged {age}, was discovered.",
        "The police registered a murder case following the fatal assault on {victim}.",
        "A fatal attack resulted in the death of {victim} at {district}."
    ],
    "Assault": [
        "A physical altercation was reported where {victim}, aged {age}, was severely assaulted.",
        "The victim {victim} suffered physical injuries after being attacked by a group.",
        "An assault case was registered after {victim} was confronted and beaten."
    ],
    "Homicide": [
        "A culpable homicide case was registered following the death of {victim}, aged {age}.",
        "The police are investigating the suspicious death of {victim} at {district}.",
        "A fatal scuffle resulted in the homicide of {victim}."
    ]
}

MODUS = {
    "Chain Snatching": [
        "The accused rode a {vehicle}, snatched the ornament near a crowded junction and sped off without a number plate.",
        "Two pillion-riders on a {vehicle} grabbed the chain and vanished into a bylane before bystanders could react.",
        "Weapon displayed: {weapon}. The snatch was executed in seconds and the duo escaped against traffic.",
    ],
    "Extortion": [
        "Calls were traced to burner SIMs; the caller invoked a gang name and threatened the family with {weapon}.",
        "A collector visited the shop demanding cash, brandishing {weapon} and warning of consequences.",
        "The threats escalated from calls to a physical visit, with a {vehicle} seen circling the premises.",
    ],
    "Narcotics Distribution": [
        "Recovered material was sent for FSL analysis; peddlers used a {vehicle} and coded calls to move consignments.",
        "The network operated a hand-to-hand supply near educational hubs, using {vehicle} for last-mile drops.",
        "Intelligence indicates the racket sources from an interstate corridor and distributes via micro-dealers.",
    ],
    "Vehicle Theft": [
        "The lock was tampered and the vehicle hot-wired; CCTV shows it driven off toward {district}.",
        "A key-cloning method was suspected; the {vehicle} was later flagged near a chop-shop cluster.",
        "The stolen vehicle's plates were swapped within hours, a signature of an organised lifting gang.",
    ],
    "House Break-in (Night)": [
        "Entry was gained via a broken window grille; cash, gold and a {vehicle} key were taken while occupants slept.",
        "The intruders disabled the latch, ransacked the bedroom and fled on foot before dawn.",
        "Tool marks on the door and a discarded {weapon} suggest a practised burglary crew.",
    ],
    "Sandalwood Smuggling": [
        "The logs were concealed under farm produce in a {vehicle}; forest checkpost staff flagged the load.",
        "GPS trails place the consignment moving at night through forest-fringe villages toward a transit depot.",
        "The racket coordinates fellers, carriers and a receiver, evading patrols using decoy vehicles.",
    ],
    "Cyber Fraud (Jamtara style)": [
        "The fraudsters spoofed a courier/bank line, pushed a screen-sharing APK and initiated UPI debits worth several lakhs.",
        "Money moved through a chain of mule accounts and UPI handles before withdrawal at distant ATMs.",
        "The caller impersonated an official, induced panic over a fake parcel/warrant and harvested OTPs and card data.",
    ],
    "Murder": [
        "The suspect used a {weapon} to inflict fatal injuries and fled the scene in a {vehicle}.",
        "Preliminary forensic analysis indicates the victim was attacked with a {weapon} during the {tod} hours.",
        "CCTV footage shows a suspect fleeing the crime scene on a {vehicle} shortly after the attack."
    ],
    "Assault": [
        "The assailants brandished a {weapon} and struck the victim before escaping on a {vehicle}.",
        "The dispute escalated, resulting in the suspect using a {weapon} to cause bodily harm.",
        "A street brawl broke out during which the victim was threatened with a {weapon}."
    ],
    "Homicide": [
        "The confrontation turned violent as the suspect used a {weapon} to inflict fatal blows.",
        "Witnesses reported a heated argument that ended with the suspect brandishing a {weapon}.",
        "The victim was found unresponsive with marks indicating an assault with a {weapon}."
    ]
}

TARGET_LINE = {
    "Solitary Elderly": "The victim profile — a solitary elderly resident — points to deliberate target selection.",
    "Forest Outpost": "The location near a forest outpost is consistent with the offence pattern.",
    "Commercial Establishment": "A commercial establishment was targeted during business hours.",
    "Residential": "A residential premises was targeted.",
    "Transit Hub": "The offence occurred around a transit hub with high footfall.",
    "Public Area": "The incident took place in a public area."
}

def _casualty_clause(fatal, injured):
    if fatal and injured:
        return f" The incident turned violent — {int(fatal)} died and {int(injured)} were injured."
    if fatal:
        return f" The incident was fatal, claiming {int(fatal)} life(s)."
    if injured:
        return f" {int(injured)} person(s) sustained injuries."
    return ""

def _sev_clause(sev):
    if sev >= 85:
        return " Given the gravity, the case is graded high-severity and escalated to a special team."
    if sev >= 60:
        return " The case carries a moderate-to-serious severity grade."
    return ""

def build_narrative(r):
    ct = r["crime_type"]
    victim = str(r.get("victim_name") or "the complainant")
    age = int(r.get("victim_age") or random.randint(20, 70))
    weapon = str(r.get("weapon_used") or "None")
    weapon = weapon if weapon and weapon != "None" else "no weapon"
    vehicle = str(r.get("vehicle_used") or "vehicle")
    vehicle = vehicle if vehicle and vehicle != "None" else "an unregistered two-wheeler"
    district = str(r.get("district", "the district"))
    station = str(r.get("police_station") or f"{district} PS")
    tod = str(r.get("time_of_day", "")).lower()
    sections = str(r.get("ipc_bns_sections") or "")

    opener = random.choice(OPENERS.get(ct, ["An offence was reported."])).format(
        victim=victim, age=age, district=district)
    modus = random.choice(MODUS.get(ct, [""])).format(
        weapon=weapon, vehicle=vehicle, district=district, tod=tod)
    parts = [
        f"FIR registered at {station}, {district}" + (f" during the {tod} hours." if tod else "."),
        opener,
        modus,
    ]
    tgt = r.get("target_type")
    if tgt in TARGET_LINE and random.random() < 0.7:
        parts.append(TARGET_LINE[tgt])
    parts.append(_casualty_clause(r.get("fatalities", 0), r.get("injured", 0)).strip())
    parts.append(_sev_clause(int(r.get("severity_score") or 0)).strip())
    if sections and random.random() < 0.85:
        parts.append(f"Booked under {sections}.")
    acc = int(r.get("accused_count") or 0)
    if acc:
        parts.append(f"{acc} accused named so far; investigation continues.")
    return " ".join(p for p in parts if p).strip()

def main():
    print("Generating rich diversified dataset strictly conforming to KSP schema...")

    # Create target data dir if not exists
    os.makedirs(DATA, exist_ok=True)

    # 1. State
    state_df = pd.DataFrame([{"StateID": 1, "StateName": "Karnataka", "NationalityID": 1, "Active": 1}])
    state_df.to_csv(os.path.join(DATA, "State.csv"), index=False)

    # 2. District
    dist_map = {name: idx + 1 for idx, name in enumerate(DISTRICTS)}
    dist_df = pd.DataFrame([{"DistrictID": dist_map[d], "DistrictName": d, "StateID": 1, "Active": 1} for d in DISTRICTS])
    dist_df.to_csv(os.path.join(DATA, "District.csv"), index=False)

    # 3. UnitType
    ut_df = pd.DataFrame([
        {"UnitTypeID": 1, "UnitTypeName": "Police Station", "CityDistState": "District", "Hierarchy": 1, "Active": 1},
        {"UnitTypeID": 2, "UnitTypeName": "Circle Office", "CityDistState": "District", "Hierarchy": 2, "Active": 1}
    ])
    ut_df.to_csv(os.path.join(DATA, "UnitType.csv"), index=False)

    # 4. Unit
    unit_map = {}
    unit_records = []
    u_idx = 1
    for d in DISTRICTS:
        for ps in STATIONS[d]:
            unit_map[ps] = u_idx
            unit_records.append({
                "UnitID": u_idx,
                "UnitName": ps,
                "TypeID": 1,
                "ParentUnit": np.nan,
                "NationalityID": 1,
                "StateID": 1,
                "DistrictID": dist_map[d],
                "Active": 1
            })
            u_idx += 1
    unit_df = pd.DataFrame(unit_records)
    unit_df.to_csv(os.path.join(DATA, "Unit.csv"), index=False)

    # 5. Rank
    rank_map = {name: idx + 1 for idx, name in enumerate(RANKS)}
    rank_df = pd.DataFrame([{"RankID": rank_map[r], "RankName": r, "Hierarchy": idx + 1, "Active": 1} for idx, r in enumerate(RANKS)])
    rank_df.to_csv(os.path.join(DATA, "Rank.csv"), index=False)

    # 6. Designation
    desigs = ["Investigating Officer", "SHO", "Inspector", "Writer"]
    desig_map = {name: idx + 1 for idx, name in enumerate(desigs)}
    desig_df = pd.DataFrame([{"DesignationID": desig_map[d], "DesignationName": d, "Active": 1, "SortOrder": idx + 1} for idx, d in enumerate(desigs)])
    desig_df.to_csv(os.path.join(DATA, "Designation.csv"), index=False)

    # 7. Employee & Officers Registry
    emp_records = []
    off_registry = []
    num_employees = 85
    for i in range(1, num_employees + 1):
        rk = random.choice(RANKS)
        ps = random.choice(list(unit_map.keys()))
        d_name = [d for d, sts in STATIONS.items() if ps in sts][0]
        
        emp_records.append({
            "EmployeeID": i,
            "DistrictID": dist_map[d_name],
            "UnitID": unit_map[ps],
            "RankID": rank_map[rk],
            "DesignationID": desig_map["Investigating Officer"] if rk in ["PSI", "SI", "PI (Inspector)"] else desig_map["SHO"],
            "KGID": f"KGID{100000 + i}",
            "FirstName": get_name(),
            "EmployeeDOB": f"{random.randint(1970, 1995)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
            "GenderID": 1 if random.random() < 0.85 else 2,
            "BloodGroupID": random.randint(1, 8),
            "PhysicallyChallenged": 0,
            "AppointmentDate": f"{random.randint(2005, 2020)}-01-01"
        })
        
        off_registry.append({
            "EmployeeID": i,
            "specialization": random.choice(SPECIALIZATIONS),
            "cases_assigned": random.randint(10, 80),
            "closure_rate": round(random.uniform(0.4, 0.9), 2),
            "commendations": random.randint(0, 8)
        })
    emp_df = pd.DataFrame(emp_records)
    emp_df.to_csv(os.path.join(DATA, "Employee.csv"), index=False)
    
    pd.DataFrame(off_registry).to_csv(os.path.join(DATA, "OfficersRegistry.csv"), index=False)

    # 8. CaseCategory
    cat_df = pd.DataFrame([
        {"CaseCategoryID": 1, "LookupValue": "FIR"},
        {"CaseCategoryID": 2, "LookupValue": "UDR"},
        {"CaseCategoryID": 3, "LookupValue": "Zero FIR"},
        {"CaseCategoryID": 4, "LookupValue": "PAR"}
    ])
    cat_df.to_csv(os.path.join(DATA, "CaseCategory.csv"), index=False)

    # 9. GravityOffence
    gravity_df = pd.DataFrame([
        {"GravityOffenceID": 1, "LookupValue": "Heinous"},
        {"GravityOffenceID": 2, "LookupValue": "Non-Heinous"}
    ])
    gravity_df.to_csv(os.path.join(DATA, "GravityOffence.csv"), index=False)

    # 10. CaseStatusMaster
    status_df = pd.DataFrame([
        {"CaseStatusID": 1, "CaseStatusName": "Under Investigation"},
        {"CaseStatusID": 2, "CaseStatusName": "Charge Sheeted"},
        {"CaseStatusID": 3, "CaseStatusName": "Closed"}
    ])
    status_df.to_csv(os.path.join(DATA, "CaseStatusMaster.csv"), index=False)

    # 11. CasteMaster
    caste_names = ["Gowda", "Kuruba", "Lingayat", "Vokkaliga", "Dalit", "General", "Others"]
    caste_map = {name: idx + 1 for idx, name in enumerate(caste_names)}
    caste_df = pd.DataFrame([{"caste_master_id": idx + 1, "caste_master_name": name} for idx, name in enumerate(caste_names)])
    caste_df.to_csv(os.path.join(DATA, "CasteMaster.csv"), index=False)

    # 12. ReligionMaster
    religion_names = ["Hindu", "Muslim", "Christian", "Sikh", "Jain", "Others"]
    rel_map = {name: idx + 1 for idx, name in enumerate(religion_names)}
    rel_df = pd.DataFrame([{"ReligionID": idx + 1, "ReligionName": name} for idx, name in enumerate(religion_names)])
    rel_df.to_csv(os.path.join(DATA, "ReligionMaster.csv"), index=False)

    # 13. OccupationMaster
    occupations = ["Farmer", "Government Employee", "Private Employee", "Businessman", "Unemployed", "Student", "Others"]
    occ_map = {name: idx + 1 for idx, name in enumerate(occupations)}
    occ_df = pd.DataFrame([{"OccupationID": idx + 1, "OccupationName": name} for idx, name in enumerate(occupations)])
    occ_df.to_csv(os.path.join(DATA, "OccupationMaster.csv"), index=False)

    # 14. Court
    court_map = {name: idx + 1 for idx, name in enumerate(COURTS)}
    court_df = pd.DataFrame([{"CourtID": court_map[c], "CourtName": c, "DistrictID": 1, "StateID": 1, "Active": 1} for c in COURTS])
    court_df.to_csv(os.path.join(DATA, "Court.csv"), index=False)

    # 15. CrimeHead
    crime_groups = ["Crimes Against Body", "Property Crime", "Cybercrime", "Narcotics", "Forest Offences", "Economic Offences"]
    ch_map = {name: idx + 1 for idx, name in enumerate(crime_groups)}
    ch_df = pd.DataFrame([{"CrimeHeadID": ch_map[name], "CrimeGroupName": name, "Active": 1} for name in crime_groups])
    ch_df.to_csv(os.path.join(DATA, "CrimeHead.csv"), index=False)

    # 16. CrimeSubHead
    subhead_map = {name: idx + 1 for idx, name in enumerate(CRIME_TYPES)}
    subhead_to_head = {}
    for name in CRIME_TYPES:
        if name in ("Chain Snatching", "House Break-in (Night)", "Vehicle Theft", "Robbery"):
            subhead_to_head[name] = ch_map["Property Crime"]
        elif name in ("Murder", "Assault", "Homicide"):
            subhead_to_head[name] = ch_map["Crimes Against Body"]
        elif "Cyber" in name:
            subhead_to_head[name] = ch_map["Cybercrime"]
        elif "Narcotics" in name:
            subhead_to_head[name] = ch_map["Narcotics"]
        elif "Sandalwood" in name or "Forest" in name:
            subhead_to_head[name] = ch_map["Forest Offences"]
        else:
            subhead_to_head[name] = ch_map["Economic Offences"]

    sh_df = pd.DataFrame([{
        "CrimeSubHeadID": subhead_map[name],
        "CrimeHeadID": subhead_to_head[name],
        "CrimeHeadName": name,
        "SeqID": idx + 1
    } for idx, name in enumerate(CRIME_TYPES)])
    sh_df.to_csv(os.path.join(DATA, "CrimeSubHead.csv"), index=False)

    # 17. Act
    act_df = pd.DataFrame([
        {"ActCode": "IPC", "ActDescription": "Indian Penal Code 1860", "ShortName": "IPC", "Active": 1},
        {"ActCode": "BNS", "ActDescription": "Bharatiya Nyaya Sanhita 2023", "ShortName": "BNS", "Active": 1},
        {"ActCode": "NDPS", "ActDescription": "Narcotic Drugs and Psychotropic Substances Act", "ShortName": "NDPS", "Active": 1},
        {"ActCode": "IT Act", "ActDescription": "Information Technology Act 2000", "ShortName": "IT Act", "Active": 1},
        {"ActCode": "Karnataka Forest Act", "ActDescription": "Karnataka Forest Act 1963", "ShortName": "Karnataka Forest Act", "Active": 1}
    ])
    act_df.to_csv(os.path.join(DATA, "Act.csv"), index=False)

    # 18. Section
    sections_data = [
        {"ActCode": "IPC", "SectionCode": "379", "SectionDescription": "Theft", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "356", "SectionDescription": "Assault to commit theft", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "457", "SectionDescription": "Lurking house-trespass", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "380", "SectionDescription": "Theft in dwelling house", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "384", "SectionDescription": "Extortion", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "386", "SectionDescription": "Extortion by putting in fear of death", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "302", "SectionDescription": "Murder", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "323", "SectionDescription": "Voluntarily causing hurt", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "304", "SectionDescription": "Culpable homicide", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "420", "SectionDescription": "Cheating", "Active": 1},
        {"ActCode": "IPC", "SectionCode": "34", "SectionDescription": "Common intention", "Active": 1},
        {"ActCode": "BNS", "SectionCode": "304(2)", "SectionDescription": "Snatching", "Active": 1},
        {"ActCode": "NDPS", "SectionCode": "20(b)", "SectionDescription": "Cannabis possession", "Active": 1},
        {"ActCode": "IT Act", "SectionCode": "66C", "SectionDescription": "Identity theft", "Active": 1},
        {"ActCode": "Karnataka Forest Act", "SectionCode": "86", "SectionDescription": "Sandalwood possession", "Active": 1}
    ]
    sec_df = pd.DataFrame(sections_data)
    sec_df.to_csv(os.path.join(DATA, "Section.csv"), index=False)

    # 19. CrimeHeadActSection
    chas_data = [
        {"CrimeHeadID": ch_map["Property Crime"], "ActCode": "IPC", "SectionCode": "379"},
        {"CrimeHeadID": ch_map["Property Crime"], "ActCode": "IPC", "SectionCode": "457"},
        {"CrimeHeadID": ch_map["Cybercrime"], "ActCode": "IT Act", "SectionCode": "66C"},
        {"CrimeHeadID": ch_map["Narcotics"], "ActCode": "NDPS", "SectionCode": "20(b)"},
        {"CrimeHeadID": ch_map["Forest Offences"], "ActCode": "Karnataka Forest Act", "SectionCode": "86"}
    ]
    chas_df = pd.DataFrame(chas_data)
    chas_df.to_csv(os.path.join(DATA, "CrimeHeadActSection.csv"), index=False)

    # 20. Suspects & Criminals Registry Generation
    # Scaled with the case count. At 800 offenders against 18,000 cases every
    # person in the registry would average ~36 FIRs and the worst would carry
    # 300+, which is not a criminal record — it is an artefact. Real dockets are
    # dominated by one-time accused with a repeat-offender tail, and both the
    # recidivism model and the network analysis need that shape to be a real task
    # rather than a foregone conclusion.
    num_suspects = 9000
    suspect_records = []
    crim_registry = []
    
    for i in range(1, num_suspects + 1):
        sid = f"SUS-{1000 + i}"
        if i == 1:
            name = "Kartik Bhartiya"
            gang = "Cyber Phishing Racket"
            risk = "Critical"
            alias = "'Chinna'"
            rowdy = True
            history = True
            warrant = True
        else:
            name = get_name()
            gang = random.choices(GANGS, weights=[1, 1, 1, 2, 2, 8])[0]
            risk = random.choices(["Low", "Medium", "High", "Critical"], weights=[6, 3, 2, 1])[0]
            alias = random.choice(["", f"'{random.choice(['Anna','Bhai','Don','Chinna','Lambu','Kala'])}'"])
            rowdy = random.choice([True, False, False, False])
            history = random.choice([True, False, False, False, False])
            warrant = random.random() < 0.18
        
        suspect_records.append({
            "suspect_id": sid,
            "name": name,
            "gang_affiliation": gang,
            "risk_level": risk
        })
        
        crim_registry.append({
            "PersonID": sid,
            "name": name,
            "alias": alias,
            "gang_affiliation": gang,
            "risk_level": risk,
            "fingerprint_id": f"FP-{random.randint(100000, 999999)}",
            "photo_url": f"https://i.pravatar.cc/120?u={sid}",
            "past_fir_count": random.randint(1, 12),
            "past_arrests": random.randint(1, 10),
            "known_associates": "",
            "rowdy_sheeter": rowdy,
            "history_sheet": history,
            "active_warrant": warrant
        })
    pd.DataFrame(suspect_records).to_csv(os.path.join(DATA, "suspects.csv"), index=False)
    pd.DataFrame(crim_registry).to_csv(os.path.join(DATA, "CriminalsRegistry.csv"), index=False)

    # 21. CaseMaster & Relational Offence Mappings
    num_cases = 18000
    cases_records = []
    comp_records = []
    asa_records = []
    victim_records = []
    accused_records = []
    as_records = []
    cs_records = []
    court_case_records = []

    serial_counters = {}
    acc_idx = 1
    as_idx = 1
    cs_idx = 1
    
    comp_names = ["Anitha", "Rekha", "Lakshmi", "Joseph", "Mary", "Manjunath", "Shivappa", "Rani", "Nagaraj"]

    # District assignment is weighted by 2011 Census population. Drawing uniformly
    # would give Kodagu (5.5 lakh) the same annual caseload as Bengaluru Urban
    # (96 lakh), which makes every per-capita rate and district ranking
    # meaningless. Drawn once up front rather than per-iteration.
    district_draw = random.choices(DISTRICTS, weights=DISTRICT_WEIGHTS, k=num_cases)

    print(f"Generating {num_cases:,} cases across {len(DISTRICTS)} districts...")
    for cm_id in range(20000, 20000 + num_cases):
        # Category
        category_id = random.choices([1, 2, 3, 4], weights=[85, 5, 8, 2])[0]
        
        # Date & Year
        year = random.choice([2020, 2021, 2022, 2023, 2024, 2025, 2026])
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        hour = random.randint(0, 23)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        ts = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        
        # Geography
        d_name = district_draw[cm_id - 20000]
        d_id = dist_map[d_name]
        ps_name = random.choice(STATIONS[d_name])
        u_id = unit_map[ps_name]
        
        # Crime Category Counter
        counter_key = (category_id, d_id, u_id, year)
        serial_counters[counter_key] = serial_counters.get(counter_key, 0) + 1
        serial = serial_counters[counter_key]
        
        crime_no = f"{category_id}{d_id:04d}{u_id:04d}{year}{serial:05d}"
        case_no = f"{year}{serial:05d}"
        
        # Officer
        emp_id = random.randint(1, num_employees)
        
        # Crime type
        crime_type = random.choice(CRIME_TYPES)
        minor_head_id = subhead_map[crime_type]
        major_head_id = subhead_to_head[crime_type]
        
        # Severity
        sev = random.randint(20, 98)
        gravity_id = 1 if sev > 75 else 2
        
        # Status
        status_id = random.choices([1, 2, 3], weights=[45, 40, 15])[0]
        
        # Court
        c_id = random.randint(1, len(COURTS))
        
        # Child entities & Embedded facts
        weapon = random.choice(WEAPONS)
        vehicle = random.choice(VEHICLES)
        target = random.choice(TARGETS)
        fatal = random.choices([0, 1, 2], weights=[90, 8, 2])[0] if crime_type in ["Murder", "Homicide", "Extortion"] else 0
        injured = random.choices([0, 1, 2, 3], weights=[70, 20, 8, 2])[0] if crime_type not in ["Cyber Fraud (Jamtara style)"] else 0
        witnesses = random.randint(0, 5)
        num_acc = random.randint(1, 3)

        # Resolve Act & Section
        act_id = "IPC"
        sec_id = "34"
        if crime_type == "Chain Snatching":
            act_id = "IPC"
            sec_id = "379"
        elif crime_type == "Vehicle Theft":
            act_id = "IPC"
            sec_id = "379"
        elif crime_type == "House Break-in (Night)":
            act_id = "IPC"
            sec_id = "457"
        elif crime_type == "Extortion":
            act_id = "IPC"
            sec_id = "384"
        elif crime_type == "Narcotics Distribution":
            act_id = "NDPS"
            sec_id = "20(b)"
        elif crime_type == "Sandalwood Smuggling":
            act_id = "Karnataka Forest Act"
            sec_id = "86"
        elif "Cyber" in crime_type:
            act_id = "IT Act"
            sec_id = "66C"
        elif crime_type == "Murder":
            act_id = "IPC"
            sec_id = "302"
        elif crime_type == "Assault":
            act_id = "IPC"
            sec_id = "323"
        elif crime_type == "Homicide":
            act_id = "IPC"
            sec_id = "304"

        # Resolve Victim Details
        v_name = get_name()
        v_age = random.randint(18, 70)
        v_gender = random.randint(1, 2)
        v_police = 1 if random.random() < 0.05 else 0

        # Resolve Time of Day for Narrative
        tod = "Morning (7AM-10AM)" if hour >= 7 and hour < 10 else "Afternoon (12PM-3PM)" if hour >= 12 and hour < 15 else "Evening (5PM-8PM)" if hour >= 17 and hour < 20 else "Night (11PM-3AM)"

        # Narrative generation
        narrative_text = build_narrative({
            "crime_type": crime_type,
            "victim_name": v_name,
            "victim_age": v_age,
            "weapon_used": weapon,
            "vehicle_used": vehicle,
            "district": d_name,
            "police_station": ps_name,
            "time_of_day": tod,
            "ipc_bns_sections": f"{act_id} Sec {sec_id}",
            "fatalities": fatal,
            "injured": injured,
            "severity_score": sev,
            "target_type": target,
            "accused_count": num_acc
        })
        brief_desc = f"{narrative_text} [METADATA: SEV={sev};WPN={weapon};VEH={vehicle};TGT={target};FAT={fatal};INJ={injured};WIT={witnesses}]"

        cases_records.append({
            "CaseMasterID": cm_id,
            "CrimeNo": crime_no,
            "CaseNo": case_no,
            "CrimeRegisteredDate": ts[:10],
            "PolicePersonID": emp_id,
            "PoliceStationID": u_id,
            "CaseCategoryID": category_id,
            "GravityOffenceID": gravity_id,
            "CrimeMajorHeadID": major_head_id,
            "CrimeMinorHeadID": minor_head_id,
            "CaseStatusID": status_id,
            "CourtID": c_id,
            "IncidentFromDate": ts,
            "IncidentToDate": ts,
            "InfoReceivedPSDate": ts,
            "latitude": round(12.0 + random.uniform(0.1, 3.5), 5),
            "longitude": round(74.0 + random.uniform(0.1, 4.0), 5),
            "BriefFacts": brief_desc
        })

        # 21. Complainant
        comp_records.append({
            "ComplainantID": cm_id - 10000,
            "CaseMasterID": cm_id,
            "ComplainantName": random.choice(comp_names) + f"_{cm_id}",
            "AgeYear": random.randint(22, 75),
            "OccupationID": random.randint(1, len(occupations)),
            "ReligionID": random.randint(1, len(religion_names)),
            "CasteID": random.randint(1, len(caste_names)),
            "GenderID": random.randint(1, 2)
        })

        # 22. ActSectionAssociation
        asa_records.append({
            "CaseMasterID": cm_id,
            "ActID": act_id,
            "SectionID": sec_id,
            "ActOrderID": 1,
            "SectionOrderID": 1
        })

        # 23. Victim
        victim_records.append({
            "VictimMasterID": cm_id - 10000,
            "CaseMasterID": cm_id,
            "VictimName": v_name,
            "AgeYear": v_age,
            "GenderID": v_gender,
            "VictimPolice": v_police
        })

        # 24. Accused & Links
        num_acc = random.randint(1, 3)
        for acc_offset in range(num_acc):
            assigned_sus = random.randint(1, num_suspects)
            if crime_type == "Cyber Fraud (Jamtara style)" and acc_offset == 0 and random.random() < 0.4:
                assigned_sus = 1
            sus_id = f"SUS-{1000 + assigned_sus}"
            sus_name = suspect_records[assigned_sus - 1]["name"]
            
            accused_records.append({
                "AccusedMasterID": acc_idx,
                "CaseMasterID": cm_id,
                "AccusedName": sus_name,
                "AgeYear": 15 if sus_id == "SUS-1001" else random.randint(19, 65),
                "GenderID": 1 if random.random() < 0.9 else 2,
                "PersonID": sus_id
            })
            
            # 25. ArrestSurrender
            if status_id in [2, 3]:
                as_records.append({
                    "ArrestSurrenderID": as_idx,
                    "CaseMasterID": cm_id,
                    "ArrestSurrenderTypeID": 1,
                    "ArrestSurrenderDate": ts[:10],
                    "ArrestSurrenderStateId": 1,
                    "ArrestSurrenderDistrictId": d_id,
                    "PoliceStationID": u_id,
                    "IOID": emp_id,
                    "CourtID": c_id,
                    "AccusedMasterID": acc_idx,
                    "IsAccused": 1,
                    "IsComplainantAccused": 0
                })
                as_idx += 1
                
            acc_idx += 1

        # 26. ChargesheetDetails & Court Cases
        if status_id in [2, 3]:
            cs_records.append({
                "CSID": cs_idx,
                "CaseMasterID": cm_id,
                "csdate": ts[:10] + " 11:00:00",
                "cstype": "A" if random.random() < 0.6 else "B",
                "PolicePersonID": emp_id
            })
            
            court_case_records.append({
                "CaseMasterID": cm_id,
                "court_case_no": f"CC/{random.randint(100, 9999)}/{year}",
                "chargesheet_status": "Filed",
                "court_name": COURTS[c_id - 1],
                "next_hearing": f"2026-{random.randint(8, 12):02d}-{random.randint(1, 28):02d}",
                "conviction_status": random.choice(["Convicted", "Acquitted", "Pending Trial"])
            })
            cs_idx += 1

    # Save case dataframes
    pd.DataFrame(cases_records).to_csv(os.path.join(DATA, "CaseMaster.csv"), index=False)
    pd.DataFrame(comp_records).to_csv(os.path.join(DATA, "ComplainantDetails.csv"), index=False)
    pd.DataFrame(asa_records).to_csv(os.path.join(DATA, "ActSectionAssociation.csv"), index=False)
    pd.DataFrame(victim_records).to_csv(os.path.join(DATA, "Victim.csv"), index=False)
    pd.DataFrame(accused_records).to_csv(os.path.join(DATA, "Accused.csv"), index=False)
    pd.DataFrame(as_records).to_csv(os.path.join(DATA, "ArrestSurrender.csv"), index=False)
    pd.DataFrame(cs_records).to_csv(os.path.join(DATA, "ChargesheetDetails.csv"), index=False)
    
    pd.DataFrame(court_case_records).to_csv(os.path.join(DATA, "CourtCasesRegistry.csv"), index=False)

    # 27. CaseLinks reconstruction
    links_records = []
    for acc in accused_records:
        cid = acc["CaseMasterID"]
        crime_no = [c["CrimeNo"] for c in cases_records if c["CaseMasterID"] == cid][0]
        links_records.append({
            "case_id": crime_no,
            "suspect_id": acc["PersonID"],
            "relationship": "Charged" if random.random() < 0.7 else "Co-Offender"
        })
    pd.DataFrame(links_records).to_csv(os.path.join(DATA, "case_links.csv"), index=False)

    # 28. Reports (narrative facts)
    reports_records = []
    for cm in cases_records:
        reports_records.append({
            "case_id": cm["CrimeNo"],
            "report_text": cm["BriefFacts"],
            "officer_id": f"OFF-{100 + cm['PolicePersonID']}"
        })
    pd.DataFrame(reports_records).to_csv(os.path.join(DATA, "case_reports.csv"), index=False)

    # Clean out the old flat files completely
    old_files_to_remove = [
        "firs.csv", "criminals.csv", "officers.csv", "court_cases.csv",
        "firs_raw.csv", "criminals_raw.csv", "officers_raw.csv", "court_cases_raw.csv"
    ]
    for old_file in old_files_to_remove:
        old_path = os.path.join(DATA, old_file)
        if os.path.exists(old_path):
            os.remove(old_path)
            print(f"Removed legacy flat file: {old_file}")

    print("Success! Generated normalized relational database and cleared legacy flat tables.")

if __name__ == "__main__":
    main()
