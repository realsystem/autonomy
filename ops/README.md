# ops

Deployment pieces that are not code.

- `hub/` — the hub's telemetry stack (M9, C17): an OpenTelemetry Collector that takes OTLP from
  components and the OTLP/JSON span files written by `autonomy_core.telemetry.Tracer`, redacts
  secret-shaped attributes, and sends traces to Jaeger (kept 7 days), metrics to Prometheus and
  everything to a local archive. Start with `docker compose -f ops/hub/docker-compose.yml up -d`;
  it reads `/var/lib/autonomy/<component>/traces.jsonl` (the `Tracer(path=...)` file per component).
- `sops.yaml.example` — `.sops.yaml` for repositories that keep encrypted config (ADR-013, C18).
  CI checks such files with `autonomy secrets check-sops`.
