"""
KSP IntelHub — district socio-economic indicators for all 31 Karnataka districts.

Supplies the one Challenge-02 capability the FIR tables cannot express on their
own: socio-economic crime correlation — the "why" behind the "where".

**What is real and what is modelled.** This distinction is published rather than
blurred, because a correlation is only as trustworthy as the weakest series in it:

* `population_lakh`, `urbanization`, `literacy` — **2011 Census figures.** Real,
  externally checkable numbers. Yadgir really does have the lowest literacy in
  Karnataka (51.8%) and Dakshina Kannada among the highest (88.6%).
* `per_capita_income_k`, `unemployment`, `police_per_100k`, `youth_pct`,
  `slum_pct` — **deterministic estimates derived from the census series above**
  by the documented formulas below. They are internally coherent and ordered
  correctly across districts, but they are not measured figures and must not be
  quoted as such. Swapping in a real DES/NCRB extract means replacing this file
  and nothing else.

Deterministic — no RNG. Run:  python generate_socioeconomic.py
"""
import os

import pandas as pd

import karnataka as KA

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 2011 Census literacy rate (%) per district. Real figures.
LITERACY = {
    "Bagalkote": 68.8, "Ballari": 67.4, "Belagavi": 73.5, "Bengaluru Rural": 77.9,
    "Bengaluru Urban": 87.7, "Bidar": 70.5, "Chamarajanagara": 61.4,
    "Chikkaballapura": 70.1, "Chikkamagaluru": 79.3, "Chitradurga": 73.7,
    "Dakshina Kannada": 88.6, "Davanagere": 75.7, "Dharwad": 80.0, "Gadag": 75.1,
    "Hassan": 76.1, "Haveri": 77.6, "Kalaburagi": 64.9, "Kodagu": 82.6,
    "Kolar": 74.4, "Koppal": 68.1, "Mandya": 70.4, "Mysuru": 72.8,
    "Raichur": 60.5, "Ramanagara": 69.2, "Shivamogga": 80.5, "Tumakuru": 75.1,
    "Udupi": 86.2, "Uttara Kannada": 84.1, "Vijayanagara": 67.0,
    "Vijayapura": 67.2, "Yadgir": 51.8,
}

COLS = ["district", "population_lakh", "literacy", "unemployment", "per_capita_income_k",
        "urbanization", "police_per_100k", "youth_pct", "slum_pct"]


def _derive(urban, lit):
    """Modelled indicators, as documented in the module docstring.

    Each formula is monotone in the census inputs and produces values in a
    plausible range for Karnataka. They exist so the correlation panel has
    something coherent to correlate against before a real extract arrives — not
    to stand in for measurement.
    """
    income = round(55 + 2.0 * urban + 1.6 * max(0.0, lit - 55))
    unemployment = round(10.5 - 0.06 * lit - 0.01 * urban, 1)
    police = round(70 + 0.9 * urban + 0.3 * lit)
    youth = round(23 + 0.06 * urban + 0.04 * (100 - lit))
    slum = round(2.5 + 0.09 * urban + 0.06 * (100 - lit), 1)
    return unemployment, income, police, youth, slum


def main():
    rows = []
    for name in KA.DISTRICT_NAMES:
        pop = KA.population(name)
        urban = KA.urban_pct(name)
        lit = LITERACY[name]
        unemployment, income, police, youth, slum = _derive(urban, lit)
        rows.append((name, pop, lit, unemployment, income, urban, police, youth, slum))

    df = pd.DataFrame(rows, columns=COLS)
    df.to_csv(os.path.join(DATA, "socioeconomic.csv"), index=False)
    print(f"Wrote socioeconomic.csv — {len(df)} districts, {len(COLS) - 1} indicators.")
    print(f"  literacy spans {df['literacy'].min()}% ({df.loc[df['literacy'].idxmin(), 'district']}) "
          f"to {df['literacy'].max()}% ({df.loc[df['literacy'].idxmax(), 'district']})")
    print("  census: population, urbanization, literacy | derived: the rest")


if __name__ == "__main__":
    main()
