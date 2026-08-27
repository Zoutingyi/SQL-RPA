import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import { useChatStore } from "../../stores/chatStore";
import type { DisplayMessage, AgentStep } from "../../types/chat";

const SUGGESTIONS = [
  "查询数据库中所有表",
  "帮我分析表结构",
  "生成数据统计报表",
];

export function ChatPanel() {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const messages = useChatStore((s) => s.messages);
  const sseState = useChatStore((s) => s.sseState);
  const error = useChatStore((s) => s.error);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const clearError = useChatStore((s) => s.clearError);

  const isStreaming = sseState === "connecting" || sseState === "streaming";
  const hasMessages = messages.length > 0;

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  }, [input]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus textarea on mount and after streaming ends
  useEffect(() => {
    if (!isStreaming) {
      textareaRef.current?.focus();
    }
  }, [isStreaming]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    send(trimmed);
    setInput("");
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [input, isStreaming, send]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleSuggestion = useCallback(
    (text: string) => {
      if (isStreaming) return;
      send(text);
    },
    [isStreaming, send],
  );

  return (
    <div className="chat-main">
      <div className="chat-header">
        <span className="chat-header-title">SQL-RPA Agent</span>
      </div>

      {hasMessages ? (
        <div className="chat-messages" ref={messagesContainerRef}>
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {isStreaming && !messages[messages.length - 1]?.content && (
            <div className="typing-indicator" style={{ padding: "0 20px" }}>
              <div className="typing-dots">
                <span /><span /><span />
              </div>
              <span>思考中...</span>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      ) : (
        <div className="chat-empty">
          <h2>数据库 RPA 助手</h2>
          <p>基于 RAG 的智能数据库操作代理，支持自然语言查询、SQL 生成与安全审核。</p>
          <div className="suggs">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                className="sugg-chip"
                onClick={() => handleSuggestion(s)}
                disabled={isStreaming}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="chat-input-area">
        {error && (
          <div className="chat-error-bar">
            <span>{error}</span>
            <button onClick={clearError}>关闭</button>
          </div>
        )}
        <div className="chat-input-wrap">
          <textarea
            ref={textareaRef}
            className="chat-textarea"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
          />
          {isStreaming ? (
            <button className="chat-stop-btn" onClick={stop}>
              停止
            </button>
          ) : (
            <button
              className="chat-send-btn"
              disabled={!input.trim()}
              onClick={handleSend}
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: DisplayMessage }) {
  if (message.role === "system") return null;

  const isUser = message.role === "user";
  const hasThought = !!message.thought;
  const hasSteps = message.steps.length > 0;
  const degradationSteps = message.steps.filter((step) => step.type === "degradation");

  // Group steps: tool_call + tool_result pairs
  const toolPairs: Array<{ call?: AgentStep; result?: AgentStep }> = [];
  for (let i = 0; i < message.steps.length; i++) {
    const step = message.steps[i];
    if (step.type === "tool_call") {
      const pair: { call?: AgentStep; result?: AgentStep } = { call: step };
      if (i + 1 < message.steps.length && message.steps[i + 1].type === "tool_result") {
        pair.result = message.steps[i + 1];
        i++;
      }
      toolPairs.push(pair);
    }
  }

  return (
    <div className={`msg ${isUser ? "user" : "agent"}`}>
      <div className="msg-avatar">{isUser ? "U" : "AI"}</div>
      <div className="msg-body">
        {!isUser && <div className="msg-role">SQL-RPA Agent</div>}

        {hasThought && (
          <div className="msg-thought">
            <ReactMarkdown>{message.thought || ""}</ReactMarkdown>
          </div>
        )}

        {degradationSteps.map((step, index) => (
          <div className="degradation-banner" role="status" key={`${step.timestamp}-${index}`}>
            <strong>模型服务已降级</strong>
            <span>{String(step.data.message || "主模型暂不可用，已切换备用模型或受限模式。")}</span>
            {!!(step.data.fallback_model || step.data.request_id) && (
              <small>
                {step.data.fallback_model ? `备用模型：${String(step.data.fallback_model)}` : ""}
                {step.data.request_id ? ` 请求 ID：${String(step.data.request_id)}` : ""}
              </small>
            )}
          </div>
        ))}

        {toolPairs.map((pair, i) => (
          <div key={i}>
            {pair.call && (
              <div className="tool-card">
                <div className="tool-card-header">
                  <span>调用工具</span>
                  <span className="tool-card-name">
                    {(pair.call.data as Record<string, unknown>)?.tool as string || "unknown"}
                  </span>
                </div>
              </div>
            )}
            {pair.result && (
              <div className="tool-card">
                <div className="tool-card-header">
                  <span>结果</span>
                  <span style={{ color: (pair.result.data as Record<string, unknown>)?.success ? "var(--accent)" : "var(--danger)" }}>
                    {(pair.result.data as Record<string, unknown>)?.success ? "成功" : "失败"}
                  </span>
                </div>
              </div>
            )}
          </div>
        ))}

        {message.content && (
          <div className="msg-content">
            {isUser ? (
              <span style={{ whiteSpace: "pre-wrap" }}>{message.content}</span>
            ) : (
              <ReactMarkdown>{message.content}</ReactMarkdown>
            )}
          </div>
        )}

        {message.sources && message.sources.length > 0 && (
          <div className="source-row">
            {message.sources.map((s, i) => (
              <span key={i} className="source-chip" title={s.text}>
                {s.document_id ? s.document_id.slice(0, 8) + "..." : `来源 ${i + 1}`}
              </span>
            ))}
          </div>
        )}

        {message.isStreaming && !message.content && !hasSteps && (
          <div className="typing-indicator">
            <div className="typing-dots">
              <span /><span /><span />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
