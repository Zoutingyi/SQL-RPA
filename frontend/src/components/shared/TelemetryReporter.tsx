import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { reportTelemetry } from "../../api/telemetry";

export function TelemetryReporter() {
  const location = useLocation();

  useEffect(() => {
    void reportTelemetry({ event_type: "navigation", page: location.pathname }).catch(() => undefined);
  }, [location.pathname]);

  useEffect(() => {
    const onError = (event: ErrorEvent) => void reportTelemetry({
      event_type: "error", message: event.message, page: window.location.pathname,
      payload: { filename: event.filename, line: event.lineno, column: event.colno },
    }).catch(() => undefined);
    const onRejection = (event: PromiseRejectionEvent) => void reportTelemetry({
      event_type: "error", message: event.reason instanceof Error ? event.reason.message : String(event.reason),
      page: window.location.pathname, payload: { source: "unhandledrejection" },
    }).catch(() => undefined);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (navigation) void reportTelemetry({ event_type: "performance", page: window.location.pathname, duration_ms: Math.round(navigation.duration), payload: { dom_interactive_ms: Math.round(navigation.domInteractive) } }).catch(() => undefined);
    return () => { window.removeEventListener("error", onError); window.removeEventListener("unhandledrejection", onRejection); };
  }, []); // install once

  return null;
}
