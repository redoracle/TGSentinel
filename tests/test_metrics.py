"""Unit tests for metrics module."""

import time

import pytest

from tgsentinel.metrics import _counters, dump, inc


@pytest.mark.unit
class TestMetricsInc:
    """Test metrics increment functionality."""

    def test_inc_basic(self):
        """Test basic increment."""
        _counters.clear()

        inc("test_metric")

        assert len(_counters) == 1
        key = ("test_metric", tuple())
        assert key in _counters
        assert _counters[key] == 1

    def test_inc_multiple_times(self):
        """Test incrementing the same metric multiple times."""
        _counters.clear()

        inc("test_metric")
        inc("test_metric")
        inc("test_metric")

        key = ("test_metric", tuple())
        assert _counters[key] == 3

    def test_inc_with_labels(self):
        """Test incrementing with labels."""
        _counters.clear()

        inc("test_metric", status="success", code=200)

        key = ("test_metric", (("code", 200), ("status", "success")))
        assert key in _counters
        assert _counters[key] == 1

    def test_inc_different_labels_different_keys(self):
        """Test that different labels create different keys."""
        _counters.clear()

        inc("test_metric", status="success")
        inc("test_metric", status="error")

        assert len(_counters) == 2

    def test_inc_labels_sorted(self):
        """Test that labels are sorted consistently."""
        _counters.clear()

        inc("test_metric", b="2", a="1")
        inc("test_metric", a="1", b="2")

        # Both should increment the same counter
        assert len(_counters) == 1
        key = ("test_metric", (("a", "1"), ("b", "2")))
        assert _counters[key] == 2

    def test_inc_multiple_metrics(self):
        """Test incrementing multiple different metrics."""
        _counters.clear()

        inc("metric1")
        inc("metric2")
        inc("metric3", label="value")

        assert len(_counters) == 3

    def test_inc_with_boolean_labels(self):
        """Test incrementing with boolean labels."""
        _counters.clear()

        inc("test_metric", important=True)
        inc("test_metric", important=False)

        assert len(_counters) == 2

    def test_inc_with_numeric_labels(self):
        """Test incrementing with numeric labels."""
        _counters.clear()

        inc("test_metric", chat=12345)

        key = ("test_metric", (("chat", 12345),))
        assert key in _counters


class TestMetricsDump:
    """Test metrics dump functionality."""

    def test_dump_empty_counters(self, caplog):
        """Test dumping when there are no counters."""
        _counters.clear()

        dump()

        # Should not log anything
        assert "metric" not in caplog.text

    def test_dump_basic_metric(self, caplog):
        """Test dumping a basic metric."""
        _counters.clear()
        inc("test_metric")

        dump()

        assert "metric test_metric" in caplog.text
        assert "1" in caplog.text
        assert "ts=" in caplog.text

    def test_dump_metric_with_labels(self, caplog):
        """Test dumping a metric with labels."""
        _counters.clear()
        inc("test_metric", status="success", code=200)

        dump()

        log_text = caplog.text
        assert "metric test_metric" in log_text
        assert "status=success" in log_text
        assert "code=200" in log_text

    def test_dump_multiple_metrics(self, caplog):
        """Test dumping multiple metrics."""
        _counters.clear()
        inc("metric1")
        inc("metric2", label="value")
        inc("metric3")

        dump()

        log_text = caplog.text
        assert "metric metric1" in log_text
        assert "metric metric2" in log_text
        assert "metric metric3" in log_text

    def test_dump_includes_timestamp(self, caplog):
        """Test that dump includes a timestamp."""
        _counters.clear()
        inc("test_metric")

        before = int(time.time())
        dump()
        after = int(time.time())

        log_text = caplog.text
        assert "ts=" in log_text

        # Extract timestamp from log
        for line in log_text.split("\n"):
            if "ts=" in line:
                ts_str = line.split("ts=")[1].strip()
                ts = int(ts_str)
                assert before <= ts <= after

    def test_dump_format_prometheus_style(self, caplog):
        """Test that dump formats metrics in Prometheus-like style."""
        _counters.clear()
        inc("http_requests_total", method="GET", status="200")

        dump()

        log_text = caplog.text
        # Should look like: metric http_requests_total{method=GET, status=200} 1 ts=...
        assert "metric http_requests_total" in log_text
        assert "method=GET" in log_text
        assert "status=200" in log_text

    def test_dump_preserves_counter_values(self):
        """Test that dump doesn't reset counter values."""
        _counters.clear()
        inc("test_metric")
        inc("test_metric")

        dump()

        # Counter should still be 2 after dump
        key = ("test_metric", tuple())
        assert _counters[key] == 2

    def test_dump_multiple_times(self, caplog):
        """Test calling dump multiple times."""
        _counters.clear()
        inc("test_metric")

        dump()
        dump()
        dump()

        # Each dump should log the metric
        log_lines = [
            line for line in caplog.text.split("\n") if "metric test_metric" in line
        ]
        assert len(log_lines) == 3


class TestMetricsIntegration:
    """Integration tests for metrics module."""

    def test_realistic_usage_pattern(self, caplog):
        """Test realistic usage pattern."""
        _counters.clear()

        # Simulate processing messages
        inc("messages_processed", important=True)
        inc("messages_processed", important=False)
        inc("messages_processed", important=False)
        inc("alerts_sent", mode="dm")
        inc("errors", module="worker")

        dump()

        log_text = caplog.text

        # Check all metrics are logged
        assert "messages_processed" in log_text
        assert "alerts_sent" in log_text
        assert "errors" in log_text

        # Check specific counter values
        assert (
            len(_counters) == 4
        )  # 2 for messages_processed, 1 for alerts_sent, 1 for errors

    def test_concurrent_increments(self):
        """Test that multiple increments are correctly accumulated."""
        _counters.clear()

        # Simulate multiple workers
        for _ in range(10):
            inc("processed_total", worker="1")

        for _ in range(15):
            inc("processed_total", worker="2")

        key1 = ("processed_total", (("worker", "1"),))
        key2 = ("processed_total", (("worker", "2"),))

        assert _counters[key1] == 10
        assert _counters[key2] == 15
