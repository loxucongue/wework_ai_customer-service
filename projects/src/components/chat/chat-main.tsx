"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { Activity, Bot, Sparkles, Settings, UserRoundX } from "lucide-react";
import Link from "next/link";
import { ChatSidebar } from "./chat-sidebar";
import { ChatInput } from "./chat-input";
import { MessageBubble } from "./message-bubble";
import type { Conversation, ChatMessage, WorkflowResponse } from "@/types/chat";

const STORAGE_KEY = "chat_conversations";
const TEST_CONVERSATIONS_URL = "/test-conversations.json";
const TEST_CONVERSATION_PREFIX = "codex_test_";
const TEST_CONVERSATION_TITLE = "Codex测试-";
const LEGACY_TEST_CONVERSATION_PREFIX = "frontend_visible_test_";
const LEGACY_TEST_CONVERSATION_TITLE = "Codex可视化回归测试";

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
      } else {
        reject(new Error("Failed to read image file"));
      }
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read image file"));
    reader.readAsDataURL(file);
  });
}

function loadConversations(): Conversation[] {
  if (typeof window === "undefined") return [];
  try {
    const data = localStorage.getItem(STORAGE_KEY);
    return data ? JSON.parse(data) : [];
  } catch {
    return [];
  }
}

function saveConversations(conversations: Conversation[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  } catch {
    // Storage full or unavailable
  }
}

function mergeConversations(
  current: Conversation[],
  incoming: Conversation[]
): Conversation[] {
  const persistent = current.filter(
    (item) =>
      !item.id.startsWith(TEST_CONVERSATION_PREFIX) &&
      !item.id.startsWith(LEGACY_TEST_CONVERSATION_PREFIX) &&
      !item.title.startsWith(TEST_CONVERSATION_TITLE) &&
      !item.title.startsWith(LEGACY_TEST_CONVERSATION_TITLE)
  );
  const seen = new Set(persistent.map((item) => item.id));
  const fresh = incoming.filter((item) => item.id && !seen.has(item.id));
  if (fresh.length === 0) return current;
  return [...fresh, ...persistent].sort((a, b) => b.updatedAt - a.updatedAt);
}

function findNewestIncomingConversation(
  current: Conversation[],
  incoming: Conversation[]
): Conversation | null {
  const existingIds = new Set(current.map((item) => item.id));
  const fresh = incoming
    .filter((item) => item.id && !existingIds.has(item.id))
    .sort((a, b) => b.updatedAt - a.updatedAt);
  return fresh[0] ?? null;
}

