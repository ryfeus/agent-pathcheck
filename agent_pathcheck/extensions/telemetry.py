"""Adapt optional OpenTelemetry tracers without depending on their SDK."""

def opentelemetry_exporter(tracer):
    """Accept an OTel tracer, including one configured by Logfire; no dependency."""
    def export(event):
        item = tracer.start_span(event["name"], start_time=event["start_time"],
                                 attributes={"policy.kind": event["kind"], "policy.status": event["status"]})
        item.end(end_time=event["end_time"])
    return export
