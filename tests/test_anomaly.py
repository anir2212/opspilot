from datetime import UTC, datetime, timedelta

import pytest

from opspilot.anomaly import (
    MultivariateIsolationForest,
    Observation,
    STLMedianAbsoluteDeviation,
    detection_metrics,
)
from opspilot.correlate import EvidenceEvent, correlate


def test_stl_mad_flags_large_latency_change() -> None:
    baseline = [0.1 + (index % 3) * 0.002 for index in range(30)]
    detector = STLMedianAbsoluteDeviation(period=6).fit(baseline)
    scores = detector.score(baseline + [2.4, 2.5, 2.3])
    assert max(scores[-3:]) > detector.threshold


def test_stl_does_not_look_ahead_into_future_fault() -> None:
    baseline = [0.0] * 16
    flags = STLMedianAbsoluteDeviation().fit(baseline).flags(baseline + [1.0] * 6)
    assert flags[:16] == [False] * 16
    assert any(flags[16:])


def test_iforest_flags_obvious_multivariate_shift() -> None:
    baseline = [
        Observation(str(index), "gateway", 0.1 + (index % 5) / 100, 0.0, 0.2, 0.3)
        for index in range(50)
    ]
    detector = MultivariateIsolationForest().fit(baseline)
    anomalous = Observation("later", "gateway", 2.4, 0.9, 0.95, 0.9, 2)
    assert detector.flags([anomalous]) == [True]


def test_metrics_zero_denominators_and_length_validation() -> None:
    assert detection_metrics([False], [False])["f1"] == 0.0
    with pytest.raises(ValueError):
        detection_metrics([True], [])


def test_correlation_favors_first_failing_upstream_service() -> None:
    now = datetime.now(UTC)
    payment = EvidenceEvent("a1", "payments", "anomaly", now, "prom-payment")
    gateway = EvidenceEvent("a2", "gateway", "anomaly", now + timedelta(seconds=20), "trace-1")
    unrelated = EvidenceEvent("a3", "backups", "anomaly", now + timedelta(hours=1), "backup-7")
    result = correlate(gateway, [payment, gateway, unrelated])
    assert result["services"][0]["service"] == "payments"
    assert result["event_ids"] == ["a1", "a2"]
