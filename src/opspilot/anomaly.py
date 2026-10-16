"""Robust univariate and multivariate anomaly detectors for demo service signals."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest
from statsmodels.tsa.seasonal import STL


@dataclass(frozen=True)
class Observation:
    timestamp: str
    service: str
    latency_seconds: float
    error_rate: float
    cpu_ratio: float = 0.0
    memory_ratio: float = 0.0
    restarts: float = 0.0
    label: str | None = None
    latency_p50_seconds: float = 0.0

    def features(self) -> list[float]:
        return [
            self.latency_seconds,
            self.latency_p50_seconds,
            self.error_rate,
            self.cpu_ratio,
            self.memory_ratio,
            self.restarts,
        ]


class STLMedianAbsoluteDeviation:
    """STL removes repeating structure; MAD handles occasional spikes robustly."""

    def __init__(self, period: int = 6, threshold: float = 4.0) -> None:
        self.period = period
        self.threshold = threshold
        self.reference_median = 0.0
        self.scale = 0.0
        self.raw_median = 0.0
        self.raw_scale = 0.0
        self.fit_length = 0

    def fit(self, baseline: list[float]) -> STLMedianAbsoluteDeviation:
        if len(baseline) < 2 * self.period + 1:
            raise ValueError("STL requires at least two full seasonal periods plus one sample")
        self.fit_length = len(baseline)
        residual = STL(np.asarray(baseline), period=self.period, robust=True).fit().resid
        self.reference_median = float(np.median(residual))
        mad = float(np.median(np.abs(residual - self.reference_median)))
        # A floor avoids declaring sub-millisecond measurement jitter an incident.
        self.scale = max(1.4826 * mad, 0.01)
        self.raw_median = float(np.median(baseline))
        self.raw_scale = max(
            1.4826 * float(np.median(np.abs(np.asarray(baseline) - self.raw_median))), 0.01
        )
        return self

    def score(self, values: list[float]) -> list[float]:
        if self.scale == 0:
            raise RuntimeError("Fit the detector before scoring")
        if len(values) < 2 * self.period + 1:
            raise ValueError("Not enough samples for STL")
        scores = []
        for index, value in enumerate(values):
            level_score = abs(value - self.raw_median) / self.raw_scale
            if index < self.fit_length:
                # Scoring the baseline with a future incident in the STL fit leaks labels.
                scores.append(level_score)
                continue
            current_residual = (
                STL(np.asarray(values[: index + 1]), period=self.period, robust=True)
                .fit()
                .resid[-1]
            )
            residual_score = abs(current_residual - self.reference_median) / self.scale
            scores.append(float(max(level_score, residual_score)))
        return scores

    def flags(self, values: list[float]) -> list[bool]:
        return [bool(value >= self.threshold) for value in self.score(values)]


class MultivariateIsolationForest:
    """Train only on healthy windows, then compare future feature vectors."""

    def __init__(self, false_alarm_quantile: float = 0.99) -> None:
        self.model = IsolationForest(n_estimators=200, contamination="auto", random_state=42)
        self.quantile = false_alarm_quantile
        self.cutoff: float | None = None
        self.lower: np.ndarray | None = None
        self.upper: np.ndarray | None = None

    def fit(self, baseline: list[Observation]) -> MultivariateIsolationForest:
        if len(baseline) < 20:
            raise ValueError("At least 20 healthy observations are required")
        matrix = np.asarray([row.features() for row in baseline], dtype=float)
        self.model.fit(matrix)
        # score_samples: lower means more unusual; calibrate on healthy observations.
        anomaly_scores = -self.model.score_samples(matrix)
        self.cutoff = float(np.quantile(anomaly_scores, self.quantile))
        margin = np.maximum(0.05, 3 * np.std(matrix, axis=0))
        self.lower = np.min(matrix, axis=0) - margin
        self.upper = np.max(matrix, axis=0) + margin
        return self

    def score(self, observations: list[Observation]) -> list[float]:
        if self.cutoff is None:
            raise RuntimeError("Fit the detector before scoring")
        matrix = np.asarray([row.features() for row in observations], dtype=float)
        return (-self.model.score_samples(matrix)).tolist()

    def flags(self, observations: list[Observation]) -> list[bool]:
        scores = self.score(observations)
        assert self.cutoff is not None
        assert self.lower is not None and self.upper is not None
        matrix = np.asarray([row.features() for row in observations], dtype=float)
        range_flags = np.any((matrix < self.lower) | (matrix > self.upper), axis=1)
        return [
            score > self.cutoff or bool(outside)
            for score, outside in zip(scores, range_flags, strict=True)
        ]


def detection_metrics(labels: list[bool], predictions: list[bool]) -> dict[str, float | int]:
    if len(labels) != len(predictions):
        raise ValueError("Label and prediction lengths differ")
    tp = sum(actual and predicted for actual, predicted in zip(labels, predictions, strict=True))
    fp = sum(
        not actual and predicted for actual, predicted in zip(labels, predictions, strict=True)
    )
    fn = sum(
        actual and not predicted for actual, predicted in zip(labels, predictions, strict=True)
    )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
