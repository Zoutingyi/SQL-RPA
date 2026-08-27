import { apiPost } from "./client";

export interface TelemetryEvent {
  event_type: "error" | "performance" | "navigation";
  message?: string; page?: string; request_id?: string;
  duration_ms?: number; payload?: Record<string, unknown>;
}

export function reportTelemetry(event: TelemetryEvent): Promise<{ accepted: boolean; id: string }> {
  return apiPost("/api/telemetry", event);
}
