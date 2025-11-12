import logging
import time
from collections import Counter

log = logging.getLogger(__name__)
# Ensure INFO messages surface during tests and default logging
if log.level == logging.NOTSET:
    log.setLevel(logging.INFO)
log.propagate = True
_counters = Counter()


def inc(name: str, **labels):
    key = (name, tuple(sorted(labels.items())))
    _counters[key] += 1


def dump():
    # simple log-based metrics; integrate with Prometheus client if needed
    for (name, labels), val in _counters.items():
        lbl = ", ".join(f"{k}={v}" for k, v in labels)
        log.info(f"metric {name}{{{lbl}}} {val} ts={int(time.time())}")
