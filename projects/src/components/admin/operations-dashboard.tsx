"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Bot, Clock3, MessageCircle, RefreshCw, Send, Sparkles, UserPlus } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type CountItem = { key: string; count: number };
type TrendPoint = { bucket: string; total: number; failed: number; timeout: number; avg_ms?: number | null };
type DashboardData = {
  range: { started_from: string; started_to: string; bucket: string; timezone: string };
  ai_reply: {
    calls: number; success: number; failed: number; timeout: number; success_rate: number;
    avg_ms: number; p50_ms: number; p90_ms: number; p95_ms: number; trend: TrendPoint[];
    node_breakdown: Array<{ node: string; calls: number; failed: number; timeout: number; avg_ms: number; p90_ms: number }>;
  };
  contacts: { new_contacts: number; opened_contacts: number };
  platform_sop: {
    events: number; tasks: number; sent: number; no_send: number; failed: number; retry_count: number;
    send_rate: number; avg_dispatch_ms: number | null; trend: TrendPoint[];
    status_breakdown: CountItem[]; reason_breakdown: CountItem[];
  };
  first_day_outreach: {
    triggers: number; plans_created: number; blocked: number; failed: number; first_sent: number;
    second_sent: number; second_cancelled_customer_reply: number; model_attempts: number; retry_count: number;
    avg_ms: number; p90_ms: number; trend: TrendPoint[]; status_breakdown: CountItem[]; reason_breakdown: CountItem[];
  };
  freshness: { latest_ai_reply_at: string; latest_platform_sop_at: string; latest_first_day_outreach_at: string };
};

const ranges = [
  { label: "24 小时", hours: 24 },
  { label: "7 天", hours: 24 * 7 },
  { label: "30 天", hours: 24 * 30 },
];