export function ChatMain() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isClearingMemory, setIsClearingMemory] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mounted, setMounted] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load from localStorage after mount
  useEffect(() => {
    const saved = loadConversations();
    setConversations(saved);
    if (saved.length > 0) {
      setActiveId(saved[0].id);
    }
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || typeof window === "undefined") return;
    if (!["localhost", "127.0.0.1"].includes(window.location.hostname)) return;

    let cancelled = false;
    fetch(`${TEST_CONVERSATIONS_URL}?t=${Date.now()}`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (cancelled || !payload || !Array.isArray(payload.conversations)) {
          return;
        }
        const incoming = payload.conversations as Conversation[];
        if (incoming.length === 0) return;
        setConversations((prev) => {
          const newestIncoming = findNewestIncomingConversation(prev, incoming);
          const merged = mergeConversations(prev, incoming);
          if (newestIncoming) {
            setActiveId(newestIncoming.id);
          }
          return merged;
        });
      })
      .catch(() => {
        // Local test seed is optional.
      });

    return () => {
      cancelled = true;
    };
  }, [mounted]);

  // Save to localStorage when conversations change
  useEffect(() => {
    if (mounted) {
      saveConversations(conversations);
    }
  }, [conversations, mounted]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [conversations, activeId]);

  const activeConversation = conversations.find((c) => c.id === activeId);

  const createNewConversation = useCallback(() => {
    const newConv: Conversation = {
      id: generateId(),
      title: "新对话",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    setConversations((prev) => [newConv, ...prev]);
    setActiveId(newConv.id);
  }, []);

  const deleteConversation = useCallback(
    (id: string) => {
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeId === id) {
        setActiveId(null);
      }
    },
    [activeId]
  );

  const clearActiveMemory = useCallback(async () => {
    if (!activeConversation || isClearingMemory) return;
    const ok = window.confirm(
      "确认清空当前测试客户的画像和历史事件吗？当前页面里的对话消息不会删除。"
    );
    if (!ok) return;

    setIsClearingMemory(true);
    try {
      const response = await fetch("/api/memory/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_id: activeConversation.id }),
      });
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      const notice: ChatMessage = {
        id: generateId(),
        role: "assistant",
        content: "当前测试客户的画像和历史事件已清空，后续回复不会再读取这部分旧记忆。",
        timestamp: Date.now(),
      };
      setConversations((prev) =>
        prev.map((item) =>
          item.id === activeConversation.id
            ? {
                ...item,
                messages: [...item.messages, notice],
                updatedAt: Date.now(),
              }
            : item
        )
      );
    } catch (error) {
      console.error("Failed to clear memory:", error);
      const notice: ChatMessage = {
        id: generateId(),
        role: "assistant",
        content: "画像清理失败，可以稍后再试，或新建一个对话继续测试。",
        timestamp: Date.now(),
      };
      setConversations((prev) =>
        prev.map((item) =>
          item.id === activeConversation.id
            ? {
                ...item,
                messages: [...item.messages, notice],
                updatedAt: Date.now(),
              }
            : item
        )
      );
    } finally {
      setIsClearingMemory(false);
    }
  }, [activeConversation, isClearingMemory]);

  const sendMessage = useCallback(
    async (content: string, imageFile?: File) => {
      if ((!content.trim() && !imageFile) || isLoading) return;

      let currentId = activeId;

      // If no active conversation, create one
      if (!currentId) {
        const newConv: Conversation = {
          id: generateId(),
          title: content.slice(0, 30) + (content.length > 30 ? "..." : ""),
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        currentId = newConv.id;
        setConversations((prev) => [newConv, ...prev]);
        setActiveId(currentId);
      }

      // Upload image if present
      let imageUrl: string | undefined;
      if (imageFile) {
        try {
          const formData = new FormData();
          formData.append("file", imageFile);
          const uploadRes = await fetch("/api/upload", {
            method: "POST",
            body: formData,
          });
          const uploadData = await uploadRes.json().catch(() => ({}));
          if (!uploadRes.ok) {
            const detail =
              typeof uploadData.error === "string"
                ? uploadData.error
                : `HTTP ${uploadRes.status}`;
            throw new Error(detail);
          }
          if (typeof uploadData.url === "string" && uploadData.url) {
            imageUrl = uploadData.url;
          } else {
            throw new Error("Upload succeeded but no image URL was returned");
          }
        } catch (err) {
          console.warn("Image upload failed, falling back to data URL:", err);
          try {
            imageUrl = await fileToDataUrl(imageFile);
          } catch (readError) {
            console.error("Image fallback failed:", readError);
            const errorMessage: ChatMessage = {
              id: generateId(),
              role: "assistant",
              content:
                "图片没有读取成功，我先不发送这条消息。可以重新选择图片，或先用文字描述一下。",
              timestamp: Date.now(),
            };
            setConversations((prev) =>
              prev.map((c) => {
                if (c.id !== currentId) return c;
                return {
                  ...c,
                  messages: [...c.messages, errorMessage],
                  updatedAt: Date.now(),
                };
              })
            );
            return;
          }
        }
      }

      // Add user message
      const userMessage: ChatMessage = {
        id: generateId(),
        role: "user",
        content,
        timestamp: Date.now(),
        imageUrl,
      };

      setConversations((prev) =>
        prev.map((c) => {
          if (c.id !== currentId) return c;
          const isFirst = c.messages.length === 0;
          return {
            ...c,
            title: isFirst
              ? content.slice(0, 30) + (content.length > 30 ? "..." : "")
              : c.title,
            messages: [...c.messages, userMessage],
            updatedAt: Date.now(),
          };
        })
      );

      setIsLoading(true);
      const startTime = Date.now();

      try {
        // Build conversation history for the workflow (last 10 messages)
        const conv = conversations.find((c) => c.id === currentId);
        const historyMessages = conv
          ? conv.messages
              .slice(-10)
              .map((m) => `${m.role === "user" ? "用户" : "助手"}: ${m.content}`)
          : [];

        const requestBody: Record<string, unknown> = {
          content,
          customer_id: currentId,
          conversation_history: historyMessages,
        };
        if (imageUrl) {
          requestBody.file_image = imageUrl;
        }

        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody),
        });

        if (!response.ok) {
          throw new Error(`API error: ${response.status}`);
        }

        const result = await response.json();

        // Check workflow-level error
        if (result.code && result.code !== 0) {
          const errorMsg = result.msg || `工作流错误 (code: ${result.code})`;
          console.error("[ChatMain] Workflow error:", result);
          throw new Error(errorMsg);
        }

        // Parse workflow response
        // The Coze API returns: { code: 0, data: "..." } where data is a JSON string
        let workflowOutput: WorkflowResponse = {};

        if (result.code === 0 && result.data) {
          // data might be a string that needs parsing
          const dataStr =
            typeof result.data === "string"
              ? result.data
              : JSON.stringify(result.data);
          try {
            workflowOutput = JSON.parse(dataStr);
          } catch {
            workflowOutput = typeof result.data === "object" ? result.data : {};
          }
        } else if (result.output || result.intent || result.scene) {
          // Direct format
          workflowOutput = result;
        }

        // Extract text content from output items — each item becomes a separate message bubble
        const elapsed = Date.now() - startTime;
        const assistantMessages: ChatMessage[] = [];
        if (workflowOutput.output && Array.isArray(workflowOutput.output)) {
          const sorted = [...workflowOutput.output].sort(
            (a, b) => a.order - b.order
          );
          const meta: ChatMessage["meta"] = {};
          if (workflowOutput.intent) meta.intent = workflowOutput.intent;
          if (workflowOutput.scene) meta.scene = workflowOutput.scene;
          if (workflowOutput.subflow) meta.subflow = workflowOutput.subflow;
          if (workflowOutput.request_id) meta.requestId = workflowOutput.request_id;
          if (workflowOutput.trace_url) meta.traceUrl = workflowOutput.trace_url;

          const debugMeta = workflowOutput.meta || {};
          if (Array.isArray(debugMeta.tool_result_keys)) {
            meta.toolResultKeys = debugMeta.tool_result_keys.filter(
              (item): item is string => typeof item === "string"
            );
          }
          if (Array.isArray(debugMeta.tool_calls)) {
            meta.toolCalls = debugMeta.tool_calls;
          }
          if ("profile_update" in debugMeta) {
            meta.profileUpdate = debugMeta.profile_update;
          }
          if (Array.isArray(debugMeta.event_updates)) {
            meta.eventUpdates = debugMeta.event_updates;
          }
          if ("image_info" in debugMeta) {
            meta.imageInfo = debugMeta.image_info;
          }
          if (typeof debugMeta.memory_loaded === "boolean") {
            meta.memoryLoaded = debugMeta.memory_loaded;
          }
          if (Object.keys(debugMeta).length > 0) {
            meta.raw = debugMeta;
          }
          // Only attach meta to the first bubble
          for (let i = 0; i < sorted.length; i++) {
            const item = sorted[i];
            if (!item.content) continue;
            assistantMessages.push({
              id: generateId(),
              role: "assistant",
              content: item.content,
              contentType: (item.type as "text" | "image") || "text",
              timestamp: Date.now(),
              duration: elapsed,
              meta: i === 0 && Object.keys(meta).length > 0 ? meta : undefined,
            });
          }
        }

        if (assistantMessages.length === 0) {
          assistantMessages.push({
            id: generateId(),
            role: "assistant",
            content: "抱歉，未能获取到回复内容。",
            timestamp: Date.now(),
            duration: elapsed,
          });
        }

        setConversations((prev) =>
          prev.map((c) => {
            if (c.id !== currentId) return c;
            return {
              ...c,
              messages: [...c.messages, ...assistantMessages],
              updatedAt: Date.now(),
            };
          })
        );
      } catch (error) {
        console.error("Failed to send message:", error);
        const errorMessage: ChatMessage = {
          id: generateId(),
          role: "assistant",
          content: "抱歉，发送消息时出现了错误，请稍后重试。",
          timestamp: Date.now(),
          duration: Date.now() - startTime,
        };
        setConversations((prev) =>
          prev.map((c) => {
            if (c.id !== currentId) return c;
            return {
              ...c,
              messages: [...c.messages, errorMessage],
              updatedAt: Date.now(),
            };
          })
        );
      } finally {
        setIsLoading(false);
      }
    },
    [activeId, conversations, isLoading]
  );

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <ChatSidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={createNewConversation}
        onDelete={deleteConversation}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
      />

      {/* Main chat area */}
      <div className="flex flex-1 flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b px-6 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <Bot className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-sm font-semibold">AI 智能助手</h1>
            <p className="text-xs text-muted-foreground">
              {isLoading ? "正在思考中..." : "在线"}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              disabled={!activeConversation || isLoading || isClearingMemory}
              onClick={clearActiveMemory}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >
              <UserRoundX className="h-3.5 w-3.5" />
              {isClearingMemory ? "清理中" : "清空画像"}
            </button>
            <Link href="/logs">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                <Activity className="h-3.5 w-3.5" />
                日志
              </button>
            </Link>
            <Link href="/config">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                <Settings className="h-3.5 w-3.5" />
                配置
              </button>
            </Link>
          </div>
        </div>

        {/* Messages area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {!activeConversation || activeConversation.messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 px-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
                <Sparkles className="h-8 w-8 text-primary" />
              </div>
              <div className="text-center">
                <h2 className="text-lg font-semibold">开始对话</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  发送消息与 AI 助手开始交流
                </p>
              </div>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {["你好", "了解一下项目", "价格咨询", "门店信息"].map(
                  (suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => sendMessage(suggestion)}
                      className="rounded-full border px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                    >
                      {suggestion}
                    </button>
                  )
                )}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl py-4">
              {activeConversation.messages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
              {isLoading && (
                <div className="flex gap-3 px-4 py-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                    <Bot className="h-4 w-4" />
                  </div>
                  <div className="flex items-center gap-1 rounded-2xl bg-muted px-4 py-2.5">
                    <div className="flex gap-1">
                      <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50 [animation-delay:0ms]" />
                      <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50 [animation-delay:150ms]" />
                      <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50 [animation-delay:300ms]" />
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input area */}
        <ChatInput onSend={sendMessage} disabled={isLoading} />
      </div>
    </div>
  );
}
