"""Survival-analysis primitives — the right tool for "how long until X?".

Why this module exists
----------------------
`tasks/allocation.py` asks a binary question: *was a chargesheet filed within 180
days?* To ask it honestly it has to throw away every case registered in the last
180 days of the corpus, because those cases have not had time to answer. On this
dataset that discards the most recent — and most operationally relevant — cases,
and it still cannot say anything at all about a case that is open **right now**.

That discarded information has a name: **right-censoring**. A case registered 40
days ago with no chargesheet has not "failed"; it is simply still running. It
carries real evidence — *this case survived at least 40 days* — and survival
analysis is the branch of statistics built to use it.

Treating censored rows as negatives biases the model toward pessimism; dropping
them biases it toward whatever was true further in the past. Neither is
acceptable when the output is "which of my open cases will breach the SLA".

Approach: discrete-time survival
--------------------------------
Rather than take a dependency on `lifelines` or `scikit-survival`, this module
uses **discrete-time survival analysis via person-period expansion**, which needs
nothing beyond the scikit-learn already in `requirements.txt` and is a standard,
textbook-correct construction:

1. Chop follow-up into fixed intervals (here, 30-day months).
2. Expand each case into one row per interval it *survived into* — stopping at
   the interval where the chargesheet lands, or at censoring.
3. Fit a plain classifier on those rows. Because each row asks "given this case
   reached interval k, did the event happen *in* interval k?", the fitted
   probability is exactly the **discrete hazard** h(k | x).
4. Recover the survival curve by chaining: S(t | x) = prod_{k<=t} (1 - h(k | x)).

Censored cases contribute every interval they survived and simply stop — they are
never scored as failures. That is the whole point.

This construction also buys something the binary model structurally cannot give:
one fitted model answers *every* horizon. The 60-day, 90-day and 180-day SLA
questions all read off the same curve, instead of needing three separate models
that could disagree with each other.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- person-period
def expand_person_period(durations, events, interval_days=30, n_intervals=12):
    """Expand per-case durations into one row per survived interval.

    Parameters
    ----------
    durations : array of float
        Days observed. For an event row, days until the event. For a censored
        row, days until observation stopped.
    events : array of {0,1}
        1 if the event was observed, 0 if the row is right-censored.
    interval_days, n_intervals :
        Discretisation grid. Follow-up beyond ``interval_days * n_intervals`` is
        administratively censored at the horizon rather than extrapolated.

    Returns
    -------
    row_index : int array
        Index into the original case array for each emitted period row.
    interval : int array
        1-based interval number for that row.
    y : int array
        1 only in the interval where the event actually occurred.

    A case with duration 74 days and event=1 at interval_days=30 emits three
    rows: (interval 1, y=0), (interval 2, y=0), (interval 3, y=1). A case
    censored at 74 days emits the same three rows with y=0 throughout — it is
    *at risk* in all three and fails in none.
    """
    durations = np.asarray(durations, dtype=float)
    events = np.asarray(events, dtype=int)
    horizon = interval_days * n_intervals

    # Interval containing each observation end. Day 0 still belongs to interval 1,
    # so a same-day chargesheet is an event rather than an empty row set.
    last = np.ceil(np.maximum(durations, 1e-9) / interval_days).astype(int)
    last = np.clip(last, 1, n_intervals)

    # An event after the horizon is not observable within the grid; the row is
    # censored at the horizon instead of being recorded as a late failure.
    observed = (events == 1) & (durations <= horizon)

    row_index = np.repeat(np.arange(len(durations)), last)
    # 1..last[i] for each i, built without a Python loop.
    interval = (np.concatenate([np.arange(1, k + 1) for k in last])
                if len(last) else np.array([], dtype=int))
    is_final = interval == last[row_index]
    y = (is_final & observed[row_index]).astype(int)
    return row_index, interval, y


def survival_from_hazard(hazard):
    """Chain discrete hazards into a survival curve: S(t) = prod_{k<=t} (1-h(k)).

    `hazard` is (n_rows, n_intervals). Returns the same shape, where column t
    holds the probability of surviving *through* interval t+1.
    """
    h = np.clip(np.asarray(hazard, dtype=float), 1e-9, 1 - 1e-9)
    return np.cumprod(1.0 - h, axis=1)


def survival_at(hazard, days, interval_days):
    """S(t) at an arbitrary day, not just at an interval boundary.

    Reading survival straight off the interval grid creates a false certainty at
    sub-interval resolution. A case open 35 days sits inside the interval that
    ends on day 60, so grid-only arithmetic says it is *certain* to still be open
    on day 60 — when in fact it might be charged on day 50. Quoting 100% there
    would be a claim the model has no basis for.

    Interpolating under the standard assumption of **constant hazard within each
    interval** removes the artifact:

        S(t) = prod_{k<q} (1 - h_k) * (1 - h_q) ** frac

    where q is the number of whole intervals elapsed and `frac` is the part of
    the current interval already served. Computed in log space so long horizons
    do not underflow.

    `days` may be a scalar or one value per row. Follow-up past the grid is
    clamped to the final interval rather than extrapolated.
    """
    h = np.clip(np.asarray(hazard, dtype=float), 1e-9, 1 - 1e-9)
    n, K = h.shape
    t = np.broadcast_to(np.asarray(days, dtype=float), (n,)).astype(float)
    t = np.maximum(t, 0.0)

    log1m = np.log(1.0 - h)                       # (n, K)
    cum = np.cumsum(log1m, axis=1)                # cum[:, k] = sum_{j<=k}

    units = t / float(interval_days)
    q = np.floor(units).astype(int)
    frac = units - q
    # Past the grid edge there is no further hazard to apply; clamp both parts.
    over = q >= K
    q_c = np.clip(q, 0, K - 1)

    whole = np.where(q_c > 0, cum[np.arange(n), np.maximum(q_c - 1, 0)], 0.0)
    whole = np.where(q_c == 0, 0.0, whole)
    partial = np.where(over, 0.0, frac * log1m[np.arange(n), q_c])
    total = np.where(over, cum[:, K - 1], whole + partial)
    return np.exp(total)


# ---------------------------------------------------------------- Kaplan-Meier
def kaplan_meier(durations, events, grid=None):
    """Non-parametric survival curve — the correct covariate-free baseline.

    This is the survival analogue of `baselines.prior_probability_baseline`: it
    is what you can say knowing *nothing* about a case except that it exists.
    A covariate model that cannot beat this has learned nothing, and reporting
    the two side by side is the only way to see that.

    Censored observations leave the risk set without ever counting as failures,
    which is precisely the property a naive "fraction charged so far" statistic
    lacks.
    """
    durations = np.asarray(durations, dtype=float)
    events = np.asarray(events, dtype=int)
    if not len(durations):
        return {"times": [], "survival": []}

    order = np.argsort(durations)
    d_sorted, e_sorted = durations[order], events[order]
    uniq = np.unique(d_sorted)

    times, surv, n_at_risk_out = [], [], []
    s = 1.0
    for t in uniq:
        at_risk = int((d_sorted >= t).sum())
        n_events = int(((d_sorted == t) & (e_sorted == 1)).sum())
        if at_risk == 0:
            break
        if n_events:
            s *= (1.0 - n_events / at_risk)
        times.append(float(t))
        surv.append(float(s))
        n_at_risk_out.append(at_risk)

    if grid is not None:
        g = np.asarray(grid, dtype=float)
        idx = np.searchsorted(np.asarray(times), g, side="right") - 1
        vals = [1.0 if i < 0 else surv[i] for i in idx]
        return {"times": [float(x) for x in g], "survival": [float(v) for v in vals]}
    return {"times": times, "survival": surv, "n_at_risk": n_at_risk_out}


def km_median(durations, events):
    """Median survival time: first point where the KM curve drops to 0.5.

    Returns None when the curve never reaches 0.5 — which is the honest answer
    for "median days to chargesheet" when most cases are never charged, rather
    than reporting a made-up number from the observed subset only.
    """
    km = kaplan_meier(durations, events)
    for t, s in zip(km["times"], km["survival"]):
        if s <= 0.5:
            return float(t)
    return None


# ---------------------------------------------------------------- concordance
def concordance_index(durations, events, risk_scores, max_pairs=4_000_000, random_state=42):
    """Harrell's C-index — ranking accuracy that understands censoring.

    A pair of cases is *comparable* only when we can actually tell which one
    failed first: the case with the shorter time must have had an observed event.
    If the shorter time is censored, we genuinely do not know the order and the
    pair is skipped rather than guessed.

    C = (concordant + 0.5 * tied) / comparable. 0.5 is random ranking; 1.0 is
    perfect. Higher `risk_scores` must mean *sooner* failure.
    """
    d = np.asarray(durations, dtype=float)
    e = np.asarray(events, dtype=int)
    r = np.asarray(risk_scores, dtype=float)
    n = len(d)
    if n < 2:
        return None

    # Comparable pairs are (i, j) where i had an event and d[i] < d[j].
    idx_event = np.flatnonzero(e == 1)
    if not len(idx_event):
        return None

    total_pairs = len(idx_event) * n
    rng = np.random.default_rng(random_state)
    if total_pairs > max_pairs:
        # Uniform sample over the candidate grid keeps this O(max_pairs) on a
        # large corpus. Reported in the registry so the number is not mistaken
        # for an exact computation.
        k = int(max_pairs)
        ii = idx_event[rng.integers(0, len(idx_event), size=k)]
        jj = rng.integers(0, n, size=k)
    else:
        ii = np.repeat(idx_event, n)
        jj = np.tile(np.arange(n), len(idx_event))

    comparable = d[ii] < d[jj]
    if not comparable.any():
        return None
    ri, rj = r[ii][comparable], r[jj][comparable]
    concordant = float((ri > rj).sum())
    tied = float((ri == rj).sum())
    total = float(comparable.sum())
    return round((concordant + 0.5 * tied) / total, 4)


# ---------------------------------------------------------------- horizon scoring
def horizon_labels(durations, events, horizon_days):
    """Binary outcome at a fixed horizon, keeping only rows that can answer it.

    A case censored at day 40 cannot tell us what happened by day 90 — including
    it as a negative would be exactly the bias this module exists to avoid. It is
    dropped from that horizon's evaluation and reported in `n_evaluable`.

    Returns (mask, y) where `mask` selects the rows with an observable answer.
    """
    d = np.asarray(durations, dtype=float)
    e = np.asarray(events, dtype=int)
    failed_by = (e == 1) & (d <= horizon_days)
    # Answerable if it failed by the horizon, or if it was observed past it.
    known = failed_by | (d >= horizon_days)
    return known, failed_by[known].astype(int)


def integrated_brier(durations, events, surv_matrix, grid):
    """Brier score at each horizon, averaged — a proper scoring rule for curves.

    Uses only rows whose outcome at each horizon is genuinely observable (see
    `horizon_labels`), so censored cases never silently count as successes. This
    is a censoring-restricted estimate rather than the IPCW-weighted one; it is
    the conservative choice and is labelled as such in the registry.
    """
    grid = np.asarray(grid, dtype=float)
    per_time = []
    for col, t in enumerate(grid):
        mask, y = horizon_labels(durations, events, t)
        if mask.sum() < 20 or col >= surv_matrix.shape[1]:
            continue
        # P(event by t) = 1 - S(t)
        p_event = 1.0 - surv_matrix[mask, col]
        per_time.append({
            "horizon_days": int(t),
            "brier": round(float(np.mean((p_event - y) ** 2)), 4),
            "n_evaluable": int(mask.sum()),
            "observed_rate": round(float(y.mean()), 4),
        })
    ibs = round(float(np.mean([p["brier"] for p in per_time])), 4) if per_time else None
    return {"integrated_brier_score": ibs, "per_horizon": per_time}
