import { useCallback, useRef } from "react";
import { trackedFetch } from "../api/client";

export function useSSE() {
  const controllerRef = useRef<AbortController | null>(null);

  const connect = useCallback((url: string, onEvent: (event: unknown) => void, onError?: (err: Error) => void) => {
    controllerRef.current?.abort();
    const request = trackedFetch(url);
    const controller = request.controller;
    controllerRef.current = controller;

    request.promise
      .then(async (response) => {
        if (!response.ok) throw new Error(`SSE ${response.status}`);
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
                if (!request.isCurrentContext()) return;
                onEvent({ event: eventType, data: JSON.parse(line.slice(6)) });
              } catch { /* skip */ }
            }
          }
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted && request.isCurrentContext()) {
          onError?.(err instanceof Error ? err : new Error(String(err)));
        }
      })
      .finally(request.complete);

    return controller;
  }, []);

  const disconnect = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  return { connect, disconnect };
}
