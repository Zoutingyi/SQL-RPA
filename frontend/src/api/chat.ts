import type { SSEEvent } from "../types";
import { ApiError, createRequestId, trackedFetch } from "./client";

export function sendMessage(
  message: string,
  conversationId: string | null,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onDone: () => void,
  onConvId?: (id: string) => void,
): AbortController {
  const requestId = createRequestId();

  const request = trackedFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  request.promise
    .then(async (response) => {
      if (!response.ok) {
        let detail = `聊天请求失败 (${response.status})`;
        try {
          const body = JSON.parse(await response.text());
          detail = typeof body.detail === "string" ? body.detail : body.detail?.message || detail;
        } catch { /* keep fallback */ }
        throw new ApiError(response.status, detail, response.headers.get("X-Request-ID") || requestId);
      }
      const convId = response.headers.get("X-Conversation-Id");
      if (convId && request.isCurrentContext()) onConvId?.(convId);
      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (!request.isCurrentContext()) return;
              onEvent({ event: eventType, data });
            } catch {
              // skip partial
            }
          }
        }
      }
    })
    .catch((err) => {
      if (!request.controller.signal.aborted && request.isCurrentContext()) {
        onError(err instanceof Error ? err : new Error(String(err)));
      }
    })
    .finally(() => {
      const shouldNotify = request.isCurrentContext();
      request.complete();
      if (shouldNotify) onDone();
    });

  return request.controller;
}
