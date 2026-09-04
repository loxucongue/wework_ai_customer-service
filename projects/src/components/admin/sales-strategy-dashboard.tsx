"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Filter,
  MessageCircleMore,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

type MetricSet = {
  usage_count?: number;
  adopted_count?: number;
  adoption_rate?: number;
  dispatch_count?: number;
  delivery_success_count?: number;
  delivery_success_rate?: number;
  customer_replied_24h_count?: number;
  customer_replied_24h_rate?: number;
  paid_72h_count?: number;
  paid_72h_rate?: number;
  scheduled_7d_count?: number;
  scheduled_7d_rate?: number;
  decision_coverage_count?: number;
  decision_eligible_count?: number;
  decision_coverage_rate?: number;
  decision_degraded_count?: number;
  decision_degraded_rate?: number;
  delivery_unknown_count?: number;
  delivery_unknown_rate?: number;
  order_query_success_rate?: number;
  order_attribution_complete_rate?: number;
  hard_stop_wrong_advance_count?: number;
  new_blocker_not_paused_count?: number;
  selector_empty_or_error_count?: number;
  taxonomy_fallback_count?: number;
  retrieval_relaxed_count?: number;
};

type DimensionItem = MetricSet & {
  intent_code?: string;
  emotion_code?: string;
  closing_sequence_key?: string;
  closing_sequence_name?: string;
  closing_action?: string;
  closing_node_key?: string;
  closing_node_name?: string;
  closing_primary_rule_name?: string;
  retrieval_mode?: string;
  checkpoint_code?: string;
  checkpoint_name?: string;
  sequence_id?: string;
  sequence_name?: string;
  script_id?: string;
  script_name?: string;
  next_intent_code?: string;
  next_emotion_code?: string;
  emotion_transition?: string;
};

type FailureItem = DimensionItem & {
  id?: string;
  request_id?: string;
  occurred_at?: string;
  decision_status?: string;
  selector_status?: string;
  delivery_status?: string;
  failed_reason?: string;
  decision_reasons?: string[];
};

type DimensionResponse = { items?: DimensionItem[] };
type FailureResponse = { items?: FailureItem[] };
type DashboardPayload = {
  generated_at?: string;
  capabilities?: { sales_decision?: boolean };
  data?: {
    summary?: MetricSet;
    intents?: DimensionResponse;
    emotions?: DimensionResponse;
    closing?: DimensionResponse;
    checkpoints?: DimensionResponse;
    sequences?: DimensionResponse;
    scripts?: DimensionResponse;
    transitions?: DimensionResponse;
    failures?: FailureResponse;
  };
  errors?: Record<string, string>;
  error?: string;
  detail?: string;
};

type Filters = {
  from: string;
  to: string;
  corpId: string;
  wechat: string;
  intent: string;
  emotion: string;
  closingAction: string;
};

