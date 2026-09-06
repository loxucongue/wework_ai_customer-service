"use client";

import { useEffect, useMemo, useState } from "react";
import { Bot, CheckCircle2, CircleDashed, Copy, TriangleAlert, Wrench, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import {
  type JsonValue,
  type NodeDetail,
  type ObservableNode,
  displayValue,
  formatDuration,
  formatJson,
  formatTime,
  isRecord,
  stringField,
} from "./run-log-model";

type Props = {
  requestId: string;
  node: ObservableNode | null;
  onOpenChange: (open: boolean) => void;
};

type RawModelCall = {
  id: string;
  name: string;
  input: JsonValue;
  output: JsonValue;
  usage: JsonValue;
  error: string;
};

export function RunNodeDetailSheet({ requestId, node, onOpenChange }: Props) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!node) {
      setDetail(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    fetch(`/api/logs/runs?request_id=${encodeURIComponent(requestId)}&node_id=${encodeURIComponent(node.id)}`, { cache: "no-store" })
      .then(async (response) => {
        const text = await response.text();
        const payload = text ? JSON.parse(text) as NodeDetail & { error?: string; detail?: string } : {};
        if (!response.ok) throw new Error(payload.error || payload.detail || "节点详情加载失败");
        return payload;
      })
      .then((payload) => { if (active) setDetail(payload); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "节点详情加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [node, requestId]);

  const resolvedNode = detail?.node || node;
  const trace = detail?.trace || {};
  const rawModelCalls = useMemo(() => collectRawModelCalls(trace.tool_calls || []), [trace.tool_calls]);

  return (
    <Sheet open={Boolean(node)} onOpenChange={onOpenChange}>
      <SheetContent className="w-[min(96vw,980px)] gap-0 p-0 sm:max-w-none">
        <SheetHeader className="border-b px-5 py-4 pr-12">
          <div className="flex flex-wrap items-center gap-2">
            <NodeStatusIcon status={resolvedNode?.status || "not_recorded"} />
            <SheetTitle>{resolvedNode?.display_name || "节点详情"}</SheetTitle>
            {resolvedNode ? <StatusPill status={resolvedNode.status} /> : null}
          </div>
          <SheetDescription>
            {resolvedNode?.node_name || "-"} · {formatDuration(resolvedNode?.duration_ms)} · {formatTime(resolvedNode?.started_at)}
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {loading ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-zinc-500">正在读取该节点的留存详情…</div> : null}
          {error ? <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div> : null}
          {!loading && !error && resolvedNode ? (
            <>
              <Tabs defaultValue="result">
                <TabsList className="grid w-full grid-cols-4">
                  <TabsTrigger value="result">处理结果</TabsTrigger>
                  <TabsTrigger value="input">节点输入</TabsTrigger>
                  <TabsTrigger value="output">节点输出</TabsTrigger>
                  <TabsTrigger value="calls">模型与工具</TabsTrigger>
                </TabsList>

                <TabsContent value="result" className="mt-4 space-y-5">
                  <Panel title="这一步做了什么">
                    <div className="space-y-2">
                      {(resolvedNode.summary || []).map((line, index) => (
                        <div key={index} className="rounded-md bg-zinc-50 px-3 py-2 text-sm leading-relaxed">{line}</div>
                      ))}
                      {!resolvedNode.summary?.length ? <Empty text="没有额外结果摘要" /> : null}
                    </div>
                  </Panel>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <FieldPanel title="关键输入" fields={resolvedNode.important_inputs || []} />
                    <FieldPanel title="关键输出" fields={resolvedNode.important_outputs || []} />
                  </div>
                  {resolvedNode.errors?.length ? <IssuePanel title="节点错误" values={resolvedNode.errors} tone="error" /> : null}
                  {resolvedNode.warnings?.length ? <IssuePanel title="警告与恢复" values={resolvedNode.warnings} tone="warning" /> : null}
                </TabsContent>

                <TabsContent value="input" className="mt-4">
                  <StructuredSnapshot title="节点收到的输入" value={trace.input_snapshot || {}} />
                </TabsContent>

                <TabsContent value="output" className="mt-4">
                  <StructuredSnapshot title="节点产生的输出" value={trace.output_snapshot || {}} />
                </TabsContent>

                <TabsContent value="calls" className="mt-4 space-y-4">
                  <Panel title={`模型调用（${resolvedNode.model_calls?.length || 0}）`} icon={<Bot className="size-4" />}>
                    <div className="space-y-2">
                      {(resolvedNode.model_calls || []).map((call) => (
                        <div key={call.id} className="rounded-lg border p-3 text-sm">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-medium">{call.name}</div>
                            <div className="text-xs text-zinc-500">{call.model || "模型未记录"} · {formatDuration(call.duration_ms)}</div>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-3 text-xs text-zinc-500">
                            <span>Token {call.total_tokens || 0}</span>
                            <span>尝试 {call.attempts || 1} 次</span>
                            <span>Prompt {call.prompt_messages?.reduce((sum, item) => sum + item.chars, 0) || 0} 字符</span>
                          </div>
                          {call.error ? <div className="mt-2 text-xs text-red-700">{call.error}</div> : null}
                        </div>
                      ))}
                      {!resolvedNode.model_calls?.length ? <Empty text="这个节点没有调用模型" /> : null}
                    </div>
                  </Panel>
                  <Panel title={`事实工具（${resolvedNode.tool_calls?.length || 0}）`} icon={<Wrench className="size-4" />}>
                    <div className="space-y-2">
                      {(resolvedNode.tool_calls || []).map((call, index) => (
                        <details key={`${call.name}-${index}`} className="rounded-lg border">
                          <summary className="cursor-pointer px-3 py-2 text-sm">
                            <span className="font-medium">{call.name}</span>
                            <span className="ml-3 text-zinc-500">{formatDuration(call.duration_ms)}</span>
                            <span className={`ml-3 ${call.status === "failed" ? "text-red-700" : "text-emerald-700"}`}>
                              {call.status === "failed" ? "失败" : "成功"}
                            </span>
                          </summary>
                          <div className="grid gap-3 border-t p-3 lg:grid-cols-2">
                            <StructuredSnapshot title="参数（已脱敏）" value={call.input_summary} compact />
                            <StructuredSnapshot title="结果摘要" value={call.output_summary} compact />
                          </div>
                        </details>
                      ))}
                      {!resolvedNode.tool_calls?.length ? <Empty text="这个节点没有调用外部事实工具" /> : null}
                    </div>
                  </Panel>
                  {rawModelCalls.map((call) => <RawModelCallPanel key={call.id} call={call} />)}
                </TabsContent>
              </Tabs>

              <details className="mt-6 rounded-lg border border-zinc-200 bg-zinc-50">
                <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-zinc-700">查看原始记录（已脱敏、可能截断）</summary>
                <div className="space-y-3 border-t p-3">
                  <RawSnapshot title="原始输入快照" value={trace.input_snapshot || {}} />
                  <RawSnapshot title="原始输出快照" value={trace.output_snapshot || {}} />
                  <RawSnapshot title="原始调用记录" value={trace.tool_calls || []} />
                </div>
              </details>
              <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                {detail?.data_availability?.notice || "这里只展示系统现有留存；字段缺失不代表当时结果为零。"}
              </p>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Panel({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-800">{icon}{title}</h3>
      {children}
    </section>
  );
}

function FieldPanel({ title, fields }: { title: string; fields: Array<{ key: string; label: string; value: JsonValue }> }) {
  return (
    <div className="rounded-lg border p-4">
      <div className="mb-3 text-sm font-semibold">{title}</div>
      {fields.length ? (
        <dl className="space-y-2">
          {fields.map((field) => (
            <div key={field.key} className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 text-sm">
              <dt className="text-zinc-500">{field.label}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words">{displayValue(field.value)}</dd>
            </div>
          ))}
        </dl>
      ) : <Empty text="没有额外记录" />}
    </div>
  );
}

function StructuredSnapshot({ title, value, compact = false }: { title: string; value: JsonValue; compact?: boolean }) {
  const entries = isRecord(value) ? Object.entries(value) : [];
  return (
    <section className="rounded-lg border bg-white">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="text-sm font-medium">{title}</div>
        <CopyButton value={value} />
      </div>
      <div className={`divide-y ${compact ? "max-h-80 overflow-auto" : "max-h-[62vh] overflow-auto"}`}>
        {entries.map(([key, item]) => (
          <div key={key} className="grid gap-1 px-3 py-2 text-sm sm:grid-cols-[170px_minmax(0,1fr)] sm:gap-3">
            <div className="font-mono text-xs text-zinc-500">{key}</div>
            <ReadableValue value={item} />
          </div>
        ))}
        {!entries.length ? <div className="p-4"><Empty text={value ? displayValue(value) : "没有留存数据"} /></div> : null}
      </div>
    </section>
  );
}

function ReadableValue({ value }: { value: JsonValue }) {
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-zinc-400">空</span>;
    return (
      <details>
        <summary className="cursor-pointer text-xs text-blue-700">{value.length} 项，点击展开</summary>
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-50 p-2 text-xs">{formatJson(value)}</pre>
      </details>
    );
  }
  if (isRecord(value)) {
    return (
      <details>
        <summary className="cursor-pointer text-xs text-blue-700">{Object.keys(value).length} 个字段，点击展开</summary>
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-50 p-2 text-xs">{formatJson(value)}</pre>
      </details>
    );
  }
  return <span className="whitespace-pre-wrap break-words leading-relaxed">{displayValue(value)}</span>;
}

function RawModelCallPanel({ call }: { call: RawModelCall }) {
  const input = isRecord(call.input) ? call.input : {};
  const messages = Array.isArray(input.messages) ? input.messages : [];
  return (
    <details className="rounded-lg border border-blue-100 bg-blue-50/40">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium">原始模型调用 · {call.name}</summary>
      <div className="space-y-3 border-t border-blue-100 p-3">
        {messages.length ? (
          <div className="space-y-2">
            {messages.map((message, index) => {
              const record = isRecord(message) ? message : {};
              const content = record.content ?? message;
              return (
                <details key={index} className="rounded border bg-white">
                  <summary className="cursor-pointer px-3 py-2 text-xs font-medium">#{index + 1} {stringField(record.role) || "unknown"}</summary>
                  <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t bg-zinc-50 p-3 text-xs leading-relaxed">{typeof content === "string" ? content : formatJson(content)}</pre>
                </details>
              );
            })}
          </div>
        ) : <RawSnapshot title="模型输入" value={call.input} />}
        <RawSnapshot title="模型原始输出" value={call.output} />
        <RawSnapshot title="Usage / error" value={{ usage: call.usage, error: call.error }} />
      </div>
    </details>
  );
}

function RawSnapshot({ title, value }: { title: string; value: JsonValue }) {
  return (
    <div className="rounded-md border bg-white">
      <div className="flex items-center justify-between border-b px-3 py-2 text-xs font-medium text-zinc-600">
        <span>{title}</span><CopyButton value={value} />
      </div>
      <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-relaxed text-zinc-700">{formatJson(value)}</pre>
    </div>
  );
}

function CopyButton({ value }: { value: JsonValue }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      title="复制"
      onClick={() => {
        void navigator.clipboard.writeText(formatJson(value)).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? <CheckCircle2 className="size-3.5 text-emerald-600" /> : <Copy className="size-3.5" />}
    </Button>
  );
}

function IssuePanel({ title, values, tone }: { title: string; values: string[]; tone: "error" | "warning" }) {
  return (
    <div className={`rounded-lg border p-3 text-sm ${tone === "error" ? "border-red-200 bg-red-50 text-red-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
      <div className="mb-1 font-medium">{title}</div>
      {values.map((value, index) => <div key={index} className="text-xs leading-relaxed">{value}</div>)}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="text-sm text-zinc-400">{text}</div>;
}

function StatusPill({ status }: { status: string }) {
  const text = ({ success: "完成", warning: "有警告", failed: "失败", skipped: "跳过", pending: "处理中" } as Record<string, string>)[status] || status;
  return <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600">{text}</span>;
}

function NodeStatusIcon({ status }: { status: string }) {
  if (status === "failed") return <XCircle className="size-4 text-red-600" />;
  if (status === "warning") return <TriangleAlert className="size-4 text-amber-600" />;
  if (status === "pending") return <CircleDashed className="size-4 animate-pulse text-blue-600" />;
  if (status === "skipped") return <CircleDashed className="size-4 text-zinc-400" />;
  return <CheckCircle2 className="size-4 text-emerald-600" />;
}

function collectRawModelCalls(values: JsonValue[]) {
  const output: RawModelCall[] = [];
  values.forEach((value, index) => collectRawModelCall(value, `${index + 1}`, output));
  return output;
}

function collectRawModelCall(value: JsonValue, id: string, output: RawModelCall[]) {
  if (!isRecord(value)) return;
  const name = stringField(value.name).toLowerCase();
  const looksLikeModel = isRecord(value.usage) || value.raw_json_output !== undefined || /model|reply|router|vision|gate/.test(name);
  if (looksLikeModel) {
    output.push({
      id,
      name: stringField(value.name) || "model_call",
      input: value.input || {},
      output: value.raw_json_output ?? value.output ?? {},
      usage: value.usage || {},
      error: stringField(value.error),
    });
  }
  if (Array.isArray(value.nested_calls)) value.nested_calls.forEach((item, index) => collectRawModelCall(item, `${id}-nested-${index}`, output));
  for (const key of ["retry", "recovery"]) if (isRecord(value[key])) collectRawModelCall(value[key], `${id}-${key}`, output);
}
