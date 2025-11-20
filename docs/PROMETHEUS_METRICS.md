# Prometheus Metrics Integration

TG Sentinel now includes full Prometheus metrics support for monitoring and observability.

## Quick Start

### 1. Access the Metrics Endpoint

Metrics are exposed at: `http://localhost:8080/metrics`

```bash
curl http://localhost:8080/metrics
```

### 2. Configure Prometheus

Add this to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: "tgsentinel"
    static_configs:
      - targets: ["sentinel:8080"]
    metrics_path: "/metrics"
    scrape_interval: 15s
```

### 3. Add to Docker Compose (Optional)

```yaml
prometheus:
  image: prom/prometheus:latest
  ports:
    - "9090:9090"
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus_data:/prometheus
  networks:
    - tgsentinel_net
```

## Available Metrics

### Message Processing

- `tgsentinel_messages_ingested_total` (counter) - Total messages ingested from Telegram

  - Labels: `chat_id`, `chat_name`

- `tgsentinel_messages_processed_total` (counter) - Total messages processed

  - Labels: `status` (success, error, filtered)

- `tgsentinel_message_score` (histogram) - Distribution of importance scores

### Alerts

- `tgsentinel_alerts_generated_total` (counter) - Total alerts generated

  - Labels: `channel`, `trigger_type`

- `tgsentinel_alerts_sent_total` (counter) - Alerts successfully sent
  - Labels: `destination` (dm, channel)

### Database Operations

- `tgsentinel_db_messages_current` (gauge) - Current message count in database

- `tgsentinel_db_cleanup_seconds` (histogram) - Database cleanup duration

- `tgsentinel_db_vacuum_seconds` (histogram) - VACUUM operation duration

- `tgsentinel_db_messages_deleted_total` (counter) - Messages deleted by cleanup
  - Labels: `reason` (age, count_limit)

### Worker Health

- `tgsentinel_worker_authorized` (gauge) - Worker authorization status (1=yes, 0=no)

- `tgsentinel_worker_connected` (gauge) - Worker connection status (1=yes, 0=no)

- `tgsentinel_redis_stream_depth` (gauge) - Redis message stream depth

### API Performance

- `tgsentinel_api_requests_total` (counter) - Total API requests

  - Labels: `method`, `endpoint`, `status_code`

- `tgsentinel_api_request_seconds` (histogram) - API request duration
  - Labels: `method`, `endpoint`

### Semantic Processing

- `tgsentinel_semantic_inference_seconds` (histogram) - Inference time for semantic model

### User Feedback

- `tgsentinel_feedback_submitted_total` (counter) - Feedback submissions
  - Labels: `label` (1=positive, 0=negative)

## Example Prometheus Queries

### Messages per Minute

```promql
rate(tgsentinel_messages_ingested_total[1m])
```

### Alert Rate by Channel

```promql
rate(tgsentinel_alerts_generated_total[5m]) by (channel)
```

### Database Size Trend

```promql
tgsentinel_db_messages_current
```

### Worker Health Check

```promql
tgsentinel_worker_authorized * tgsentinel_worker_connected
```

### API Error Rate

```promql
rate(tgsentinel_api_requests_total{status_code=~"5.."}[5m])
```

### P95 Semantic Latency

```promql
histogram_quantile(0.95, rate(tgsentinel_semantic_inference_seconds_bucket[5m]))
```

## Grafana Dashboard

### Recommended Panels

1. **Message Throughput**

   - Graph: `rate(tgsentinel_messages_processed_total[5m])`

2. **Alert Rate**

   - Graph: `rate(tgsentinel_alerts_sent_total[5m])`

3. **Database Size**

   - Gauge: `tgsentinel_db_messages_current`

4. **Worker Status**

   - Stat: `tgsentinel_worker_authorized`
   - Stat: `tgsentinel_worker_connected`

5. **API Performance**

   - Heatmap: `tgsentinel_api_request_seconds`

6. **Semantic Inference Latency**
   - Graph: `histogram_quantile(0.95, rate(tgsentinel_semantic_inference_seconds_bucket[5m]))`

## Testing

Run the test script to verify the metrics endpoint:

```bash
./tools/test_prometheus_metrics.sh
```

## Backward Compatibility

The legacy `inc()` and `dump()` functions are maintained for backward compatibility:

```python
from tgsentinel.metrics import inc

# These still work and map to Prometheus metrics
inc("messages_processed", status="success")
inc("alerts_sent", destination="dm")
```

## Integration with Existing Code

Metrics are automatically updated at various points:

- Message ingestion → `messages_ingested_total`
- Alert generation → `alerts_generated_total`, `alerts_sent_total`
- Database cleanup → `db_messages_deleted_total`, `db_cleanup_seconds`
- API requests → `api_requests_total`, `api_request_seconds`

The `/metrics` endpoint dynamically updates gauges before export:

- Worker authorization/connection status
- Current database message count
- Redis stream depth

## Troubleshooting

### Metrics not appearing?

1. Check the endpoint is accessible:

   ```bash
   curl http://localhost:8080/metrics
   ```

2. Verify prometheus-client is installed:

   ```bash
   pip list | grep prometheus-client
   ```

3. Check Sentinel logs for errors:
   ```bash
   docker compose logs sentinel | grep -i metric
   ```

### Stale metrics?

Metrics are updated:

- Counters: On each event
- Gauges: On each `/metrics` scrape
- Histograms: On each observation

Some gauges (like `db_messages_current`) are only accurate at scrape time.

## Security Considerations

- The `/metrics` endpoint is unauthenticated by design (Prometheus standard)
- Do not expose port 8080 publicly
- Use internal networks or VPN for Prometheus scraping
- Metrics do not contain sensitive data (no message content, only counts)

## Performance Impact

- **Minimal**: Prometheus metrics are in-memory counters
- **Storage**: ~10KB per 1000 time series
- **CPU**: <1% overhead for typical workloads
- **Scrape time**: <100ms for full export

## Migration from Old Metrics

The old log-based metrics system has been replaced. If you were parsing logs for metrics:

**Old (deprecated):**

```
metric messages_ingested{chat_id=123} 1 ts=1700000000
```

**New (Prometheus):**

```
tgsentinel_messages_ingested_total{chat_id="123",chat_name="test"} 1
```

Update your monitoring scripts to use the `/metrics` endpoint instead of parsing logs.
