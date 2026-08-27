import { create } from "zustand";
import type { DisplayMessage, AgentStep } from "../types/chat";
import type { SSEState } from "../types";
import { sendMessage } from "../api/chat";
import { listConversations, createConversation, deleteConversation, getMessages, renameConversation } from "../api/conversations";
import { formatApiError, isOrganizationRequestCancelled } from "../api/client";
import { registerOrganizationReset } from "./organizationScope";

interface ChatStore {
  messages: DisplayMessage[];
  conversations: Array<{ id: string; title: string; updated_at: string }>;
  currentConvId: string | null;
  sseState: SSEState;
  error: string | null;
  abortController: AbortController | null;
  loadingHistory: boolean;

  loadConversations: () => Promise<void>;
  newConversation: () => Promise<void>;
  switchConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  stop: () => void;
  clearError: () => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  conversations: [],
  currentConvId: null,
  sseState: "idle",
  error: null,
  abortController: null,
  loadingHistory: false,

  loadConversations: async () => {
    try {
      const convs = await listConversations();
      set({ conversations: convs });
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      /* ignore — backend may not be running */
    }
  },

  newConversation: async () => {
    try {
      const conv = await createConversation();
      set({
        conversations: [conv, ...get().conversations],
        currentConvId: conv.id,
        messages: [],
        error: null,
      });
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      const fallbackId = crypto.randomUUID();
      set({ currentConvId: fallbackId, messages: [], error: null });
    }
  },

  switchConversation: async (id: string) => {
    set({ currentConvId: id, loadingHistory: true, error: null });
    try {
      const msgs = await getMessages(id);
      const displayMsgs: DisplayMessage[] = [];

      for (const m of (msgs as Array<{
        id: string; role: string; content: string | null;
        tool_name?: string; tool_call_id?: string; tool_args?: string;
        sources?: string; created_at: string;
      }>)) {
        if (m.role === "tool") {
          const content = m.content || "";
          let resultCount = 0;
          let success = true;
          const match = content.match(/Success:\s*(\d+)\s*results?/);
          if (match) {
            resultCount = parseInt(match[1], 10);
          } else if (content.startsWith("Error:")) {
            success = false;
          }

          let args: Record<string, unknown> = {};
          if (m.tool_args) {
            try { args = JSON.parse(m.tool_args); } catch { /* ignore */ }
          }

          const toolName = m.tool_name || "unknown";

          for (let i = displayMsgs.length - 1; i >= 0; i--) {
            if (displayMsgs[i].role === "assistant") {
              displayMsgs[i].steps.push({
                type: "tool_call",
                data: { tool: toolName, args, call_id: m.tool_call_id },
                timestamp: Date.now(),
              });
              displayMsgs[i].steps.push({
                type: "tool_result",
                data: { tool: toolName, success, result_count: resultCount, reranked: false },
                timestamp: Date.now(),
              });
              break;
            }
          }
        } else {
          displayMsgs.push({
            id: m.id,
            role: m.role as DisplayMessage["role"],
            content: m.content || "",
            steps: [],
            sources: m.sources ? JSON.parse(m.sources) : undefined,
            isStreaming: false,
          });
        }
      }

      set({ messages: displayMsgs, loadingHistory: false });
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      set({ messages: [], loadingHistory: false });
    }
  },

  deleteConversation: async (id: string) => {
    try { await deleteConversation(id); } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
    }
    set((s) => {
      const convs = s.conversations.filter((c) => c.id !== id);
      if (s.currentConvId === id) {
        return { conversations: convs, currentConvId: null, messages: [] };
      }
      return { conversations: convs };
    });
  },

  renameConversation: async (id: string, title: string) => {
    try { await renameConversation(id, title); } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
    }
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      ),
    }));
  },

  send: async (text: string) => {
    const msgId = crypto.randomUUID();
    const userMsg: DisplayMessage = {
      id: msgId, role: "user", content: text, steps: [], isStreaming: false,
    };
    const assistantMsg: DisplayMessage = {
      id: crypto.randomUUID(), role: "assistant", content: "", steps: [], isStreaming: true,
    };

    const convId = get().currentConvId;
    const isNewConv = !convId;

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      sseState: "connecting",
      error: null,
    }));

    const controller = sendMessage(
      text,
      convId,
      (event) => {
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role !== "assistant") return s;

          const step: AgentStep = {
            type: event.event as AgentStep["type"],
            data: event.data as Record<string, unknown>,
            timestamp: Date.now(),
          };

          if (event.event === "thought") {
            last.thought = (last.thought || "") + ((event.data as { delta: string }).delta || "");
          }
          if (event.event === "answer_chunk") {
            last.content += (event.data as { delta: string }).delta || "";
          }
          if (event.event === "sources") {
            last.sources = event.data as Array<{ document_id: string; text: string }>;
          }
          if (event.event === "done" || event.event === "error") {
            last.isStreaming = false;
          }
          let sseError: string | null = null;
          if (event.event === "error") {
            sseError = (event.data as { message?: string }).message || "未知错误";
          }
          last.steps.push(step);
          msgs[msgs.length - 1] = { ...last };

          const newSseState: SSEState =
            event.event === "done" ? "idle"
            : event.event === "error" ? "error"
            : "streaming";

          return { messages: msgs, sseState: newSseState, abortController: null, error: sseError };
        });
      },
      (err) => {
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role === "assistant") {
            last.isStreaming = false;
            msgs[msgs.length - 1] = { ...last };
          }
          return {
            messages: msgs,
            sseState: "error",
            error: formatApiError(err, "连接失败"),
            abortController: null,
          };
        });
      },
      () => {
        set((s) => {
          if (s.sseState === "error") return s;
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role === "assistant") {
            last.isStreaming = false;
            msgs[msgs.length - 1] = { ...last };
          }
          return { messages: msgs, sseState: "idle", abortController: null };
        });
      },
      (newConvId) => {
        if (isNewConv) {
          const title = text.length > 40 ? text.slice(0, 40) + "..." : text;
          set((s) => ({
            currentConvId: newConvId,
            conversations: [
              { id: newConvId, title, updated_at: new Date().toISOString() },
              ...s.conversations,
            ],
          }));
        }
      },
    );

    set({ abortController: controller });
  },

  stop: () => {
    get().abortController?.abort();
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last.role === "assistant") {
        last.isStreaming = false;
        msgs[msgs.length - 1] = { ...last };
      }
      return { messages: msgs, sseState: "idle", abortController: null, error: null };
    });
  },

  clearError: () => set({ error: null }),
}));

registerOrganizationReset("chat", () => {
  useChatStore.getState().abortController?.abort();
  useChatStore.setState({
    messages: [], conversations: [], currentConvId: null, sseState: "idle",
    error: null, abortController: null, loadingHistory: false,
  });
});
