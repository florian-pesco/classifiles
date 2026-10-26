"""Trained classifier on the example vectors (experimental; measured by `classifiles eval --trained`).

The nearest-neighbour vote (examples.py) asks the 20 most similar sorted files. This model instead
learns from all examples at once: multinomial logistic regression, i.e. one weight vector per
category over the 1024 embedding dimensions, turned into probabilities with a softmax.

It trains on the *inbox-style* vector of each example (`examples.qvector`: the file embedded like an
inbox file, without its folder). The regular example vector contains the category folder name, which
a trained model would simply learn to read, and inbox files have no folder.

- Categories are balanced (each counts as much in total, whatever its number of examples), so big
  categories don't drown small ones.
- Learned examples (files you applied) are real inbox files with your answer and count more
  (`applied_weight`).
- CPU only, a few seconds; thread count capped.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

C_GRID = (0.3, 1.0, 3.0, 10.0)


@dataclass
class TrainedModel:
    keys: list[str]                      # category keys, one row of coef per key
    coef: np.ndarray                     # (categories, dims)
    intercept: np.ndarray                # (categories,)
    C: float = 1.0
    n_train: dict = field(default_factory=dict)  # examples per category key

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        z = np.atleast_2d(X) @ self.coef.T + self.intercept
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def probs(self, vec: np.ndarray, allowed: set[str] | None = None) -> dict[str, float]:
        """Probability per category key (renormalised over `allowed`, if given)."""
        p = self.predict_proba(vec)[0]
        out = {k: float(v) for k, v in zip(self.keys, p) if allowed is None or k in allowed}
        total = sum(out.values()) or 1.0
        return {k: v / total for k, v in out.items()}


def sample_weights(y: list[str], extra: np.ndarray | None = None) -> np.ndarray:
    """Balanced class weights (n / (classes · n_class)), times optional per-sample weights."""
    counts = Counter(y)
    w = np.array([len(y) / (len(counts) * counts[k]) for k in y], dtype=np.float64)
    return w * extra if extra is not None else w


def fit(X: np.ndarray, y: list[str], weights: np.ndarray | None = None, C: float = 1.0,
        threads: int = 2) -> TrainedModel:
    from sklearn.linear_model import LogisticRegression
    from threadpoolctl import threadpool_limits

    with threadpool_limits(threads):
        m = LogisticRegression(C=C, max_iter=1000, tol=1e-4)
        m.fit(X, y, sample_weight=sample_weights(y, weights))
    keys = [str(k) for k in m.classes_]
    coef, intercept = m.coef_, m.intercept_
    if len(keys) == 2:  # sklearn stores one row for two classes: expand to softmax form
        coef = np.vstack([-coef[0] / 2, coef[0] / 2])
        intercept = np.array([-intercept[0] / 2, intercept[0] / 2])
    return TrainedModel(keys, coef.astype(np.float32), intercept.astype(np.float32), C, dict(Counter(y)))


def choose_C(X: np.ndarray, y: list[str], weights: np.ndarray | None = None, grid=C_GRID, folds: int = 3,
             threads: int = 2, seed: int = 0) -> tuple[float, dict[float, float]]:
    """Regularisation strength by cross-validation: the C with the best balanced accuracy (mean
    recall per category), since categories are balanced in training too."""
    from sklearn.model_selection import StratifiedKFold

    y_arr = np.array(y)
    scores = {}
    for C in grid:
        recalls = []
        for train, test in StratifiedKFold(folds, shuffle=True, random_state=seed).split(X, y_arr):
            m = fit(X[train], list(y_arr[train]), None if weights is None else weights[train], C, threads)
            pred = np.array(m.keys)[m.predict_proba(X[test]).argmax(axis=1)]
            per = [np.mean(pred[y_arr[test] == k] == k) for k in set(y_arr[test])]
            recalls.append(float(np.mean(per)))
        scores[C] = float(np.mean(recalls))
    return max(scores, key=scores.get), scores


def training_set(rows: list[dict], exclude_ids: set[int] | None = None, applied_weight: float = 3.0
                 ) -> tuple[np.ndarray, list[str], np.ndarray]:
    """X, y and per-sample weights from example rows (id, category_key, origin, qvector)."""
    rows = [r for r in rows if not exclude_ids or r["id"] not in exclude_ids]
    if not rows:
        return np.zeros((0, 0), np.float32), [], np.zeros(0)
    X = np.stack([np.frombuffer(r["qvector"], dtype=np.float16) for r in rows]).astype(np.float32)
    y = [r["category_key"] for r in rows]
    w = np.array([applied_weight if r["origin"] == "applied" else 1.0 for r in rows])
    return X, y, w


def blend(p_knn: dict[str, float], p_trained: dict[str, float], a: float, eps: float = 1e-4) -> dict[str, float]:
    """P ∝ P_knn^(1−a) · P_trained^a over the union of categories (same scheme as pictures)."""
    keys = set(p_knn) | set(p_trained)
    raw = {k: max(p_knn.get(k, 0.0), eps) ** (1 - a) * max(p_trained.get(k, 0.0), eps) ** a for k in keys}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}
