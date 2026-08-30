"""FIR narrative -> crime type.

Reads the free-text body of a First Information Report and predicts which crime
head it belongs to, so that un-categorised or mis-categorised reports can be
triaged automatically.

What makes this evaluation trustworthy
--------------------------------------
* **Temporal split.** Scored only on FIRs registered after the cutoff date.
* **Leakage scrub.** The structured [METADATA:] tag and any legal-section line
  are stripped before the text reaches the vectoriser, and a probe (below)
  checks that no single token is a perfect give-away for a class.
* **Baselines.** Reported against majority-class and stratified-random, because
  accuracy without a reference point is not a result.
* **Model selection on the training window only.** Hyper-parameters are chosen
  by cross-validation *inside* the training period; the test window is touched
  exactly once, at the end.
"""
import re

import numpy as np
import pandas as pd

from .. import config, evaluate, splits, baselines

NAME = "narrative_classifier"
_STRIP = [re.compile(p, re.IGNORECASE) for p in config.NARRATIVE_STRIP_PATTERNS]


def clean_text(text):
    """Remove spans that would let the model read the label instead of the language."""
    t = str(text or "")
    for pat in _STRIP:
        t = pat.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _leakage_probe(texts, labels, min_count=15):
    """Flag tokens that appear only ever inside one class.

    A token with high support that is 100% predictive of a single label is the
    signature of a template generator, not of natural language. Any hit here is
    surfaced as a warning in the report rather than quietly left in the features.
    """
    from sklearn.feature_extraction.text import CountVectorizer

    vec = CountVectorizer(min_df=min_count, binary=True)
    X = vec.fit_transform(texts)
    vocab = np.array(vec.get_feature_names_out())
    labels = np.asarray(labels)
    suspects = []
    for cls in np.unique(labels):
        mask = labels == cls
        in_cls = np.asarray(X[mask].sum(axis=0)).ravel()
        total = np.asarray(X.sum(axis=0)).ravel()
        with np.errstate(divide="ignore", invalid="ignore"):
            purity = np.where(total > 0, in_cls / total, 0.0)
        hits = np.where((purity >= 0.995) & (in_cls >= min_count))[0]
        for i in hits[:5]:
            suspects.append({"token": str(vocab[i]), "class": str(cls),
                             "occurrences": int(total[i]), "purity": round(float(purity[i]), 3)})
    return suspects


class _FixedSearch:
    """Stands in for a GridSearchCV that was skipped, so the rest of `train` is
    identical on both paths."""

    def __init__(self, estimator, params):
        self.best_estimator_ = estimator
        self.best_params_ = params
        self.best_score_ = float("nan")


def _recorded_params():
    """Hyper-parameters chosen by the last real training run, from the registry.

    Returns None if there is no recorded run, in which case the caller searches.
    """
    from .. import registry

    report = registry.read_report()
    entry = ((report or {}).get("models") or {}).get(NAME) or {}
    raw = entry.get("best_params") or {}
    if not raw:
        return None
    out = {}
    for key, value in raw.items():
        # The registry stores parameters as strings for portability.
        text = str(value)
        if text in ("None", "none"):
            out[key] = None
        elif text.startswith("("):
            try:
                out[key] = tuple(int(p) for p in text.strip("()").split(",") if p.strip())
            except ValueError:
                return None
        else:
            try:
                out[key] = int(text) if text.isdigit() else float(text)
            except ValueError:
                out[key] = text
    return out or None


