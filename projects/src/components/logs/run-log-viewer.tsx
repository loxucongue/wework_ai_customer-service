"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  CircleDashed,
  Filter,
  MessageSquareText,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Target,
  TriangleAlert,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import {
  EMOTION_LABELS,
  INTENT_LABELS,
  STATUS_META,
  type CheckpointSummary,
  type Evidence,
  type Filters,
  type JsonValue,
  type KnowledgeMatch,
  type ObservableNode,
  type ObservabilityView,
  type RunDetail,
  type RunItem,
  contentString,
  formatDuration,
  formatTime,
  isRecord,
  isRunning,
  replyMessages,
  runContent,
  runReply,
  runtimePhaseLabel,
  stringField,
} from "./run-log-model";
import { RunNodeDetailSheet } from "./run-node-detail-sheet";

const DEFAULT_FILTERS: Filters = {
  request_id: "",
  limit: "50",
  customer_id: "",
  conversation_id: "",
  started_from: "",
  started_to: "",
  wechat: "",
  run_status: "",
  intent_code: "",
  emotion_code: "",
  checkpoint_code: "",
  decision_status: "",
  sequence_matched: "",
  sequence_adopted: "",
  script_adopted: "",
  node_failed: "",
};

const ADOPTION_REASON: Record<string, string> = {
  adopted: "Reply 已采用",
  reply_not_adopted: "查询有结果，但 Reply 本轮未采用",
  no_checkpoint: "本轮未识别到需要处理的卡点",
  directory_unavailable: "知识目录当时不可用",
  no_sequence_candidate: "已查询，但没有可用序列候选",
  selector_empty: "话术选择器返回为空",
  selector_error: "话术选择器运行异常",
  script_lookup_not_run: "本轮未发起话术查询",
  no_script_candidate: "已查询，但没有可用话术候选",
  historical_not_recorded: "历史日志未保存该字段，不能视为数量为 0",
};

export function RunLogViewer() {
  const [draftFilters, setDraftFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [selectedNode, setSelectedNode] = useState<ObservableNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  const loadRuns = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    try {
      if (filters.request_id.trim()) {
        const payload = await getJson<RunDetail>(
          `/api/logs/runs?request_id=${encodeURIComponent(filters.request_id.trim())}`,
          "按请求 ID 查询失败",
        );
        if (!payload.run?.request_id) throw new Error(`没有找到请求 ${filters.request_id.trim()}`);
        setRuns([payload.run]);
        setSelectedId(payload.run.request_id);
        setDetail(payload);
        return;
      }
      const search = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value && key !== "request_id") search.set(key, value);
      });
      const payload = await getJson<{ items?: RunItem[] }>(`/api/logs/runs?${search.toString()}`, "加载日志失败");
      const items = Array.isArray(payload.items) ? payload.items : [];
      setRuns(items);
      setSelectedId((current) => items.some((item) => item.request_id === current) ? current : items[0]?.request_id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载日志失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [filters]);

  const loadDetail = useCallback(async (requestId: string, silent = false) => {
    if (!requestId) return;
    if (!silent) setDetailLoading(true);
    try {
      const payload = await getJson<RunDetail>(
        `/api/logs/runs?request_id=${encodeURIComponent(requestId)}`,
        "加载日志详情失败",
      );
      setDetail(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载日志详情失败");
    } finally {
      if (!silent) setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void loadRuns(); }, [loadRuns]);
  useEffect(() => {
    if (selectedId && detail?.run?.request_id !== selectedId) void loadDetail(selectedId);
  }, [detail?.run?.request_id, loadDetail, selectedId]);
  useEffect(() => { setSelectedNode(null); }, [selectedId]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadRuns(true);
      const selected = runs.find((item) => item.request_id === selectedId);
      if (selected && isRunning(selected)) void loadDetail(selectedId, true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadDetail, loadRuns, runs, selectedId]);

  const applyFilters = () => setFilters({ ...draftFilters });
  const clearFilters = () => {
    setDraftFilters(DEFAULT_FILTERS);
    setFilters(DEFAULT_FILTERS);
  };
  const selectedRun = detail?.run?.request_id === selectedId
    ? detail.run
    : runs.find((item) => item.request_id === selectedId);
  const selectedDetail = detail?.run?.request_id === selectedId ? detail : null;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-[640px] flex-col overflow-hidden bg-[#f6f7f8] xl:flex-row">
      <aside className="flex max-h-[44vh] w-full shrink-0 flex-col border-b border-zinc-200 bg-white xl:max-h-none xl:w-[360px] xl:border-b-0 xl:border-r">
        <div className="border-b border-zinc-200 px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold">回复记录</div>
              <div className="mt-0.5 text-xs text-zinc-500">先看业务判断，再下钻节点原始记录</div>
            </div>
            <div className="flex gap-1">
              <Button variant={filtersOpen ? "secondary" : "ghost"} size="icon-sm" onClick={() => setFiltersOpen((value) => !value)} title="筛选">
                <Filter className="size-4" />
              </Button>
              <Button variant="ghost" size="icon-sm" onClick={() => void loadRuns()} disabled={loading} title="刷新">
                <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
              </Button>
            </div>
          </div>
          <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); applyFilters(); }}>
            <div className="relative min-w-0 flex-1">
              <Search className="absolute left-2.5 top-2.5 size-4 text-zinc-400" />
              <Input
                value={draftFilters.request_id}
                onChange={(event) => setDraftFilters((current) => ({ ...current, request_id: event.target.value }))}
                placeholder="输入请求 ID 精确查询"
                className="h-9 pl-8 text-sm"
              />
            </div>
            <Button type="submit" size="sm">查询</Button>
          </form>
        </div>

        {filtersOpen ? (
          <FilterPanel filters={draftFilters} onChange={setDraftFilters} onApply={applyFilters} onClear={clearFilters} />
        ) : null}
        {error ? (
          <div className="mx-3 mt-3 flex gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-relaxed text-red-700">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />{error}
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {runs.map((run) => (
            <RunListItem key={run.request_id} run={run} selected={run.request_id === selectedId} onSelect={setSelectedId} />
          ))}
          {!loading && runs.length === 0 ? (
            <div className="p-8 text-center text-sm text-zinc-400">没有符合条件的回复记录</div>
          ) : null}
        </div>
      </aside>

      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {selectedRun ? (
          <RunDetailPanel run={selectedRun} detail={selectedDetail} loading={detailLoading} onOpenNode={setSelectedNode} />
        ) : (
          <div className="flex h-full items-center justify-center p-8 text-sm text-zinc-400">请从左侧选择一条回复记录</div>
        )}
      </main>

      <RunNodeDetailSheet requestId={selectedId} node={selectedNode} onOpenChange={(open) => { if (!open) setSelectedNode(null); }} />
    </div>
  );
}

