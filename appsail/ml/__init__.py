"""KSP IntelHub machine-learning package.

Everything that fits, validates or scores a model lives here. The Flask app
(`main.py`) only *consumes* this package — it never defines features or metrics
itself, so the numbers shown in the UI are exactly the numbers produced by the
offline training run.

Layout
------
config.py     constants, paths, feature specs, evaluation policy
dataset.py    the adapter: raw tables -> canonical frames (the real-data swap point)
splits.py     temporal / grouped / walk-forward splitters
baselines.py  the "can a trivial rule do this?" reference models
evaluate.py   honest metric computation (incl. confusion, calibration, lift)
registry.py   artifact persistence + the portable JSON evaluation report
tasks/        one module per model, each exposing train(...) -> TrainedModel
train.py      CLI entrypoint:  python -m ml.train
"""
__all__ = ["config", "dataset", "splits", "baselines", "evaluate", "registry"]
