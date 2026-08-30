"""Offline training entrypoint.

    python -m ml.train                 # train everything, write artifacts + report
    python -m ml.train --only narrative_classifier recidivism_risk
    python -m ml.train --no-artifacts  # evaluate and report, persist nothing
    python -m ml.train --data-dir ../real_extract

Produces:
    artifacts/<model>.joblib      fitted estimators (a boot-time speed path)
    artifacts/model_registry.json the portable evaluation report the UI renders

The report is the deliverable. Artifacts are an optimisation: if the serving
runtime has a different scikit-learn build they are refused on load and the app
retrains in-process from this same code, so the two paths can never disagree
about what a model is.
"""
import argparse
import sys
import time
import traceback

from . import config, dataset, registry
from .registry import ReportBuilder
from .tasks import TASKS


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train and evaluate the KSP IntelHub models.")
    ap.add_argument("--only", nargs="+", metavar="MODEL",
                    help=f"train a subset. Available: {', '.join(TASKS)}")
    ap.add_argument("--data-dir", default=None,
                    help="directory of source tables (defaults to appsail/data)")
    ap.add_argument("--no-artifacts", action="store_true",
                    help="evaluate and write the report without persisting estimators")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    selected = args.only or list(TASKS)
    unknown = [m for m in selected if m not in TASKS]
    if unknown:
        ap.error(f"unknown model(s): {', '.join(unknown)}. Available: {', '.join(TASKS)}")

    def log(*a):
        if not args.quiet:
            print(*a, flush=True)

    log("=" * 74)
    log("KSP IntelHub — model training")
    log("=" * 74)

    t0 = time.time()
    data = dataset.load_canonical(args.data_dir)
    census = dataset.describe(data)
    span = census.get("_span", {})
    log(f"Loaded {census.get('cases', {}).get('rows', 0)} cases "
        f"({span.get('first_incident')} to {span.get('last_incident')}), "
        f"{census.get('offenders', {}).get('rows', 0)} offenders, "
        f"{census.get('courts', {}).get('rows', 0)} court records "
        f"in {time.time() - t0:.1f}s")
    log("")

    # A partial run must not wipe the entries for models it did not touch, so the
    # previous report is carried forward for everything outside `--only`.
    partial = bool(args.only)
    keep = [m for m in TASKS if m not in selected] if partial else None
    report = ReportBuilder(carry_forward=partial, keep=keep)
    report.dataset = census
    if report.carried:
        log(f"Carrying forward existing entries for: {', '.join(sorted(report.carried))}")
        log("")

    trained, skipped, failed = [], [], []
    for name in selected:
        module = TASKS[name]
        log(f"-> {name}")
        t = time.time()
        try:
            artifact = module.train(data, report)
        except Exception as e:
            failed.append((name, str(e)))
            report.warn(f"[{name}] training raised: {e}")
            log(f"   FAILED: {e}")
            if not args.quiet:
                traceback.print_exc()
            continue

        if artifact is None:
            skipped.append(name)
            report.warn(f"[{name}] skipped — insufficient data for a valid evaluation")
            log("   skipped (insufficient data for a valid evaluation)")
            continue

        path = None
        if not args.no_artifacts:
            artifact["_env"] = registry._env_stamp()
            path = registry.save_artifact(name, artifact)
            if path and name in report.models:
                import os
                report.models[name]["artifact"] = os.path.basename(path)

        entry = report.models.get(name, {})
        log(f"   {entry.get('headline', 'done')}   [{time.time() - t:.1f}s]")
        trained.append(name)

    path = report.write()

    log("")
    log("=" * 74)
    log(f"Trained {len(trained)}  |  skipped {len(skipped)}  |  failed {len(failed)}")
    if skipped:
        log(f"  skipped: {', '.join(skipped)}")
    if failed:
        log(f"  failed:  {', '.join(n for n, _ in failed)}")
    if report.warnings:
        log("")
        log("Warnings recorded in the report:")
        for w in report.warnings:
            log(f"  ! {w}")
    log("")
    log(f"Report written to {path}")
    log(f"Total {time.time() - t0:.1f}s")
    log("=" * 74)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