function FilterPanel({ filters, onChange, onApply, onClear }: {
  filters: Filters;
  onChange: (filters: Filters) => void;
  onApply: () => void;
  onClear: () => void;
}) {
  const update = (key: keyof Filters, value: string) => onChange({ ...filters, [key]: value });
  return (
    <div className="max-h-[52vh] overflow-y-auto border-b border-zinc-200 bg-zinc-50/70 p-3">
      <div className="grid grid-cols-2 gap-2">
        <FilterField label="开始时间"><Input type="datetime-local" value={filters.started_from} onChange={(event) => update("started_from", event.target.value)} /></FilterField>
        <FilterField label="结束时间"><Input type="datetime-local" value={filters.started_to} onChange={(event) => update("started_to", event.target.value)} /></FilterField>
        <FilterField label="企微号"><Input value={filters.wechat} onChange={(event) => update("wechat", event.target.value)} placeholder="如 sl8003" /></FilterField>
        <FilterField label="显示数量"><Input value={filters.limit} onChange={(event) => update("limit", event.target.value)} inputMode="numeric" /></FilterField>
        <FilterField label="客户 ID"><Input value={filters.customer_id} onChange={(event) => update("customer_id", event.target.value)} /></FilterField>
        <FilterField label="会话 ID"><Input value={filters.conversation_id} onChange={(event) => update("conversation_id", event.target.value)} /></FilterField>
        <FilterField label="请求状态"><Select value={filters.run_status} onChange={(value) => update("run_status", value)} options={[["", "全部"], ["success", "正常"], ["degraded", "降级"], ["fallback", "兜底"], ["failed", "失败"], ["delivery_failed", "发送异常"]]} /></FilterField>
        <FilterField label="决策状态"><Select value={filters.decision_status} onChange={(value) => update("decision_status", value)} options={[["", "全部"], ["valid", "正常"], ["degraded", "降级"]]} /></FilterField>
        <FilterField label="最终意图"><Select value={filters.intent_code} onChange={(value) => update("intent_code", value)} options={[["", "全部"], ...Object.entries(INTENT_LABELS)]} /></FilterField>
        <FilterField label="客户情绪"><Select value={filters.emotion_code} onChange={(value) => update("emotion_code", value)} options={[["", "全部"], ...Object.entries(EMOTION_LABELS)]} /></FilterField>
        <FilterField label="卡点编码"><Input value={filters.checkpoint_code} onChange={(event) => update("checkpoint_code", event.target.value)} placeholder="精确匹配" /></FilterField>
        <FilterField label="节点失败"><Select value={filters.node_failed} onChange={(value) => update("node_failed", value)} options={[["", "全部"], ["true", "有失败"], ["false", "无失败"]]} /></FilterField>
        <FilterField label="匹配到序列"><YesNoSelect value={filters.sequence_matched} onChange={(value) => update("sequence_matched", value)} /></FilterField>
        <FilterField label="采用序列"><YesNoSelect value={filters.sequence_adopted} onChange={(value) => update("sequence_adopted", value)} /></FilterField>
        <FilterField label="采用话术"><YesNoSelect value={filters.script_adopted} onChange={(value) => update("script_adopted", value)} /></FilterField>
      </div>
      <div className="mt-3 flex gap-2">
        <Button size="sm" className="flex-1" onClick={onApply}>应用筛选</Button>
        <Button size="sm" variant="outline" onClick={onClear}>清空</Button>
      </div>
    </div>
  );
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="space-y-1 text-[11px] font-medium text-zinc-500"><span>{label}</span>{children}</label>;
}