const intentLabels: Record<string, string> = {
  fact_inquiry: "事实咨询",
  blocker_expression: "表达卡点",
  transaction_progress: "交易推进",
  information_submission: "提交信息",
  defer: "暂缓考虑",
  explicit_exit: "明确退出",
  normal_exchange: "普通交流",
};
const emotionLabels: Record<string, string> = {
  enthusiastic: "积极热情",
  curious: "好奇了解",
  neutral: "平稳中性",
  hesitant: "犹豫顾虑",
  cold: "冷淡低意愿",
  defensive: "防御不信任",
  impatient: "不耐烦",
  angry: "生气投诉",
};
const closingLabels: Record<string, string> = {
  none: "不推进",
  enter: "进入序列",
  advance: "继续推进",
  pause: "暂停推进",
  fallback: "降级承接",
  complete: "结束序列",
};
export function SalesStrategyDashboard() {
  const initial = useMemo(() => defaultFilters(30), []);
  const [draft, setDraft] = useState<Filters>(initial);
  const [applied, setApplied] = useState<Filters>(initial);
  const [refreshKey, setRefreshKey] = useState(0);
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const query = buildQuery(applied);
    try {
      const response = await fetch(`/api/v3-strategy-analytics?${query}`, { cache: "no-store" });
      const next = (await response.json()) as DashboardPayload;
      if (!response.ok) throw new Error(next.detail || next.error || "销售策略数据加载失败");
      setPayload(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "销售策略数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [applied, refreshKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const data = payload?.data;
  const summary = data?.summary || {};
  const partialErrors = Object.keys(payload?.errors || {}).length;
  const salesDecision = Boolean(payload?.capabilities?.sales_decision);

  return (
    <div className="min-h-[calc(100vh-3.5rem)] bg-[#f5f7f8]">
      <div className="mx-auto max-w-[1680px] space-y-5 p-4 lg:p-6">
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-950 px-5 py-5 text-white shadow-sm lg:px-7">
          <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
            <div className="max-w-3xl">
              <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-300">
                <TrendingUp className="size-4" /> V3 Sales Intelligence
              </div>
              <h2 className="text-2xl font-semibold tracking-tight lg:text-3xl">销售策略 BI</h2>
              <p className="mt-2 text-sm leading-6 text-slate-300">
                观察客户卡在哪里、Reply 采用了什么策略，以及后续开口和订单状态如何变化。结果采用时间窗口归因，不代表单一策略直接造成成交。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
              <span className="rounded-full bg-white/10 px-3 py-1.5">每次刷新读取实时聚合</span>
              <span className="rounded-full bg-white/10 px-3 py-1.5">不展示聊天原文</span>
              <span className="rounded-full bg-white/10 px-3 py-1.5">延时逼单保持 Shadow</span>
            </div>
          </div>
        </section>

        <FilterPanel
          value={draft}
          loading={loading}
          unavailable={{
            intent: !salesDecision || Boolean(payload?.errors?.intents),
            emotion: !salesDecision || Boolean(payload?.errors?.emotions),
            closingAction: !salesDecision || Boolean(payload?.errors?.closing),
          }}
          onChange={setDraft}
          onApply={() => setApplied({ ...draft })}
          onPreset={(days) => {
            const value = defaultFilters(days, draft);
            setDraft(value);
            setApplied(value);
          }}
          onRefresh={() => setRefreshKey((value) => value + 1)}
        />

        {error && (
          <Notice tone="danger" title="暂时无法读取销售策略数据">
            {error}。前端页面已经就绪，但需要后端 analytics 接口和数据库迁移先上线。
          </Notice>
        )}
        {!error && partialErrors > 0 && (
          <Notice tone="warning" title="部分维度暂不可用">
            已加载核心指标，但有 {partialErrors} 个明细接口返回失败；页面保留可用数据，不把缺失项显示为零。
          </Notice>
        )}
        {!error && payload && !salesDecision && (
          <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-xs leading-5 text-blue-800">
            首版已接入跟进策略、卡点、话术和结果指标。意图、情绪与逼单维度会在对应结构化埋点后端上线后自动开放。
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
          <MetricCard label="策略记录" value={number(summary.usage_count)} detail="真实 V3 客户轮次" icon={Target} />
          <MetricCard label="Reply 采用率" value={percent(summary.adoption_rate)} detail={`${number(summary.adopted_count)} 次实际采用`} icon={Sparkles} tone="emerald" />
          <MetricCard label="24h 开口率" value={percent(summary.customer_replied_24h_rate)} detail={`${number(summary.customer_replied_24h_count)} 位客户开口`} icon={MessageCircleMore} tone="blue" />
          <MetricCard label="72h 支付率" value={percent(summary.paid_72h_rate)} detail={`${number(summary.paid_72h_count)} 笔状态转为已支付`} icon={TrendingUp} tone="violet" />
          <MetricCard label="7d 排客率" value={percent(summary.scheduled_7d_rate)} detail={`${number(summary.scheduled_7d_count)} 笔进入排客`} icon={CheckCircle2} tone="amber" />
          {salesDecision ? (
            <MetricCard label="决策覆盖率" value={percent(summary.decision_coverage_rate)} detail={`${number(summary.decision_degraded_count)} 次降级`} icon={ShieldCheck} tone="slate" />
          ) : (
            <MetricCard label="发送成功率" value={percent(summary.delivery_success_rate)} detail={`${number(summary.delivery_success_count)} 次成功送达`} icon={ShieldCheck} tone="slate" />
          )}
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(420px,0.8fr)]">
          <Panel title="关键结果概览" subtitle="各指标分母不同，用于同屏观察，不作为严格转化漏斗">
            <ResultBars summary={summary} />
          </Panel>
          <Panel title="质量与安全监控" subtitle="优先处理误推进、卡点未暂停和决策结构问题">
            <HealthGrid summary={summary} />
          </Panel>
        </div>

        {salesDecision && <div className="grid gap-4 xl:grid-cols-3">
          <DimensionChart
            title="客户实时意图"
            subtitle="本轮唯一主意图分布"
            items={data?.intents?.items}
            dataKey="intent_code"
            labels={intentLabels}
            color="#2563eb"
          />
          <DimensionChart
            title="客户情绪"
            subtitle="客户看到本轮回复前的情绪"
            items={data?.emotions?.items}
            dataKey="emotion_code"
            labels={emotionLabels}
            color="#7c3aed"
          />
          <DimensionChart
            title="逼单动作"
            subtitle="进入、推进、暂停与结束分布"
            items={data?.closing?.items}
            dataKey="closing_action"
            labels={closingLabels}
            color="#059669"
          />
        </div>}

        {salesDecision && <ClosingStrategyTable items={data?.closing?.items} />}

        <div className="grid gap-4 xl:grid-cols-2">
          <RankTable
            title="卡点与跟进序列"
            subtitle="先看高频卡点，再看 Reply 是否真正采用候选"
            items={mergeStrategyItems(data?.checkpoints?.items, data?.sequences?.items)}
          />
          <ScriptTable items={data?.scripts?.items} />
        </div>

        <div className={cn("grid gap-4", salesDecision && "xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]")}>
          {salesDecision && <TransitionTable items={data?.transitions?.items} />}
          <FailureTable items={data?.failures?.items} />
        </div>

        <footer className="flex flex-col gap-1 border-t border-slate-200 py-4 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <span>统计边界：企业 + 接待微信 + 客户身份；不同接待账号不会合并。</span>
          <span>最后读取：{dateTime(payload?.generated_at)}</span>
        </footer>
      </div>
    </div>
  );
}

function FilterPanel({
  value,
  loading,
  unavailable,
  onChange,
  onApply,
  onPreset,
  onRefresh,
}: {
  value: Filters;
  loading: boolean;
  unavailable: Partial<Record<"intent" | "emotion" | "closingAction", boolean>>;
  onChange: (value: Filters) => void;
  onApply: () => void;
  onPreset: (days: number) => void;
  onRefresh: () => void;
}) {
  const update = (patch: Partial<Filters>) => onChange({ ...value, ...patch });
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><Filter className="size-4" />筛选范围</div>
        <div className="flex rounded-lg bg-slate-100 p-1">
          {[7, 30, 90].map((days) => (
            <Button key={days} variant="ghost" size="sm" className="h-7 px-2.5 text-xs" onClick={() => onPreset(days)}>{days} 天</Button>
          ))}
        </div>
      </div>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[140px_140px_1fr_1fr_170px_170px_150px_auto]">
        <Input type="date" value={value.from} onChange={(event) => update({ from: event.target.value })} aria-label="开始日期" />
        <Input type="date" value={value.to} onChange={(event) => update({ to: event.target.value })} aria-label="结束日期" />
        <Input value={value.corpId} onChange={(event) => update({ corpId: event.target.value })} placeholder="企业 ID（可选）" />
        <Input value={value.wechat} onChange={(event) => update({ wechat: event.target.value })} placeholder="接待微信（可选）" />
        <FilterSelect value={value.intent} placeholder="全部意图" options={intentLabels} disabled={unavailable.intent} onChange={(intent) => update({ intent })} />
        <FilterSelect value={value.emotion} placeholder="全部情绪" options={emotionLabels} disabled={unavailable.emotion} onChange={(emotion) => update({ emotion })} />
        <FilterSelect value={value.closingAction} placeholder="全部逼单动作" options={closingLabels} disabled={unavailable.closingAction} onChange={(closingAction) => update({ closingAction })} />
        <div className="flex gap-2">
          <Button className="flex-1" onClick={onApply} disabled={loading}>查询</Button>
          <Button variant="outline" size="icon" onClick={onRefresh} disabled={loading} title="刷新">
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
          </Button>
        </div>
      </div>
    </section>
  );
}

function FilterSelect({ value, placeholder, options, disabled, onChange }: { value: string; placeholder: string; options: Record<string, string>; disabled?: boolean; onChange: (value: string) => void }) {
  return (
    <Select value={value || "all"} disabled={disabled} onValueChange={(next) => onChange(next === "all" ? "" : next)}>
      <SelectTrigger className="w-full bg-white" aria-label={disabled ? `${placeholder}（数据接口暂不可用）` : placeholder} title={disabled ? "该维度的数据接口暂不可用" : undefined}><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        <SelectItem value="all">{placeholder}</SelectItem>
        {Object.entries(options).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function MetricCard({ label, value, detail, icon: Icon, tone = "slate" }: { label: string; value: string; detail: string; icon: typeof Target; tone?: "slate" | "emerald" | "blue" | "violet" | "amber" }) {
  const tones = {
    slate: "bg-slate-100 text-slate-700",
    emerald: "bg-emerald-50 text-emerald-700",
    blue: "bg-blue-50 text-blue-700",
    violet: "bg-violet-50 text-violet-700",
    amber: "bg-amber-50 text-amber-700",
  };
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between text-xs font-medium text-slate-500"><span>{label}</span><span className={cn("rounded-lg p-2", tones[tone])}><Icon className="size-4" /></span></div>
      <div className="mt-3 text-2xl font-semibold tracking-tight text-slate-950 tabular-nums">{value}</div>
      <div className="mt-1 truncate text-xs text-slate-500" title={detail}>{detail}</div>
    </section>
  );
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 shadow-sm lg:p-5">
      <div className="mb-4"><h3 className="text-sm font-semibold text-slate-900">{title}</h3><p className="mt-1 text-xs text-slate-500">{subtitle}</p></div>
      {children}
    </section>
  );
}

function ResultBars({ summary }: { summary: MetricSet }) {
  if (summary.usage_count === undefined) return <Empty label="暂无可确认的结果数据" />;
  const values = [
    { name: "策略记录", value: summary.usage_count || 0 },
    { name: "Reply 采用", value: summary.adopted_count || 0 },
    { name: "发送成功", value: summary.delivery_success_count || 0 },
    { name: "24h 开口", value: summary.customer_replied_24h_count || 0 },
    { name: "72h 支付", value: summary.paid_72h_count || 0 },
    { name: "7d 排客", value: summary.scheduled_7d_count || 0 },
  ];
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={values} layout="vertical" margin={{ left: 6, right: 24 }}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="name" width={72} tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
          <Tooltip formatter={(value) => number(Number(value))} cursor={{ fill: "#f1f5f9" }} />
          <Bar dataKey="value" name="数量" fill="#0f172a" radius={[0, 6, 6, 0]} maxBarSize={24} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function HealthGrid({ summary }: { summary: MetricSet }) {
  const items = [
    { label: "明确退订误推进", value: summary.hard_stop_wrong_advance_count, danger: true },
    { label: "新卡点未暂停", value: summary.new_blocker_not_paused_count, danger: true },
    { label: "旧 Selector 空/错误", value: summary.selector_empty_or_error_count },
    { label: "策略降级", value: summary.decision_degraded_count },
    { label: "送达未知", value: summary.delivery_unknown_count },
    { label: "同类型同动作放宽", value: summary.retrieval_relaxed_count ?? summary.taxonomy_fallback_count },
  ].filter((item) => item.value !== undefined);
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-2">
      {items.map((item) => {
        const count = item.value;
        const hasValue = typeof count === "number";
        const isDanger = Boolean(item.danger && hasValue && count > 0);
        return (
          <div key={item.label} className={cn("rounded-lg border px-3 py-3", isDanger ? "border-red-200 bg-red-50" : "border-slate-200 bg-slate-50")}>
            <div className="flex items-center gap-2 text-xs text-slate-600">
              {isDanger ? <AlertTriangle className="size-3.5 text-red-600" /> : <ShieldCheck className="size-3.5 text-slate-400" />}
              {item.label}
            </div>
            <div className={cn("mt-2 text-xl font-semibold tabular-nums", isDanger && "text-red-700")}>{number(count)}</div>
          </div>
        );
      })}
    </div>
  );
}

function DimensionChart({ title, subtitle, items = [], dataKey, labels, color }: { title: string; subtitle: string; items?: DimensionItem[]; dataKey: keyof DimensionItem; labels: Record<string, string>; color: string }) {
  const chartData = items.slice(0, 8).map((item) => {
    const key = String(item[dataKey] || "unknown");
    return { name: labels[key] || key || "未分类", value: item.usage_count || 0 };
  });
  return (
    <Panel title={title} subtitle={subtitle}>
      {chartData.length ? (
        <div className="h-60">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 4, left: -24, bottom: 20 }}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" angle={-24} textAnchor="end" interval={0} tick={{ fontSize: 10 }} height={54} />
              <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
              <Tooltip formatter={(value) => number(Number(value))} cursor={{ fill: "#f8fafc" }} />
              <Bar dataKey="value" name="使用次数" fill={color} radius={[6, 6, 0, 0]} maxBarSize={34} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : <Empty label="暂无该维度数据" />}
    </Panel>
  );
}

function RankTable({ title, subtitle, items }: { title: string; subtitle: string; items: Array<{ name: string; type: string; metrics: DimensionItem }> }) {
  return (
    <Panel title={title} subtitle={subtitle}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className="border-b text-xs text-slate-500"><tr><th className="pb-2 font-medium">名称</th><th className="pb-2 font-medium">类型</th><th className="pb-2 text-right font-medium">使用</th><th className="pb-2 text-right font-medium">采用率</th><th className="pb-2 text-right font-medium">24h 开口</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {items.slice(0, 10).map((item, index) => <tr key={`${item.type}-${item.name}-${index}`}><td className="max-w-56 truncate py-3 font-medium text-slate-800" title={item.name}>{item.name}</td><td className="py-3"><Badge variant="outline">{item.type}</Badge></td><td className="py-3 text-right tabular-nums">{number(item.metrics.usage_count)}</td><td className="py-3 text-right tabular-nums">{percent(item.metrics.adoption_rate)}</td><td className="py-3 text-right tabular-nums">{percent(item.metrics.customer_replied_24h_rate)}</td></tr>)}
          </tbody>
        </table>
        {!items.length && <Empty label="暂无卡点或序列数据" />}
      </div>
    </Panel>
  );
}

function ScriptTable({ items = [] }: { items?: DimensionItem[] }) {
  return (
    <Panel title="高频卡点话术" subtitle="话术使用、Reply 采用和后续结果同表观察">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className="border-b text-xs text-slate-500"><tr><th className="pb-2 font-medium">话术</th><th className="pb-2 text-right font-medium">使用</th><th className="pb-2 text-right font-medium">采用率</th><th className="pb-2 text-right font-medium">24h 开口</th><th className="pb-2 text-right font-medium">72h 支付</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {items.slice(0, 10).map((item, index) => <tr key={`${item.script_id || item.script_name}-${index}`}><td className="max-w-64 truncate py-3 font-medium text-slate-800" title={item.script_name || item.script_id}>{item.script_name || item.script_id || "未命名话术"}</td><td className="py-3 text-right tabular-nums">{number(item.usage_count)}</td><td className="py-3 text-right tabular-nums">{percent(item.adoption_rate)}</td><td className="py-3 text-right tabular-nums">{percent(item.customer_replied_24h_rate)}</td><td className="py-3 text-right tabular-nums">{percent(item.paid_72h_rate)}</td></tr>)}
          </tbody>
        </table>
        {!items.length && <Empty label="暂无话术采用数据" />}
      </div>
    </Panel>
  );
}

function ClosingStrategyTable({ items = [] }: { items?: DimensionItem[] }) {
  return (
    <Panel title="逼单策略实际使用" subtitle="名称来自本轮外部业务目录，ID 只用于追溯">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="border-b text-xs text-slate-500"><tr><th className="pb-2 font-medium">规则</th><th className="pb-2 font-medium">策略 / 节点</th><th className="pb-2 font-medium">动作</th><th className="pb-2 font-medium">召回方式</th><th className="pb-2 text-right font-medium">使用</th><th className="pb-2 text-right font-medium">24h 开口</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {items.slice(0, 12).map((item, index) => (
              <tr key={`${item.closing_sequence_key || "none"}-${item.closing_node_key || "none"}-${index}`}>
                <td className="max-w-52 truncate py-3" title={item.closing_primary_rule_name}>{item.closing_primary_rule_name || "未命中规则"}</td>
                <td className="max-w-72 py-3"><div className="truncate font-medium text-slate-800" title={item.closing_sequence_name}>{item.closing_sequence_name || item.closing_sequence_key || "未进入策略"}</div><div className="truncate text-xs text-slate-500" title={item.closing_node_name}>{item.closing_node_name || item.closing_node_key || "无节点"}</div></td>
                <td className="py-3"><Badge variant="outline">{closingLabels[item.closing_action || ""] || item.closing_action || "未判断"}</Badge></td>
                <td className="py-3 text-xs text-slate-500">{item.retrieval_mode === "deterministic_top_k" ? "稳定 Top-K" : item.retrieval_mode || "—"}</td>
                <td className="py-3 text-right tabular-nums">{number(item.usage_count)}</td>
                <td className="py-3 text-right tabular-nums">{percent(item.customer_replied_24h_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <Empty label="暂无逼单策略使用数据" />}
      </div>
    </Panel>
  );
}

function TransitionTable({ items = [] }: { items?: DimensionItem[] }) {
  return (
    <Panel title="下一轮变化" subtitle="只统计客户下一次真实回复，不预测 emotion_after">
      <div className="space-y-2">
        {items.slice(0, 8).map((item, index) => (
          <div key={`${item.emotion_transition}-${index}`} className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 px-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
                <span className="truncate">{emotionLabels[item.emotion_code || ""] || item.emotion_code || "未分类"}</span>
                <ArrowRight className="size-3.5 shrink-0 text-slate-400" />
                <span className="truncate">{emotionLabels[item.next_emotion_code || ""] || item.next_emotion_code || "未分类"}</span>
              </div>
              <div className="mt-1 text-xs text-slate-500">意图：{intentLabels[item.intent_code || ""] || item.intent_code || "未分类"} → {intentLabels[item.next_intent_code || ""] || item.next_intent_code || "未分类"}</div>
            </div>
            <span className="shrink-0 text-sm font-semibold tabular-nums">{number(item.usage_count)} 次</span>
          </div>
        ))}
        {!items.length && <Empty label="暂无可确认的跨轮变化" />}
      </div>
    </Panel>
  );
}

function FailureTable({ items = [] }: { items?: FailureItem[] }) {
  return (
    <Panel title="需要关注的问题" subtitle="集中查看未采用、selector、决策结构或送达异常，需结合客户状态复核">
      <div className="space-y-2">
        {items.slice(0, 8).map((item, index) => (
          <div key={item.id || item.request_id || index} className="grid gap-2 rounded-lg border border-slate-200 px-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={item.decision_status === "degraded" ? "destructive" : "outline"}>{item.decision_status || item.selector_status || "异常"}</Badge>
                <span className="truncate text-sm font-medium text-slate-800">{item.checkpoint_name || item.checkpoint_code || item.closing_sequence_name || item.sequence_name || item.closing_primary_rule_name || "策略链路"}</span>
              </div>
              <div className="mt-1 truncate text-xs text-slate-500" title={item.failed_reason || item.decision_reasons?.join(", ")}>{item.failed_reason || item.decision_reasons?.join("、") || "请结合 request_id 查看运行日志"}</div>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-500"><Clock3 className="size-3.5" />{dateTime(item.occurred_at)}</div>
          </div>
        ))}
        {!items.length && <Empty label="当前筛选范围内没有待处理问题" success />}
      </div>
    </Panel>
  );
}

function Notice({ tone, title, children }: { tone: "danger" | "warning"; title: string; children: React.ReactNode }) {
  return <div className={cn("rounded-xl border px-4 py-3", tone === "danger" ? "border-red-200 bg-red-50 text-red-800" : "border-amber-200 bg-amber-50 text-amber-800")}><div className="flex gap-3"><AlertTriangle className="mt-0.5 size-4 shrink-0" /><div><div className="text-sm font-semibold">{title}</div><div className="mt-1 text-xs leading-5 opacity-90">{children}</div></div></div></div>;
}

function Empty({ label, success = false }: { label: string; success?: boolean }) {
  return <div className="flex min-h-28 flex-col items-center justify-center gap-2 text-center text-sm text-slate-400">{success ? <CheckCircle2 className="size-5 text-emerald-500" /> : <TrendingUp className="size-5" />}<span>{label}</span></div>;
}

function mergeStrategyItems(checkpoints: DimensionItem[] = [], sequences: DimensionItem[] = []) {
  return [
    ...checkpoints.map((metrics) => ({ name: metrics.checkpoint_name || metrics.checkpoint_code || "未命名卡点", type: "卡点", metrics })),
    ...sequences.map((metrics) => ({ name: metrics.sequence_name || metrics.sequence_id || "未命名序列", type: "序列", metrics })),
  ].sort((a, b) => (b.metrics.usage_count || 0) - (a.metrics.usage_count || 0));
}

function defaultFilters(days: number, current?: Filters): Filters {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - days + 1);
  return {
    from: dateInput(start),
    to: dateInput(end),
    corpId: current?.corpId || "",
    wechat: current?.wechat || "",
    intent: current?.intent || "",
    emotion: current?.emotion || "",
    closingAction: current?.closingAction || "",
  };
}

function buildQuery(filters: Filters) {
  const query = new URLSearchParams();
  if (filters.from) query.set("started_from", new Date(`${filters.from}T00:00:00`).toISOString());
  if (filters.to) query.set("started_to", new Date(`${filters.to}T23:59:59.999`).toISOString());
  if (filters.corpId.trim()) query.set("corp_id", filters.corpId.trim());
  if (filters.wechat.trim()) query.set("wechat", filters.wechat.trim());
  if (filters.intent) query.set("intent_code", filters.intent);
  if (filters.emotion) query.set("emotion_code", filters.emotion);
  if (filters.closingAction) query.set("closing_action", filters.closingAction);
  return query.toString();
}

function dateInput(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function number(value?: number) { return value === undefined || value === null ? "—" : new Intl.NumberFormat("zh-CN").format(value); }
function percent(value?: number) { return value === undefined || value === null ? "—" : `${(value * 100).toFixed(1)}%`; }
function dateTime(value?: string) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "暂无"; }
