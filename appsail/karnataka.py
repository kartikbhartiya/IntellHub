"""Canonical Karnataka geography — the single source of truth for districts.

Why this module exists
----------------------
The corpus previously ran on **ten** districts, and two of the ten were not
districts at all:

* **Mangaluru** is not a district. It is the headquarters of Dakshina Kannada,
  and separately a police commissionerate. Listing it alongside Dakshina Kannada
  double-counted the same territory.
* **Hubballi-Dharwad** is a police commissionerate covering the twin cities. The
  revenue district is **Dharwad**.

Karnataka has **31 districts**. Any SCRB officer reading a district filter that
stops at ten — and that contains a city where a district should be — knows the
data is invented before they look at a single model. Getting the administrative
geography right is not cosmetic; it is the first credibility test the platform
has to pass.

What is authoritative here vs. approximate
------------------------------------------
* **District names and count** — authoritative. All 31, including Vijayanagara,
  carved out of Ballari in 2021.
* **Police commissionerates** — authoritative. Karnataka has 8; they are a
  separate command structure from the revenue districts and are flagged rather
  than mixed into the district list.
* **Headquarters coordinates** — approximate district-HQ positions, accurate to
  roughly a kilometre. They anchor the synthetic incident scatter; they are not
  survey-grade boundaries, and the platform never claims they are.
* **Population** — 2011 Census figures in lakhs, with Ballari/Vijayanagara split
  apportioned. Used to weight synthetic case volume so that Bengaluru Urban does
  not generate the same caseload as Kodagu. Real 2026 figures would differ; the
  point is the *relative* weighting, which is what makes per-capita rates and
  district comparisons meaningful at all.
* **Urban share** — 2011 Census urban population percentage, used for the
  socio-economic overlay.
* **Police station names** — real town and locality names within each district.
  The station roster is representative, not the complete KSP establishment list.

Every consumer — the generator, the outcome simulator, the socio-economic
overlay — reads from here, so the geography cannot drift between them.
"""

# name -> (lat, lon, population_lakhs_2011, urban_pct_2011)
DISTRICTS = {
    "Bagalkote":        (16.1725, 75.6560, 18.90, 27.5),
    "Ballari":          (15.1394, 76.9214, 13.00, 37.7),
    "Belagavi":         (15.8497, 74.4977, 47.79, 25.1),
    "Bengaluru Rural":  (13.2846, 77.7101,  9.91, 24.4),
    "Bengaluru Urban":  (12.9716, 77.5946, 96.22, 90.9),
    "Bidar":            (17.9104, 77.5199, 17.03, 24.4),
    "Chamarajanagara":  (11.9261, 76.9438, 10.21, 17.0),
    "Chikkaballapura":  (13.4355, 77.7315, 12.55, 22.8),
    "Chikkamagaluru":   (13.3161, 75.7720, 11.37, 22.6),
    "Chitradurga":      (14.2251, 76.3980, 16.60, 19.6),
    "Dakshina Kannada": (12.8698, 74.8431, 20.90, 47.7),
    "Davanagere":       (14.4644, 75.9218, 19.46, 32.3),
    "Dharwad":          (15.4589, 75.0078, 18.47, 56.8),
    "Gadag":            (15.4315, 75.6355, 10.65, 35.6),
    "Hassan":           (13.0068, 76.0997, 17.76, 21.4),
    "Haveri":           (14.7951, 75.3990, 15.98, 21.5),
    "Kalaburagi":       (17.3297, 76.8343, 25.66, 32.6),
    "Kodagu":           (12.4218, 75.7405,  5.54, 14.6),
    "Kolar":            (13.1357, 78.1326, 15.36, 30.0),
    "Koppal":           (15.3547, 76.1550, 13.89, 16.8),
    "Mandya":           (12.5223, 76.8955, 18.06, 16.0),
    "Mysuru":           (12.2958, 76.6394, 30.01, 41.5),
    "Raichur":          (16.2076, 77.3463, 19.28, 22.8),
    "Ramanagara":       (12.7209, 77.2799, 10.82, 23.5),
    "Shivamogga":       (13.9299, 75.5681, 17.53, 35.3),
    "Tumakuru":         (13.3379, 77.1173, 26.79, 19.9),
    "Udupi":            (13.3409, 74.7421, 11.77, 27.0),
    "Uttara Kannada":   (14.8136, 74.1297, 14.37, 30.1),
    "Vijayanagara":     (15.2689, 76.3909, 12.00, 30.4),
    "Vijayapura":       (16.8302, 75.7100, 21.77, 22.6),
    "Yadgir":           (16.7700, 77.1370, 11.75, 17.0),
}