function Select({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[][] }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} className="h-9 w-full rounded-md border border-input bg-white px-2 text-xs outline-none focus:ring-2 focus:ring-ring/50">
      {options.map(([key, label]) => <option key={key || "all"} value={key}>{label}</option>)}
    </select>
  );
}

function YesNoSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <Select value={value} onChange={onChange} options={[["", "全部"], ["true", "是"], ["false", "否"]]} />;
}

function RunListItem({ run, selected, onSelect }: { run: RunItem; selected: boolean; onSelect: (id: string) => void }) {
  const summary = run.business_summary || {};
  const status = runStatus(run);
  const reply = runReply(run);
  return (
    <button type="button" onClick={() => onSelect(run.request_id)} className={`w-full border-b border-zinc-100 px-4 py-3 text-left transition-colors ${selected ? "bg-blue-50/80 shadow-[inset_3px_0_0_#2563eb]" : "bg-white hover:bg-zinc-50"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="line-clamp-2 text-sm font-medium leading-relaxed text-zinc-900">{runContent(run)}</div>
        <StatusPill status={status} />
      </div>
      {reply ? <div className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-zinc-500">AI：{reply}</div> : null}
      <div className="mt-2 flex flex-wrap gap-1.5">
        <SmallTag>{INTENT_LABELS[summary.intent_code || ""] || summary.intent_code || "意图未记录"}</SmallTag>
        <SmallTag>{EMOTION_LABELS[summary.emotion_code || ""] || summary.emotion_code || "情绪未记录"}</SmallTag>
        {summary.checkpoint_code ? <SmallTag tone="amber">卡点 {summary.checkpoint_name || summary.checkpoint_code}</SmallTag> : null}
        {summary.sequence_adopted ? <SmallTag tone="green">已采用序列</SmallTag> : null}
        {summary.script_adopted ? <SmallTag tone="green">已采用话术</SmallTag> : null}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-zinc-400">
        <span className="truncate font-mono">{run.request_id}</span>
        <span className="shrink-0">{formatTime(run.created_at)}</span>
      </div>
    </button>
  );
}

function RunDetailPanel({ run, detail, loading, onOpenNode }: {
  run: RunItem;
  detail: RunDetail | null;
  loading: boolean;
  onOpenNode: (node: ObservableNode) => void;
}) {
  const view = detail?.observability_view;
  const summary = view?.summary;
  const decision = view?.decision_summary;
  const checkpoint = view?.checkpoint_summary;
  const knowledge = view?.knowledge_match;
  const customerMessage = summary?.customer_message || runContent(run);
  const finalMessages = summary?.final_messages?.length ? summary.final_messages : replyMessages(run.output_snapshot);
  const finalReply = finalMessages.map((item) => contentString(isRecord(item) ? item.content : item)).filter(Boolean);
  const alerts = collectAlerts(run, view);

  return (
    <div className="mx-auto max-w-[1440px] space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">本轮回复判断</h2>
            <StatusPill status={runStatus(run, view)} />
            {decision?.decision_status === "degraded" ? <StatusPill status="degraded" /> : null}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
            <span className="font-mono">{run.request_id}</span>
            <span>企微 {run.business_summary?.wechat || "未记录"}</span>
            <span>{formatTime(run.created_at)}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-zinc-500">
          <Metric label="总耗时" value={formatDuration(summary?.wall_duration_ms || run.duration_ms)} />
          <Metric label="模型调用" value={`${summary?.model_call_count ?? "-"} 次`} />
          <Metric label="Token" value={summary?.total_tokens ? String(summary.total_tokens) : "未记录"} />
          <Metric label="实际模型" value={modelsUsed(view)} />
        </div>
      </header>

      {loading ? <div className="h-1 overflow-hidden rounded-full bg-blue-100"><div className="h-full w-1/3 animate-pulse rounded-full bg-blue-500" /></div> : null}
      {isRunning(run) ? (
        <Notice tone="blue" title={runtimePhaseLabel(run.runtime_phase)} text="运行中只展示主链阶段；请求完成后才提供准确的逐节点输入与输出。" />
      ) : null}
      {alerts.map((alert, index) => <Notice key={index} tone={alert.tone} title={alert.title} text={alert.text} />)}

      <section className="grid gap-4 lg:grid-cols-2">
        <ConversationCard label="客户当前原话" icon={<MessageSquareText className="size-4 text-blue-600" />} lines={[customerMessage]} />
        <ConversationCard label="AI 最终客户可见回复" icon={<Bot className="size-4 text-emerald-600" />} lines={finalReply} empty="尚未生成或历史未记录最终回复" />
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <DecisionCard icon={<Target className="size-4 text-blue-600" />} title="最终意图" value={decision?.intent?.name || "未记录"} meta={`${confidenceLabel(decision?.intent?.confidence)}${decision?.intent?.secondary_names?.length ? ` · 次要：${decision.intent.secondary_names.join("、")}` : ""}`} evidence={decision?.intent?.evidence} />
        <DecisionCard icon={<Sparkles className="size-4 text-violet-600" />} title="客户情绪" value={decision?.emotion?.name || "未记录"} meta={[confidenceLabel(decision?.emotion?.confidence), decision?.emotion?.pressure ? `表达压力 ${pressureLabel(decision.emotion.pressure)}` : "", flowActionLabel(decision?.emotion?.flow_action)].filter(Boolean).join(" · ")} evidence={decision?.emotion?.evidence} />
        <DecisionCard icon={<TriangleAlert className="size-4 text-amber-600" />} title="卡点判断" value={checkpointTitle(checkpoint?.final, checkpoint?.router)} meta={checkpoint?.final?.available ? "Reply 最终判断" : checkpoint?.router?.primary?.code ? "仅 Router 检索判断" : "本轮无明确卡点或未记录"} evidence={checkpoint?.final?.evidence?.length ? checkpoint.final.evidence : checkpoint?.router?.evidence} />
        <DecisionCard icon={<Send className="size-4 text-emerald-600" />} title="逼单动作" value={decision?.closing?.action_name || "未记录"} meta={[decision?.closing?.sequence_name || decision?.closing?.sequence_key, decision?.closing?.node_name || decision?.closing?.node_key, customerStateLabel(decision?.closing?.customer_state), decision?.closing?.pressure ? `表达压力 ${pressureLabel(decision.closing.pressure)}` : ""].filter(Boolean).join(" · ")} evidence={decision?.closing?.evidence} />
      </section>

      <StrategySection knowledge={knowledge} checkpoint={checkpoint} deliveryStatus={view?.delivery?.status} />
      <WorkflowSection view={view} onOpenNode={onOpenNode} />
      <DeliverySection view={view} />
    </div>
  );
}

function StrategySection({ knowledge, checkpoint, deliveryStatus }: {
  knowledge?: KnowledgeMatch;
  checkpoint?: ObservabilityView["checkpoint_summary"];
  deliveryStatus?: string;
}) {
  const sequences = knowledge?.matched_sequences || [];
  const scripts = knowledge?.script_candidates || [];
  const availability = knowledge?.available;
  const router = checkpoint?.router;
  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <SectionHeader title="策略与话术匹配" subtitle="候选、最终采用、实际发送是三个不同阶段" />
      <div className="border-t border-zinc-100 p-4 sm:p-5">
        <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-stretch">
          <FlowCard title="1. 识别卡点" value={router?.primary?.name || router?.primary?.code || "未识别或未记录"} detail={router?.reason || router?.retrieval_goal?.summary} />
          <FlowArrow />
          <FlowCard title="2. 候选序列" value={availability === false ? "历史未记录" : `${sequences.length} 条`} detail={reasonText(knowledge?.adoption_explanation?.sequence)} />
          <FlowArrow />
          <FlowCard title="3. 候选话术" value={availability === false ? "历史未记录" : `${knowledge?.script_candidate_count ?? scripts.length} 条`} detail={reasonText(knowledge?.adoption_explanation?.script)} />
          <FlowArrow />
          <FlowCard title="4. 最终采用" value={knowledge?.adopted?.sequence_name || knowledge?.adopted?.sequence_id || "未采用序列"} detail={knowledge?.adopted?.script_ids?.length ? `采用 ${knowledge.adopted.script_ids.length} 条话术` : "未采用话术"} tone={knowledge?.adopted?.sequence_id || knowledge?.adopted?.script_ids?.length ? "green" : "neutral"} />
          <FlowArrow />
          <FlowCard title="5. 实际发送" value={deliveryText(deliveryStatus)} detail={knowledge?.delivered_content_ids?.length ? `${knowledge.delivered_content_ids.length} 个知识内容已交付` : "没有已交付知识 ID 或未记录"} tone={deliveryStatus === "delivered" || deliveryStatus === "send_succeeded" ? "green" : "neutral"} />
        </div>

        {!availability ? (
          <Notice tone="gray" title="历史数据未完整留存" text="这个请求没有保存完整候选资料，页面不会把字段缺失显示成候选数为 0，也不会用当前知识库反推当时结果。" />
        ) : (
          <div className="grid gap-5 xl:grid-cols-2">
            <div>
              <Subheading title="候选跟进序列" count={sequences.length} />
              <div className="mt-3 space-y-2">
                {sequences.map((sequence, index) => (
                  <div key={`${sequence.sequence_id}-${index}`} className={`rounded-lg border p-3 ${sequence.adopted ? "border-emerald-300 bg-emerald-50/60" : "border-zinc-200"}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div><div className="text-sm font-medium">{sequence.sequence_name || sequence.sequence_id || `候选 ${index + 1}`}</div><div className="mt-0.5 text-xs text-zinc-500">{sequence.selection_reason || sequence.checkpoint_name || sequence.checkpoint_code || "未记录命中理由"}</div></div>
                      {sequence.adopted ? <AdoptedBadge /> : <span className="text-xs text-zinc-400">候选 #{sequence.rank || index + 1}</span>}
                    </div>
                    {sequence.steps?.length ? <div className="mt-2 flex flex-wrap gap-1.5">{sequence.steps.map((step) => <SmallTag key={step.step_id || `${step.sort_order}`} tone={step.adopted ? "green" : "gray"}>{step.action_name || step.action_code || `步骤 ${step.sort_order}`}</SmallTag>)}</div> : null}
                  </div>
                ))}
                {!sequences.length ? <EmptyState text={reasonText(knowledge?.adoption_explanation?.sequence)} /> : null}
              </div>
            </div>
            <div>
              <Subheading title="候选卡点话术" count={scripts.length} />
              <div className="mt-3 space-y-2">
                {scripts.map((script, index) => (
                  <div key={`${script.script_id}-${index}`} className={`rounded-lg border p-3 ${script.adopted ? "border-emerald-300 bg-emerald-50/60" : "border-zinc-200"}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div><div className="text-sm font-medium">{script.script_name || script.script_code || `候选话术 ${index + 1}`}</div><div className="mt-0.5 text-xs text-zinc-500">{[script.checkpoint_type_name, script.checkpoint_tag_name, script.action_name || script.action_code].filter(Boolean).join(" · ") || "标签未记录"}</div></div>
                      <div className="flex gap-1">{script.adopted ? <AdoptedBadge /> : null}{script.delivered ? <SmallTag tone="blue">已发送</SmallTag> : null}</div>
                    </div>
                    <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-zinc-600">{script.text_preview || "历史记录未保存正文预览"}</p>
                  </div>
                ))}
                {!scripts.length ? <EmptyState text={reasonText(knowledge?.adoption_explanation?.script)} /> : null}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function WorkflowSection({ view, onOpenNode }: { view?: ObservabilityView; onOpenNode: (node: ObservableNode) => void }) {
  const stages = view?.workflow_nodes || [];
  const nodes = view?.nodes || [];
  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <SectionHeader title="执行链路" subtitle="先看业务阶段；点击真实内部节点查看当时输入、输出、模型和工具" />
      <div className="border-t border-zinc-100 p-4 sm:p-5">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {stages.map((stage, index) => (
            <div key={stage.key} className={`relative rounded-lg border p-3 ${stageTone(stage.status)}`}>
              <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold">{index + 1}. {stage.label}</span><StatusDot status={stage.status} /></div>
              <div className="mt-1 text-[11px] leading-relaxed text-zinc-500">{stage.summary || stage.purpose}</div>
              {stage.duration_ms ? <div className="mt-2 text-[11px] text-zinc-400">{formatDuration(stage.duration_ms)}</div> : null}
            </div>
          ))}
        </div>
        <div className="mt-5">
          <Subheading title="真实内部节点" count={nodes.length} />
          {nodes.length ? (
            <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {nodes.map((node) => (
                <button key={node.id} type="button" onClick={() => onOpenNode(node)} className="group rounded-lg border border-zinc-200 p-3 text-left transition hover:border-blue-300 hover:bg-blue-50/40">
                  <div className="flex items-center justify-between gap-3"><div className="truncate text-sm font-medium">{node.display_name}</div><div className="flex items-center gap-2"><span className="text-xs text-zinc-400">{formatDuration(node.duration_ms)}</span><ArrowRight className="size-3.5 text-zinc-400 group-hover:text-blue-600" /></div></div>
                  <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-zinc-500">{node.summary?.[0] || node.node_name}</div>
                  <div className="mt-2 flex gap-1.5"><StatusPill status={node.status} />{node.model_calls?.length ? <SmallTag>{node.model_calls.length} 次模型</SmallTag> : null}{node.tool_calls?.length ? <SmallTag>{node.tool_calls.length} 次工具</SmallTag> : null}</div>
                </button>
              ))}
            </div>
          ) : <EmptyState text={view?.data_availability?.node_traces === "expired" ? `节点轨迹已超过 ${view.data_availability.trace_retention_days || 14} 天保留期；业务摘要仍可查看。` : "本次历史记录未保存节点轨迹，或请求仍在处理中。"} />}
        </div>
      </div>
    </section>
  );
}

function DeliverySection({ view }: { view?: ObservabilityView }) {
  const delivery = view?.delivery;
  const store = view?.store_workflow;
  const recommendationRecord = store && isRecord(store.latest_recommendation) ? store.latest_recommendation : undefined;
  const recommendation = recommendationRecord && (
    stringField(recommendationRecord.query)
    || stringField(recommendationRecord.city)
    || (Array.isArray(recommendationRecord.store_ids) && recommendationRecord.store_ids.length > 0)
    || recommendationRecord.recommendation_final_for_destination !== null
      && recommendationRecord.recommendation_final_for_destination !== undefined
  ) ? recommendationRecord : undefined;
  const latestDelivery = store && isRecord(store.latest_delivery) ? store.latest_delivery : undefined;
  if (!delivery && !store) return null;
  return (
    <section className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">发送与平台回执</h3><StatusPill status={delivery?.status || "not_recorded"} /></div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-center"><Metric label="预期发送" value={String(delivery?.expected_count ?? "-")} /><Metric label="成功" value={String(delivery?.succeeded_count ?? "-")} /><Metric label="失败" value={String(delivery?.failed_count ?? "-")} /></div>
        <p className="mt-3 text-xs leading-relaxed text-zinc-500">“最终采用”只表示 Reply 选择了资料；只有这里显示平台接受或送达，才代表实际触达客户。</p>
      </div>
      {store && Object.keys(store).length ? (
        <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">门店事实工作流</h3><SmallTag tone={store.called ? "blue" : "gray"}>{store.called ? "已调用" : "未调用"}</SmallTag></div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-xs"><SummaryPair label="查询状态" value={stringField(store.status) || (store.called ? "已调用" : "未查询")} /><SummaryPair label="查询地点" value={stringField(store.destination) || stringField(store.query) || (store.called ? "未记录" : "未查询")} /><SummaryPair label="匹配门店" value={store.called ? String(Array.isArray(store.stores) ? store.stores.length : "未记录") : "未查询"} /><SummaryPair label="下一步" value={stringField(store.next_action) || (store.called ? "未记录" : "无需门店处理")} /></div>
          {recommendation ? (
            <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50/50 p-3">
              <div className="text-xs font-medium text-blue-900">跨轮门店推荐依据</div>
              <div className="mt-2 grid grid-cols-2 gap-3 text-xs">
                <SummaryPair label="原查询范围" value={stringField(recommendation.query) || "未记录"} />
                <SummaryPair label="城市 / 区县" value={[stringField(recommendation.city), stringField(recommendation.district)].filter(Boolean).join(" / ") || "未记录"} />
                <SummaryPair label="查询是否完成" value={booleanStatus(recommendation.candidate_search_complete)} />
                <SummaryPair label="当前范围最终推荐" value={booleanStatus(recommendation.recommendation_final_for_destination)} />
                <SummaryPair label="继续细化是否有用" value={recommendation.same_city_refinement_useful === false ? "无用，应处理距离卡点" : booleanStatus(recommendation.same_city_refinement_useful)} />
                <SummaryPair label="是否切换新城市" value={store.new_city_detected === true ? "是" : store.new_city_detected === false ? "否" : "本轮未发生新城市查询"} />
              </div>
              {latestDelivery ? <p className="mt-2 text-[11px] leading-relaxed text-blue-700">最近实际发送门店：{Array.isArray(latestDelivery.store_ids) ? latestDelivery.store_ids.join("、") : "未记录"}。推荐依据和最近发送记录分开保存。</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function DecisionCard({ icon, title, value, meta, evidence }: { icon: React.ReactNode; title: string; value: string; meta?: string; evidence?: Evidence[] }) {
  const quote = evidence?.find((item) => item.quote)?.quote;
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">{icon}{title}</div>
      <div className="mt-2 text-base font-semibold text-zinc-900">{value}</div>
      <div className="mt-1 min-h-8 text-xs leading-relaxed text-zinc-500">{meta || "没有更多信息"}</div>
      {quote ? <div className="mt-2 line-clamp-2 rounded bg-zinc-50 px-2 py-1.5 text-[11px] leading-relaxed text-zinc-600">证据：“{quote}”</div> : null}
    </div>
  );
}

function ConversationCard({ label, icon, lines, empty = "没有文本" }: { label: string; icon: React.ReactNode; lines: string[]; empty?: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">{icon}{label}</div>
      <div className="mt-3 space-y-2">
        {lines.length ? lines.map((line, index) => <div key={index} className={`rounded-lg px-3 py-2.5 text-sm leading-relaxed ${label.startsWith("AI") ? "bg-emerald-50 text-emerald-950" : "bg-blue-50 text-blue-950"}`}>{line}</div>) : <div className="text-sm text-zinc-400">{empty}</div>}
      </div>
    </div>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return <div className="flex flex-wrap items-end justify-between gap-2 px-4 py-3 sm:px-5"><h3 className="text-sm font-semibold">{title}</h3><p className="text-xs text-zinc-500">{subtitle}</p></div>;
}

function FlowCard({ title, value, detail, tone = "blue" }: { title: string; value: string; detail?: string; tone?: "blue" | "green" | "neutral" }) {
  const colors = tone === "green" ? "border-emerald-200 bg-emerald-50/60" : tone === "neutral" ? "border-zinc-200 bg-zinc-50" : "border-blue-200 bg-blue-50/50";
  return <div className={`min-w-0 flex-1 rounded-lg border p-3 ${colors}`}><div className="text-[11px] font-medium text-zinc-500">{title}</div><div className="mt-1 truncate text-sm font-semibold">{value}</div><div className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-500">{detail || "未记录说明"}</div></div>;
}

function FlowArrow() { return <div className="hidden items-center text-zinc-300 lg:flex"><ArrowRight className="size-4" /></div>; }
function Subheading({ title, count }: { title: string; count: number }) { return <div className="flex items-center justify-between text-sm font-semibold"><span>{title}</span><span className="text-xs font-normal text-zinc-400">{count} 条已留存</span></div>; }
function AdoptedBadge() { return <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700"><Check className="size-3" />最终采用</span>; }
function SmallTag({ children, tone = "gray" }: { children: React.ReactNode; tone?: "gray" | "blue" | "green" | "amber" }) {
  const classes = { gray: "bg-zinc-100 text-zinc-600", blue: "bg-blue-100 text-blue-700", green: "bg-emerald-100 text-emerald-700", amber: "bg-amber-100 text-amber-800" }[tone];
  return <span className={`rounded px-1.5 py-0.5 text-[11px] ${classes}`}>{children}</span>;
}
function StatusPill({ status }: { status: string }) {
  const meta = STATUS_META[status] || { label: status || "未记录", className: "bg-zinc-100 text-zinc-600 ring-zinc-200" };
  return <span className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${meta.className}`}>{meta.label}</span>;
}
function StatusDot({ status }: { status: string }) {
  if (status === "failed" || status === "not_reached") return <XCircle className="size-4 text-red-500" />;
  if (status === "warning") return <TriangleAlert className="size-4 text-amber-500" />;
  if (status === "pending" || status === "running") return <CircleDashed className="size-4 animate-pulse text-blue-500" />;
  if (status === "success" || status === "completed") return <CheckCircle2 className="size-4 text-emerald-500" />;
  return <CircleDashed className="size-4 text-zinc-400" />;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="min-w-20 rounded-lg border border-zinc-200 bg-white px-3 py-2"><div className="text-[10px] text-zinc-400">{label}</div><div className="mt-0.5 truncate text-xs font-medium text-zinc-700">{value}</div></div>; }
function SummaryPair({ label, value }: { label: string; value: string }) { return <div><div className="text-zinc-400">{label}</div><div className="mt-1 break-words font-medium text-zinc-700">{value}</div></div>; }
function booleanStatus(value: JsonValue) { return value === true ? "是" : value === false ? "否" : "未记录"; }
function EmptyState({ text }: { text: string }) { return <div className="mt-3 rounded-lg border border-dashed border-zinc-200 bg-zinc-50 p-4 text-center text-xs leading-relaxed text-zinc-500">{text}</div>; }

function Notice({ tone, title, text }: { tone: "red" | "amber" | "blue" | "gray"; title: string; text: string }) {
  const classes = { red: "border-red-200 bg-red-50 text-red-800", amber: "border-amber-200 bg-amber-50 text-amber-900", blue: "border-blue-200 bg-blue-50 text-blue-800", gray: "border-zinc-200 bg-zinc-50 text-zinc-700" }[tone];
  return <div className={`flex gap-3 rounded-lg border p-3 text-xs leading-relaxed ${classes}`}><AlertCircle className="mt-0.5 size-4 shrink-0" /><div><div className="font-semibold">{title}</div><div className="mt-0.5">{text}</div></div></div>;
}

function collectAlerts(run: RunItem, view?: ObservabilityView) {
  const alerts: Array<{ tone: "red" | "amber"; title: string; text: string }> = [];
  if (run.error) alerts.push({ tone: "red", title: "请求执行失败", text: run.error });
  if (view?.summary?.fallback_detected) alerts.push({ tone: "amber", title: "本轮触发了兜底", text: "最终回复可能不是模型正常业务决策，请结合失败节点查看原因。" });
  if (view?.decision_summary?.decision_status === "degraded") alerts.push({ tone: "amber", title: "策略判断已降级", text: view.decision_summary.decision_reasons?.join("；") || "部分策略字段无效或缺失，但客户回复链路继续完成。" });
  const delivery = view?.delivery?.status || "";
  if (["send_failed", "delivery_failed", "partial_failed"].includes(delivery)) alerts.push({ tone: "red", title: "发送或送达异常", text: `平台状态：${delivery}` });
  return alerts;
}

function runStatus(run: RunItem, view?: ObservabilityView) {
  if (isRunning(run)) return "running";
  if (run.error) return "failed";
  const delivery = view?.delivery?.status || run.business_summary?.delivery_status || "";
  if (["send_failed", "delivery_failed", "partial_failed"].includes(delivery)) return "delivery_failed";
  if (view?.summary?.fallback_detected || run.business_summary?.fallback_used) return "fallback";
  if (view?.decision_summary?.decision_status === "degraded" || run.business_summary?.decision_status === "degraded") return "degraded";
  return "success";
}

function modelsUsed(view?: ObservabilityView) {
  if (view?.summary?.model_names?.length) return view.summary.model_names.join("、");
  const models = new Set<string>();
  view?.nodes?.forEach((node) => node.model_calls?.forEach((call) => { if (call.model) models.add(call.model); }));
  return models.size ? Array.from(models).join("、") : "未记录";
}

function checkpointTitle(final?: CheckpointSummary["final"], router?: CheckpointSummary["router"]) {
  if (final?.available) return final.scenario || final.category_key || final.state || "存在卡点";
  return router?.primary?.name || router?.primary?.code || "无明确卡点";
}

function confidenceLabel(value?: string) {
  const labels: Record<string, string> = { high: "高置信", medium: "中置信", low: "低置信" };
  return value ? labels[value] || `置信度 ${value}` : "置信度未记录";
}
function pressureLabel(value?: string) {
  return ({ normal: "正常", low: "低压", none: "不推进" } as Record<string, string>)[value || ""] || value || "未记录";
}
function flowActionLabel(value?: string) {
  return ({ keep: "保持当前节奏", lower_pressure: "降低推进压力", pause_marketing_turn: "本轮停止追加营销", handoff_by_system_rule: "转人工规则处理" } as Record<string, string>)[value || ""] || value || "";
}
function customerStateLabel(value?: string) {
  return ({ engaged: "客户愿意继续", hesitant: "客户仍在犹豫", soft_reject: "软拒绝", not_buying_now: "当前暂不购买", hard_stop: "明确停止联系", new_blocker: "出现新卡点", transaction_terminal_or_handoff: "交易终态或人工接管", none: "无明确阶段" } as Record<string, string>)[value || ""] || value || "";
}
function reasonText(value?: string) { return value ? ADOPTION_REASON[value] || value : "历史未记录原因"; }
function deliveryText(status?: string) {
  const labels: Record<string, string> = { delivered: "已确认送达", send_succeeded: "发送成功", platform_accepted: "平台已接受", delivery_pending: "等待回执", direct_response_returned: "接口已返回", send_failed: "发送失败", delivery_failed: "送达失败", partial_failed: "部分失败", not_recorded: "未记录发送" };
  return labels[status || ""] || status || "未记录发送";
}
function stageTone(status: string) {
  if (status === "failed" || status === "not_reached") return "border-red-200 bg-red-50/40";
  if (status === "warning") return "border-amber-200 bg-amber-50/50";
  if (status === "pending") return "border-blue-200 bg-blue-50/50";
  if (status === "success") return "border-emerald-200 bg-emerald-50/40";
  return "border-zinc-200 bg-zinc-50";
}

async function getJson<T>(url: string, fallback: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  const text = await response.text();
  let payload: JsonValue = {};
  try { payload = text ? JSON.parse(text) : {}; } catch { throw new Error(fallback); }
  if (!response.ok) {
    const record = isRecord(payload) ? payload : {};
    throw new Error(stringField(record.error) || stringField(record.detail) || fallback);
  }
  return payload as T;
}