def train(data, report=None):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.model_selection import GridSearchCV, StratifiedKFold
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.svm import LinearSVC
    from sklearn.naive_bayes import ComplementNB

    cases = data["cases"]
    reports = data["reports"]
    if reports is None or reports.empty:
        return None

    merged = reports.merge(
        cases[["case_id", "crime_type", "ts"]], on="case_id", how="inner")
    merged["text"] = merged["report_text"].map(clean_text)
    merged = merged[merged["text"].str.len() > 20].dropna(subset=["crime_type", "ts"])
    if len(merged) < 200 or merged["crime_type"].nunique() < 2:
        return None

    train_df, test_df, cutoff = splits.temporal_split(merged, "ts")
    Xtr, ytr = train_df["text"].values, train_df["crime_type"].astype(str).values
    Xte, yte = test_df["text"].values, test_df["crime_type"].astype(str).values

    # Classes absent from the training window cannot be predicted; scoring on them
    # would understate the model for a data problem, so they are reported instead.
    unseen = sorted(set(yte) - set(ytr))
    if unseen:
        keep = ~np.isin(yte, unseen)
        Xte, yte = Xte[keep], yte[keep]

    probe = _leakage_probe(list(Xtr), list(ytr))

    # --- model selection inside the training window only
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(sublinear_tf=True, strip_accents="unicode")),
        ("clf", LogisticRegression(max_iter=2000, random_state=config.RANDOM_STATE)),
    ])
    grid = {
        "tfidf__ngram_range": [(1, 1), (1, 2)],
        "tfidf__min_df": [2, 5],
        "tfidf__max_features": [20000],
        "clf__C": [0.5, 1.0, 4.0],
        "clf__class_weight": [None, "balanced"],
    }
    cv = StratifiedKFold(n_splits=4, shuffle=True, random_state=config.RANDOM_STATE)

    # The grid search is the expensive part of the whole ML layer. When this runs
    # as the serving fallback (report is None — the artifact could not be loaded,
    # typically a scikit-learn version mismatch), the hyper-parameters that a real
    # training run already selected are read from the portable registry JSON and
    # the search is skipped. That turns a ~30s cold start into ~2s and cannot
    # change the model: the recorded parameters are the search's own answer.
    recorded = _recorded_params() if report is None else None
    if recorded:
        best = pipe.set_params(**recorded).fit(Xtr, ytr)
        search = _FixedSearch(best, recorded)
    else:
        search = GridSearchCV(pipe, grid, scoring="f1_macro", cv=cv, n_jobs=-1, refit=True)
        search.fit(Xtr, ytr)
        best = search.best_estimator_

    # --- alternatives, fitted on the same training window for a fair comparison
    alternatives = []
    for label, est in [
        ("Linear SVM (calibrated)",
         Pipeline([("tfidf", TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), min_df=2)),
                   ("clf", CalibratedClassifierCV(
                       LinearSVC(random_state=config.RANDOM_STATE), cv=3))])),
        ("Complement Naive Bayes",
         Pipeline([("tfidf", TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), min_df=2)),
                   ("clf", ComplementNB())])),
    ]:
        try:
            est.fit(Xtr, ytr)
            from sklearn.metrics import accuracy_score, f1_score
            p = est.predict(Xte)
            alternatives.append({
                "model": label,
                "accuracy": round(float(accuracy_score(yte, p)), 4),
                "f1_macro": round(float(f1_score(yte, p, average="macro", zero_division=0)), 4),
            })
        except Exception as e:
            alternatives.append({"model": label, "error": str(e)})

    pred = best.predict(Xte)
    labels = sorted(set(ytr.tolist()))
    metrics = evaluate.classification_metrics(yte, pred, labels=labels)

    bl = [
        baselines.majority_class_baseline(ytr, yte),
        baselines.stratified_random_baseline(ytr, yte, config.RANDOM_STATE),
    ]

    # --- explainability: strongest tokens per class, straight off the fitted model
    class_keywords = {}
    try:
        vec = best.named_steps["tfidf"]
        clf = best.named_steps["clf"]
        names = vec.get_feature_names_out()
        for i, cls in enumerate(clf.classes_):
            coefs = clf.coef_[i] if clf.coef_.ndim > 1 else clf.coef_[0]
            top = np.argsort(coefs)[::-1][:10]
            class_keywords[str(cls)] = [
                {"token": str(names[j]), "weight": round(float(coefs[j]), 3)} for j in top]
    except Exception:
        pass

    notes = [
        "Trained and scored on a temporal split — the model never sees an FIR "
        "registered after the cutoff.",
        "The structured [METADATA:] tag and legal-section lines are stripped "
        "before vectorisation.",
    ]
    if probe:
        notes.append(
            f"Leakage probe flagged {len(probe)} token(s) that occur in only one class; "
            "treat their contribution with suspicion.")
    else:
        notes.append("Leakage probe found no token that uniquely identifies a class.")
    if unseen:
        notes.append(f"Classes absent from the training window and therefore excluded "
                     f"from scoring: {', '.join(unseen)}.")

    # Confusion between neighbouring heads is a finding worth stating outright.
    conf = _top_confusions(metrics)
    if conf:
        notes.append("Largest residual confusions: " + "; ".join(conf) + ".")

    artifact = {
        "name": NAME,
        "estimator": best,
        "classes": [str(c) for c in best.named_steps["clf"].classes_],
        "best_params": {k: str(v) for k, v in search.best_params_.items()},
        "class_keywords": class_keywords,
    }

    if report is not None:
        report.add(
            NAME,
            algorithm="TF-IDF + Logistic Regression (grid-searched on the training window)",
            task="multi-class text classification",
            target="crime_type",
            features=["FIR narrative text (word 1-2 grams, sublinear TF-IDF)"],
            split=splits.describe_split(train_df, test_df, cutoff),
            metrics=metrics,
            baselines=bl,
            notes=notes,
            extra={
                "best_params": artifact["best_params"],
                "cv_best_f1_macro": round(float(search.best_score_), 4),
                "alternatives": alternatives,
                "class_keywords": class_keywords,
                "leakage_probe": {"suspect_tokens": probe, "n_flagged": len(probe)},
            },
        )
        for s in probe:
            report.warn(f"[{NAME}] token '{s['token']}' appears only in class "
                        f"'{s['class']}' ({s['occurrences']} occurrences)")

    return artifact


def _top_confusions(metrics, k=3):
    """Read the largest off-diagonal cells out of the confusion matrix."""
    labels = metrics.get("labels") or []
    cm = metrics.get("confusion_matrix") or []
    pairs = []
    for i, row in enumerate(cm):
        for j, v in enumerate(row):
            if i != j and v > 0:
                total = sum(row) or 1
                pairs.append((v / total, v, labels[i], labels[j]))
    pairs.sort(reverse=True)
    return [f"{a} read as {b} ({int(round(100 * frac))}% of {a} cases)"
            for frac, _, a, b in pairs[:k]]


def make_predictor(artifact):
    """Rebuild the live scoring function: text -> ranked (label, probability)."""
    if not artifact:
        return None
    est = artifact["estimator"]
    classes = list(est.named_steps["clf"].classes_)

    def predict(text, k=3):
        cleaned = clean_text(text)
        if not cleaned:
            return []
        probs = est.predict_proba([cleaned])[0]
        order = np.argsort(probs)[::-1][:k]
        return [{"label": str(classes[i]), "prob": round(float(probs[i]), 4)} for i in order]

    return predict