# Karnataka State Police commissionerates — a separate command structure from the
# revenue districts, deliberately kept out of the district list so the two are
# never conflated. `covers` names the district(s) each sits inside.
COMMISSIONERATES = {
    "Bengaluru City":   ["Bengaluru Urban"],
    "Mysuru City":      ["Mysuru"],
    "Mangaluru City":   ["Dakshina Kannada"],
    "Hubballi-Dharwad": ["Dharwad"],
    "Belagavi City":    ["Belagavi"],
    "Kalaburagi City":  ["Kalaburagi"],
    "Davanagere City":  ["Davanagere"],
    "Shivamogga City":  ["Shivamogga"],
}

# Representative station roster per district, using real town / locality names.
# Bengaluru Urban carries the most because it genuinely does.
STATIONS = {
    "Bagalkote":        ["Bagalkote Town PS", "Badami PS", "Jamkhandi PS", "Mudhol PS"],
    "Ballari":          ["Cowl Bazaar PS", "Ballari Town PS", "Sandur PS", "Siruguppa PS"],
    "Belagavi":         ["Camp PS", "Tilakwadi PS", "Chikkodi PS", "Gokak PS", "Bailhongal PS"],
    "Bengaluru Rural":  ["Devanahalli PS", "Doddaballapura PS", "Nelamangala PS", "Hoskote PS"],
    "Bengaluru Urban":  ["Cubbon Park PS", "Koramangala PS", "Whitefield PS", "Yeshwanthpur PS",
                         "Electronic City PS", "Jayanagar PS", "Indiranagar PS", "KR Puram PS",
                         "Banashankari PS", "Hebbal PS", "Marathahalli PS", "Rajajinagar PS"],
    "Bidar":            ["Bidar Town PS", "Basavakalyan PS", "Humnabad PS", "Bhalki PS"],
    "Chamarajanagara":  ["Chamarajanagara Town PS", "Kollegal PS", "Gundlupet PS"],
    "Chikkaballapura":  ["Chikkaballapura Town PS", "Sidlaghatta PS", "Gauribidanur PS"],
    "Chikkamagaluru":   ["Chikkamagaluru Town PS", "Kadur PS", "Tarikere PS", "Sringeri PS"],
    "Chitradurga":      ["Chitradurga Town PS", "Hiriyur PS", "Challakere PS", "Hosadurga PS"],
    "Dakshina Kannada": ["Mangaluru North PS", "Pandeshwar PS", "Bantwal PS", "Puttur PS",
                         "Sullia PS", "Ullal PS"],
    "Davanagere":       ["Davanagere Town PS", "Harihara PS", "Channagiri PS", "Honnali PS"],
    "Dharwad":          ["Vidyanagar PS", "Dharwad Town PS", "Hubballi Market PS",
                         "Navanagar PS", "Kalghatgi PS"],
    "Gadag":            ["Gadag Town PS", "Betageri PS", "Ron PS", "Nargund PS"],
    "Hassan":           ["Hassan Town PS", "Arsikere PS", "Sakleshpur PS", "Channarayapatna PS"],
    "Haveri":           ["Haveri Town PS", "Ranebennur PS", "Byadgi PS", "Hirekerur PS"],
    "Kalaburagi":       ["Brahmapur PS", "Kalaburagi Town PS", "Sedam PS", "Chittapur PS",
                         "Aland PS"],
    "Kodagu":           ["Madikeri PS", "Virajpet PS", "Somwarpet PS"],
    "Kolar":            ["Kolar Town PS", "KGF PS", "Bangarpet PS", "Malur PS"],
    "Koppal":           ["Koppal Town PS", "Gangavathi PS", "Yelburga PS", "Kushtagi PS"],
    "Mandya":           ["Mandya Town PS", "Maddur PS", "Malavalli PS", "Srirangapatna PS",
                         "Pandavapura PS"],
    "Mysuru":           ["Devaraja PS", "Lashkar PS", "Vijayanagar PS", "Nazarbad PS",
                         "Krishnaraja PS", "Hunsur PS", "T Narasipura PS"],
    "Raichur":          ["Raichur Town PS", "Sindhanur PS", "Manvi PS", "Lingsugur PS"],
    "Ramanagara":       ["Ramanagara Town PS", "Channapatna PS", "Kanakapura PS", "Magadi PS"],
    "Shivamogga":       ["Doddapet PS", "Vinoba Nagar PS", "Bhadravathi PS", "Sagara PS",
                         "Shikaripura PS"],
    "Tumakuru":         ["Kyathsandra PS", "Tumakuru Town PS", "Tiptur PS", "Sira PS",
                         "Madhugiri PS"],
    "Udupi":            ["Udupi Town PS", "Malpe PS", "Kundapura PS", "Karkala PS"],
    "Uttara Kannada":   ["Karwar Town PS", "Sirsi PS", "Bhatkal PS", "Honnavar PS", "Dandeli PS"],
    "Vijayanagara":     ["Hosapete PS", "Kampli PS", "Hagaribommanahalli PS", "Harapanahalli PS"],
    "Vijayapura":       ["Vijayapura Town PS", "Indi PS", "Basavana Bagevadi PS", "Sindagi PS"],
    "Yadgir":           ["Yadgir Town PS", "Shahapur PS", "Surpur PS"],
}

