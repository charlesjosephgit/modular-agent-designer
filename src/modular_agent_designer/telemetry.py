"""Optional MLflow tracing setup (via OpenTelemetry) — only imported when --mlflow is passed."""
from __future__ import annotations


def setup_tracing(experiment_id: str = "0") -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    exporter = OTLPSpanExporter(headers={"x-mlflow-experiment-id": experiment_id})
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
