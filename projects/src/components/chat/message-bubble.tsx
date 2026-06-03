"use client";

import type { ChatMessage } from "@/types/chat";
import { cn } from "@/lib/utils";
import { Bot, User, Tag, Layers, GitBranch, Timer, Bug, Database, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { translateIntent, translateScene, translateSubflow } from "@/lib/workflow-maps";

interface MessageBubbleProps {
  message: ChatMessage;
}

function formatDebugValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function hasDebugMeta(message: ChatMessage): boolean {
  const meta = message.meta;
  if (!meta) return false;
  return Boolean(
      meta.requestId ||
      meta.traceUrl ||
      meta.toolResultKeys?.length ||
      meta.toolCalls?.length ||
      meta.profileUpdate ||
      meta.eventUpdates?.length ||
      meta.imageInfo ||
      typeof meta.memoryLoaded === "boolean"
  );
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const showDebug = !isUser && hasDebugMeta(message);

  return (
    <div
      className={cn("flex gap-3 px-4 py-3", isUser ? "justify-end" : "justify-start")}
    >
      {/* Assistant avatar */}
      {!isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Bot className="h-4 w-4" />
        </div>
      )}

      <div
        className={cn(
          "flex max-w-[75%] flex-col gap-1.5",
          isUser ? "items-end" : "items-start"
        )}
      >
        {/* Image preview */}
        {message.imageUrl && (
          <div className={cn("overflow-hidden rounded-2xl", isUser ? "bg-primary/10" : "bg-muted")}>
            <img
              src={message.imageUrl}
              alt="用户上传的图片"
              className="max-h-60 max-w-full object-contain"
            />
          </div>
        )}

        {/* Message bubble */}
        {message.content && message.contentType !== "image" && (
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap",
              isUser
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-foreground"
            )}
          >
            {message.content}
          </div>
        )}

        {/* Assistant image from workflow */}
        {!isUser && message.contentType === "image" && message.content && (
          <div className="overflow-hidden rounded-2xl bg-muted">
            <img
              src={message.content}
              alt="AI回复的图片"
              className="max-h-80 max-w-full object-contain"
            />
          </div>
        )}

        {/* Meta badges & duration for assistant messages */}
        {!isUser && (message.meta || message.duration != null) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {message.meta?.intent && (
              <Badge variant="outline" className="gap-1 text-xs font-normal">
                <Tag className="h-3 w-3" />
                {translateIntent(message.meta.intent)}
              </Badge>
            )}
            {message.meta?.scene && (
              <Badge variant="outline" className="gap-1 text-xs font-normal">
                <Layers className="h-3 w-3" />
                {translateScene(message.meta.scene)}
              </Badge>
            )}
            {message.meta?.subflow && (
              <Badge variant="outline" className="gap-1 text-xs font-normal">
                <GitBranch className="h-3 w-3" />
                {translateSubflow(message.meta.subflow)}
              </Badge>
            )}
            {message.duration != null && (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Timer className="h-3 w-3" />
                {message.duration < 1000
                  ? `${message.duration}ms`
                  : `${(message.duration / 1000).toFixed(1)}s`}
              </span>
            )}
          </div>
        )}

        {/* Debug metadata from local AI Paths */}
        {showDebug && (
          <details className="group w-full max-w-[560px] rounded-md border border-border/70 bg-background/80 text-xs text-muted-foreground">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none [&::-webkit-details-marker]:hidden">
              <Bug className="h-3.5 w-3.5" />
              <span className="font-medium text-foreground">调试信息</span>
              {message.meta?.requestId && (
                <span className="truncate font-mono text-[11px]">
                  {message.meta.requestId}
                </span>
              )}
            </summary>
            <div className="space-y-2 border-t border-border/70 px-3 py-2">
              {typeof message.meta?.memoryLoaded === "boolean" && (
                <div>
                  <span className="font-medium text-foreground">记忆读取：</span>
                  {message.meta.memoryLoaded ? "已读取" : "未读取"}
                </div>
              )}

              {message.meta?.toolResultKeys && message.meta.toolResultKeys.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="inline-flex items-center gap-1 font-medium text-foreground">
                    <Database className="h-3.5 w-3.5" />
                    工具结果
                  </span>
                  {message.meta.toolResultKeys.map((key) => (
                    <Badge key={key} variant="secondary" className="text-[11px] font-normal">
                      {key}
                    </Badge>
                  ))}
                </div>
              )}

              {message.meta?.toolCalls && message.meta.toolCalls.length > 0 && (
                <div>
                  <div className="mb-1 inline-flex items-center gap-1 font-medium text-foreground">
                    <Database className="h-3.5 w-3.5" />
                    工具调用
                  </div>
                  <pre className="max-h-48 overflow-auto rounded bg-muted/70 p-2 font-mono text-[11px] leading-relaxed">
                    {formatDebugValue(message.meta.toolCalls)}
                  </pre>
                </div>
              )}

              {message.meta?.traceUrl && (
                <div className="flex items-center gap-1">
                  <ExternalLink className="h-3.5 w-3.5" />
                  <span className="font-medium text-foreground">Trace：</span>
                  <span className="break-all font-mono text-[11px]">{message.meta.traceUrl}</span>
                </div>
              )}

              {message.meta?.profileUpdate != null && (
                <div>
                  <div className="mb-1 font-medium text-foreground">画像更新</div>
                  <pre className="max-h-40 overflow-auto rounded bg-muted/70 p-2 font-mono text-[11px] leading-relaxed">
                    {formatDebugValue(message.meta.profileUpdate)}
                  </pre>
                </div>
              )}

              {message.meta?.eventUpdates && message.meta.eventUpdates.length > 0 && (
                <div>
                  <div className="mb-1 font-medium text-foreground">事件更新</div>
                  <pre className="max-h-40 overflow-auto rounded bg-muted/70 p-2 font-mono text-[11px] leading-relaxed">
                    {formatDebugValue(message.meta.eventUpdates)}
                  </pre>
                </div>
              )}

              {message.meta?.imageInfo != null && (
                <div>
                  <div className="mb-1 font-medium text-foreground">图片理解</div>
                  <pre className="max-h-40 overflow-auto rounded bg-muted/70 p-2 font-mono text-[11px] leading-relaxed">
                    {formatDebugValue(message.meta.imageInfo)}
                  </pre>
                </div>
              )}
            </div>
          </details>
        )}

        {/* Timestamp */}
        <span className="text-xs text-muted-foreground">
          {new Date(message.timestamp).toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  );
}
