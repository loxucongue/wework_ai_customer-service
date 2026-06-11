"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  MessageSquare,
  PanelLeft,
  PanelLeftClose,
  Plus,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { Conversation } from "@/types/chat";

interface ChatSidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const INITIAL_VISIBLE_COUNT = 24;
const LOAD_MORE_COUNT = 24;

export function ChatSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  collapsed,
  onToggleCollapse,
}: ChatSidebarProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);

  const formatDate = useCallback((timestamp: number) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return "今天";
    if (diffDays === 1) return "昨天";
    if (diffDays < 7) return `${diffDays}天前`;
    return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  }, []);

  useEffect(() => {
    setVisibleCount((current) => {
      if (conversations.length <= INITIAL_VISIBLE_COUNT) {
        return INITIAL_VISIBLE_COUNT;
      }
      return Math.min(Math.max(current, INITIAL_VISIBLE_COUNT), conversations.length);
    });
  }, [conversations.length]);

  const visibleConversations = useMemo(
    () => conversations.slice(0, visibleCount),
    [conversations, visibleCount]
  );

  const remainingCount = Math.max(conversations.length - visibleConversations.length, 0);

  if (collapsed) {
    return (
      <div className="flex h-full w-12 shrink-0 flex-col items-center gap-2 border-r bg-muted/30 py-3">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleCollapse}
          className="h-9 w-9 shrink-0"
        >
          <PanelLeft className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" onClick={onNew} className="h-9 w-9 shrink-0">
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  return (
    <aside className="flex h-full min-h-0 w-72 shrink-0 flex-col border-r bg-muted/30">
      <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">对话记录</h2>
          <p className="text-xs text-muted-foreground">{conversations.length} 条</p>
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" size="icon" onClick={onNew} className="h-8 w-8">
            <Plus className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleCollapse}
            className="h-8 w-8"
          >
            <PanelLeftClose className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-1 p-2">
          {conversations.length === 0 ? (
            <p className="px-3 py-8 text-center text-xs text-muted-foreground">
              暂无对话记录
            </p>
          ) : (
            <>
              {visibleConversations.map((conv) => (
                <div
                  key={conv.id}
                  className={cn(
                    "group relative flex cursor-pointer items-start gap-2 rounded-lg px-3 py-2.5 text-sm transition-colors",
                    activeId === conv.id
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-accent/50"
                  )}
                  onClick={() => onSelect(conv.id)}
                  onMouseEnter={() => setHoveredId(conv.id)}
                  onMouseLeave={() => setHoveredId(null)}
                >
                  <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1 pr-8">
                    <p className="line-clamp-2 break-words text-sm leading-5">{conv.title}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {formatDate(conv.updatedAt)}
                    </p>
                  </div>
                  {hoveredId === conv.id && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="absolute right-1 top-2 h-7 w-7 shrink-0 opacity-0 group-hover:opacity-100"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(conv.id);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
                    </Button>
                  )}
                </div>
              ))}

              {remainingCount > 0 && (
                <div className="px-2 pb-2 pt-1">
                  <Button
                    variant="outline"
                    className="h-9 w-full text-xs text-muted-foreground"
                    onClick={() => setVisibleCount((current) => current + LOAD_MORE_COUNT)}
                  >
                    再加载 {Math.min(remainingCount, LOAD_MORE_COUNT)} 条（剩余 {remainingCount} 条）
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}