export function OperationsDashboard() {
  const [rangeHours, setRangeHours] = useState(24);
  const [dateFrom, setDateFrom] = useState(() => todayDateInput());
  const [dateTo, setDateTo] = useState(() => todayDateInput());
  const [corpId, setCorpId] = useState("");
  const [wechat, setWechat] = useState("");
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const selectedRange = dateRangeToIso(dateFrom, dateTo);
    const end = selectedRange?.end || new Date();
    const start = selectedRange?.start || new Date(end.getTime() - rangeHours * 60 * 60 * 1000);
    const params = new URLSearchParams({ started_from: start.toISOString(), started_to: end.toISOString() });
    if (corpId.trim()) params.set("corp_id", corpId.trim());
    if (wechat.trim()) params.set("wechat", wechat.trim());
    try {
      const response = await fetch(`/api/operations-dashboard?${params}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || payload.error || "运维指标加载失败");
      setData(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "运维指标加载失败");
    } finally {
      setLoading(false);
    }
  }, [corpId, dateFrom, dateTo, rangeHours, wechat]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const combinedTrend = useMemo(() => mergeTrends(data), [data]);

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 p-4 lg:p-6">
      <section className="flex flex-col justify-between gap-4 border-b border-zinc-200 pb-5 xl:flex-row xl:items-end">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><Activity className="size-4" />实时运行态势</div>
          <p className="mt-1 text-sm text-zinc-500">AI 回复、第三方 SOP 与首日千人千面链路，每 60 秒刷新。</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex rounded-md border bg-white p-1">
            {ranges.map((item) => (
              <Button
                key={item.hours}
                size="sm"
                variant={!dateFrom && !dateTo && rangeHours === item.hours ? "default" : "ghost"}
                onClick={() => {
                  setRangeHours(item.hours);
                  setDateFrom("");
                  setDateTo("");
                }}
              >
                {item.label}
              </Button>
            ))}
          </div>
          <Input
            className="w-40 bg-white"
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
            title="开始日期"
          />
          <Input
            className="w-40 bg-white"
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
            title="结束日期"
          />
          <Input className="w-48 bg-white" value={corpId} onChange={(event) => setCorpId(event.target.value)} placeholder="企业 ID" />
          <Input className="w-48 bg-white" value={wechat} onChange={(event) => setWechat(event.target.value)} placeholder="接待账号" />
          <Button size="icon" variant="outline" onClick={() => void load()} disabled={loading} title="刷新">
            <RefreshCw className={cn(loading && "animate-spin")} />
          </Button>
        </div>
      </section>

      {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <Metric label="新加微人数" value={data?.contacts.new_contacts} detail="按当前日期范围去重" icon={UserPlus} />
        <Metric label="开口数量" value={data?.contacts.opened_contacts} detail="客户真实消息去重" icon={MessageCircle} />
        <Metric label="AI 回复调用" value={data?.ai_reply.calls} detail={`成功率 ${percent(data?.ai_reply.success_rate)}`} icon={Bot} />
        <Metric label="AI 超时 / 失败" value={`${format(data?.ai_reply.timeout)} / ${format(data?.ai_reply.failed)}`} detail={`P90 ${duration(data?.ai_reply.p90_ms)}`} icon={AlertTriangle} tone="danger" />
        <Metric label="第三方 SOP 已发送" value={data?.platform_sop.sent} detail={`${format(data?.platform_sop.no_send)} 条模型判断不发`} icon={Send} />
        <Metric label="首日计划触发" value={data?.first_day_outreach.triggers} detail={`${format(data?.first_day_outreach.plans_created)} 个计划已创建`} icon={Sparkles} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,0.85fr)]">
        <Panel title="链路执行趋势" subtitle="按当前时间范围自动使用小时或自然日聚合">
          <div className="h-72 min-w-0">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={combinedTrend} margin={{ top: 12, right: 12, bottom: 0, left: -16 }}>
                <CartesianGrid stroke="#e4e4e7" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={28} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="ai" name="AI 回复" stroke="#18181b" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="sop" name="第三方 SOP" stroke="#0f766e" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="outreach" name="首日千人千面" stroke="#b45309" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="链路新鲜度" subtitle="最后一条权威运行记录">
          <div className="divide-y">
            <Freshness label="AI 回复" value={data?.freshness.latest_ai_reply_at} />
            <Freshness label="第三方 SOP" value={data?.freshness.latest_platform_sop_at} />
            <Freshness label="首日千人千面" value={data?.freshness.latest_first_day_outreach_at} />
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="AI 回复性能" subtitle="接口和模型节点耗时">
          <StatRows rows={[
            ["平均耗时", duration(data?.ai_reply.avg_ms)], ["P50", duration(data?.ai_reply.p50_ms)],
            ["P90", duration(data?.ai_reply.p90_ms)], ["P95", duration(data?.ai_reply.p95_ms)],
          ]} />
          <Breakdown title="高频模型节点" items={(data?.ai_reply.node_breakdown || []).slice(0, 6).map((item) => ({ key: item.node, count: item.calls, suffix: duration(item.p90_ms) }))} />
        </Panel>
        <Panel title="第三方 SOP" subtitle="平台任务消费和发送结果">
          <StatRows rows={[
            ["任务数", format(data?.platform_sop.tasks)], ["发送率", percent(data?.platform_sop.send_rate)],
            ["失败", format(data?.platform_sop.failed)], ["重试", format(data?.platform_sop.retry_count)],
          ]} />
          <Breakdown title="状态分布" items={data?.platform_sop.status_breakdown || []} />
        </Panel>
        <Panel title="首日千人千面" subtitle="触发、建计划和两步发送">
          <StatRows rows={[
            ["第一步已发", format(data?.first_day_outreach.first_sent)], ["第二步已发", format(data?.first_day_outreach.second_sent)],
            ["客户回复取消", format(data?.first_day_outreach.second_cancelled_customer_reply)], ["P90", duration(data?.first_day_outreach.p90_ms)],
          ]} />
          <Breakdown title="阻断 / 失败原因" items={data?.first_day_outreach.reason_breakdown || []} />
        </Panel>
      </div>
    </div>
  );
}

function Metric({ label, value, detail, icon: Icon, tone = "normal" }: { label: string; value: string | number | undefined; detail: string; icon: typeof Bot; tone?: "normal" | "danger" }) {
  return <div className="rounded-md border bg-white p-4 shadow-sm"><div className="flex items-center justify-between text-sm text-zinc-500"><span>{label}</span><Icon className={cn("size-4", tone === "danger" ? "text-red-600" : "text-zinc-700")} /></div><div className="mt-3 text-2xl font-semibold tabular-nums">{typeof value === "number" ? format(value) : value || "0"}</div><div className="mt-1 text-xs text-zinc-500">{detail}</div></div>;
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <section className="min-w-0 rounded-md border bg-white p-4 shadow-sm"><div className="mb-4"><h2 className="text-sm font-semibold">{title}</h2><p className="mt-1 text-xs text-zinc-500">{subtitle}</p></div>{children}</section>;
}

function StatRows({ rows }: { rows: Array<[string, string]> }) {
  return <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-zinc-200">{rows.map(([label, value]) => <div key={label} className="bg-white p-3"><div className="text-xs text-zinc-500">{label}</div><div className="mt-1 font-semibold tabular-nums">{value}</div></div>)}</div>;
}

function Breakdown({ title, items }: { title: string; items: Array<CountItem & { suffix?: string }> }) {
  return <div className="mt-4"><div className="mb-2 text-xs font-medium text-zinc-500">{title}</div><div className="space-y-2">{items.length ? items.slice(0, 6).map((item) => <div key={item.key} className="flex min-w-0 items-center justify-between gap-3 text-sm"><span className="truncate text-zinc-600" title={item.key}>{item.key}</span><span className="shrink-0 tabular-nums">{format(item.count)}{item.suffix ? ` · ${item.suffix}` : ""}</span></div>) : <div className="text-sm text-zinc-400">暂无数据</div>}</div></div>;
}

function Freshness({ label, value }: { label: string; value?: string }) {
  return <div className="flex items-center justify-between gap-3 py-4 first:pt-0 last:pb-0"><div className="flex items-center gap-2 text-sm"><Clock3 className="size-4 text-zinc-400" />{label}</div><div className="text-right text-xs text-zinc-500">{value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "暂无记录"}</div></div>;
}

function mergeTrends(data: DashboardData | null) {
  const merged = new Map<string, { bucket: string; ai: number; sop: number; outreach: number }>();
  const add = (rows: TrendPoint[] | undefined, key: "ai" | "sop" | "outreach") => (rows || []).forEach((row) => {
    const item = merged.get(row.bucket) || { bucket: row.bucket, ai: 0, sop: 0, outreach: 0 };
    item[key] = row.total;
    merged.set(row.bucket, item);
  });
  add(data?.ai_reply.trend, "ai"); add(data?.platform_sop.trend, "sop"); add(data?.first_day_outreach.trend, "outreach");
  return [...merged.values()].sort((a, b) => a.bucket.localeCompare(b.bucket)).map((item) => ({ ...item, label: new Date(item.bucket).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: data?.range.bucket === "hour" ? "2-digit" : undefined, hour12: false }) }));
}

function todayDateInput() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dateRangeToIso(dateFrom: string, dateTo: string) {
  if (!dateFrom && !dateTo) return null;
  const startText = dateFrom || dateTo;
  const endText = dateTo || dateFrom;
  const start = new Date(`${startText}T00:00:00`);
  const end = new Date(`${endText}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  const exclusiveEnd = new Date(end.getTime() + 24 * 60 * 60 * 1000 - 1);
  if (start > exclusiveEnd) return { start: new Date(`${endText}T00:00:00`), end: new Date(start.getTime() + 24 * 60 * 60 * 1000 - 1) };
  return { start, end: exclusiveEnd };
}

function format(value: number | undefined) { return new Intl.NumberFormat("zh-CN").format(value || 0); }
function percent(value: number | undefined) { return `${((value || 0) * 100).toFixed(1)}%`; }
function duration(value: number | undefined | null) { if (!value) return "0 ms"; return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`; }
