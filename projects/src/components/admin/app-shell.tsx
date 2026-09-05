"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  Archive,
  BarChart3,
  Bot,
  ChevronLeft,
  DatabaseZap,
  FileClock,
  History,
  Menu,
  MessageSquareText,
  PanelLeft,
  Settings2,
  Sparkles,
  TrendingUp,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const groups = [
  {
    label: "监控",
    items: [
      { href: "/", label: "运维总览", icon: BarChart3 },
      { href: "/analytics/sales", label: "销售策略 BI", icon: TrendingUp },
    ],
  },
  {
    label: "客服与配置",
    items: [
      { href: "/v3-debug", label: "AI 对话调试", icon: Bot },
      { href: "/sop", label: "主线 SOP", icon: MessageSquareText },
    ],
  },
  {
    label: "触达与日志",
    items: [
      { href: "/logs", label: "AI 运行日志", icon: FileClock },
      { href: "/logs/sop", label: "SOP 触达日志", icon: History },
      { href: "/logs/sop-platform", label: "第三方 SOP 日志", icon: Archive },
      { href: "/logs/outreach-first-day", label: "沉默唤醒日志", icon: Sparkles },
    ],
  },
  {
    label: "系统",
    items: [{ href: "/admin/customer-cleanup", label: "客户数据清理", icon: DatabaseZap }],
  },
];

const titles = Object.fromEntries(groups.flatMap((group) => group.items.map((item) => [item.href, item.label])));

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const title = titles[pathname] || "AI 客服运营后台";

  return (
    <div className="flex min-h-screen bg-[#f6f7f8] text-zinc-950">
      <aside
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-zinc-200 bg-white transition-[width] duration-200 lg:flex",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <SidebarContent collapsed={collapsed} pathname={pathname} onNavigate={() => undefined} />
        <Button
          variant="ghost"
          size="icon"
          className="absolute -right-4 top-5 z-10 size-8 rounded-md border bg-white shadow-sm"
          onClick={() => setCollapsed((value) => !value)}
          title={collapsed ? "展开导航" : "收起导航"}
        >
          {collapsed ? <PanelLeft /> : <ChevronLeft />}
        </Button>
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-72 gap-0 p-0">
          <SheetHeader className="sr-only">
            <SheetTitle>后台导航</SheetTitle>
            <SheetDescription>选择要进入的运营后台页面。</SheetDescription>
          </SheetHeader>
          <SidebarContent collapsed={false} pathname={pathname} onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b border-zinc-200 bg-white/95 px-4 backdrop-blur lg:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)}>
            <Menu />
            <span className="sr-only">打开导航</span>
          </Button>
          <h1 className="truncate text-base font-semibold">{title}</h1>
        </header>
        <main className="min-w-0">{children}</main>
      </div>
    </div>
  );
}

function SidebarContent({
  collapsed,
  pathname,
  onNavigate,
}: {
  collapsed: boolean;
  pathname: string;
  onNavigate: () => void;
}) {
  return (
    <>
      <div className="flex h-14 items-center gap-3 border-b px-4">
        <Settings2 className="size-5 shrink-0" />
        {!collapsed && <span className="truncate text-sm font-semibold">AI 客服运营后台</span>}
      </div>
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {groups.map((group) => (
          <div key={group.label} className="mb-4">
            {!collapsed && <div className="px-2 pb-1 text-xs font-medium text-zinc-400">{group.label}</div>}
            <div className="space-y-1">
              {group.items.map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname === item.href;
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "flex h-9 items-center gap-3 rounded-md px-2 text-sm text-zinc-600 hover:bg-zinc-100 hover:text-zinc-950",
                      active && "bg-zinc-900 text-white hover:bg-zinc-900 hover:text-white",
                      collapsed && "justify-center",
                    )}
                  >
                    <Icon className="size-4 shrink-0" />
                    {!collapsed && <span className="truncate">{item.label}</span>}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
    </>
  );
}
