"""OpenTelemetry tracing — single instrumentation surface, fanned out to two
OTLP backends: Jaeger (infra spans + latency) and Langfuse (LLM generations).

Fully env-gated and a complete no-op when both backends are disabled, so normal
runs are unaffected and the OTel deps are imported lazily (only required when
tracing is actually turned on).

Why OpenInference rather than a Langfuse LangChain callback: the LLM is called
through the RAW `openai` SDK (`client.chat.completions.create`) inside the
LangGraph nodes — not via LangChain `ChatOpenAI` — so a LangChain callback would
miss the generations. `OpenAIInstrumentor` patches the openai SDK directly and
captures every call; `LangChainInstrumentor` adds the LangGraph node spans on
top. Both Jaeger and Langfuse ingest the same OpenInference spans over OTLP.

Env:
    OTEL_ENABLED=true                 → export to Jaeger
    OTEL_EXPORTER_OTLP_ENDPOINT       → Jaeger OTLP base (default http://localhost:4318)
    OTEL_SERVICE_NAME                 → service.name (default mee-meeting-agent)
    LANGFUSE_ENABLED=true             → export to Langfuse
    LANGFUSE_HOST                     → Langfuse base (default http://localhost:3000)
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY  → Langfuse project keys (Basic auth)
"""

import base64
import logging
import os

log = logging.getLogger(__name__)

_initialized = False


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def init_tracing(app=None) -> None:
    """Initialise the global TracerProvider with up to two OTLP exporters and
    instrument openai / LangChain / FastAPI. Idempotent; called from the FastAPI
    lifespan. No-op (and imports nothing) when both backends are disabled.
    """
    global _initialized
    if _initialized:
        return

    jaeger = _truthy("OTEL_ENABLED")
    langfuse = _truthy("LANGFUSE_ENABLED")
    if not (jaeger or langfuse):
        return  # tracing off → zero overhead, no deps required

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        log.warning("tracing: OpenTelemetry deps missing (%s); tracing disabled", e)
        return

    service = os.getenv("OTEL_SERVICE_NAME", "mee-meeting-agent")
    provider = TracerProvider(resource=Resource.create({"service.name": service}))

    if jaeger:
        base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        endpoint = base.rstrip("/") + "/v1/traces"
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        log.info("tracing: Jaeger OTLP exporter → %s", endpoint)

    if langfuse:
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        sk = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        if not (pk and sk):
            log.warning(
                "tracing: LANGFUSE_ENABLED but LANGFUSE_PUBLIC_KEY/SECRET_KEY missing; "
                "skipping Langfuse exporter"
            )
        else:
            host = os.getenv("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
            auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=f"{host}/api/public/otel/v1/traces",
                        headers={"Authorization": f"Basic {auth}"},
                    )
                )
            )
            log.info("tracing: Langfuse OTLP exporter → %s", host)

    trace.set_tracer_provider(provider)
    _instrument(app)
    _initialized = True


def _instrument(app=None) -> None:
    """Attach the instrumentors. Each is best-effort: a missing package logs a
    warning instead of breaking startup."""
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
    except ImportError:
        log.warning("tracing: openinference-instrumentation-openai missing")

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument()
    except ImportError:
        log.warning("tracing: openinference-instrumentation-langchain missing")

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            log.warning("tracing: opentelemetry-instrumentation-fastapi missing")


def shutdown_tracing() -> None:
    """Flush + shut down span processors on app shutdown. Safe when uninitialised."""
    global _initialized
    if not _initialized:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception as e:  # noqa: BLE001 — shutdown must never raise
        log.warning("tracing: shutdown error: %s", e)
    _initialized = False