DISTRICT_NAMES = sorted(DISTRICTS)


def centre(district):
    """(lat, lon) of a district headquarters. Falls back to the state centroid."""
    d = DISTRICTS.get(district)
    return (d[0], d[1]) if d else (14.5204, 75.7224)


def population(district):
    """2011 Census population in lakhs."""
    d = DISTRICTS.get(district)
    return d[2] if d else 10.0


def urban_pct(district):
    d = DISTRICTS.get(district)
    return d[3] if d else 30.0


def population_weights():
    """Normalised population shares — the sampling weights for case volume.

    Drawing districts uniformly gives Kodagu (5.5 lakh) the same annual caseload
    as Bengaluru Urban (96 lakh), which makes every per-capita rate, every
    district ranking and every hotspot comparison meaningless. Weighting by
    population is the minimum needed for those comparisons to mean anything.
    """
    total = sum(v[2] for v in DISTRICTS.values())
    return {name: v[2] / total for name, v in DISTRICTS.items()}


def commissionerate_for(district):
    """The commissionerate sitting inside a district, if any."""
    for name, covers in COMMISSIONERATES.items():
        if district in covers:
            return name
    return None


def summary():
    """One-line census used by the generators and the registry."""
    return {
        "districts": len(DISTRICTS),
        "commissionerates": len(COMMISSIONERATES),
        "police_stations": sum(len(v) for v in STATIONS.values()),
        "population_lakhs_2011": round(sum(v[2] for v in DISTRICTS.values()), 2),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
    missing = set(DISTRICTS) - set(STATIONS)
    extra = set(STATIONS) - set(DISTRICTS)
    print("districts without stations:", sorted(missing) or "none")
    print("stations without a district:", sorted(extra) or "none")
