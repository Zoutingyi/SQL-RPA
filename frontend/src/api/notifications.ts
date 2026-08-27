import { apiGet, apiPost, apiPut } from "./client";

export interface NotificationItem {
  id: string; event_type: string; title: string; body: string;
  payload: Record<string, unknown>; read_at: string | null; created_at: string;
}

export const listNotifications = (unreadOnly = false): Promise<{ items: NotificationItem[] }> =>
  apiGet(`/api/notifications?unread_only=${unreadOnly}`);
export const getUnreadCount = (): Promise<{ count: number }> => apiGet("/api/notifications/unread-count");
export const markNotificationRead = (id: string): Promise<{ status: string }> => apiPost(`/api/notifications/${id}/read`);
export interface NotificationEndpoint { id: string; kind: "webhook" | "email" | "im"; target: string; enabled: boolean; created_at: string; }
export interface NotificationPreference { event_type: string; channel: "in_app" | "webhook" | "email" | "im"; enabled: boolean; }
export const listNotificationEndpoints = (): Promise<{ items: NotificationEndpoint[] }> => apiGet("/api/notifications/endpoints");
export const createNotificationEndpoint = (body: { kind: string; target: string; enabled: boolean }): Promise<NotificationEndpoint> => apiPost("/api/notifications/endpoints", body);
export const testNotificationEndpoint = (id: string): Promise<{ ok: boolean; status_code: number }> => apiPost(`/api/notifications/endpoints/${id}/test`);
export const getNotificationPreferences = (): Promise<{ items: NotificationPreference[] }> => apiGet("/api/notifications/preferences");
export const setNotificationPreference = (body: NotificationPreference): Promise<NotificationPreference> => apiPut("/api/notifications/preferences", body);
