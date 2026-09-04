"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type KeyedItem = {
  key: string;
  name: string;
  description?: string;
  owner?: string;
  action?: string;
  goal?: string;
  definition?: string;
  usage?: string;
  reply_effect?: string;
  flow_action?: string;
};

type AiSalesPolicy = {
  schema_version: string;
  policy_version: string;
  status: string;
  runtime_mode: string;
  updated_at: string;
  purpose: string;
  checksum?: string;
  ownership: Record<string, string>;
  closing: {
    enabled: boolean;
    catalog_source: string;
    silent_tasks_mode: string;
    description: string;
  };
  routing: {
    mode: string;
    description: string;
    fixed_priority: KeyedItem[];
    business_tasks: KeyedItem[];
  };
  intent: {
    realtime_intents: KeyedItem[];
    analytics_scoring: Record<string, unknown>;
  };
  emotion: {
    weak_evidence: string[];
    labels: KeyedItem[];
  };
  system_boundaries: string[];
  audit?: { status: string; error_count: number; warning_count: number };
  storage?: { provider?: string; source?: string; path?: string };
  runtime_health?: { status?: string; using_last_known_good?: boolean; last_error?: string };
};

const EMPTY_POLICY: AiSalesPolicy = {
  schema_version: "",
  policy_version: "",
  status: "",
  runtime_mode: "off",
  updated_at: "",
  purpose: "",
  ownership: {},
  closing: { enabled: false, catalog_source: "", silent_tasks_mode: "off", description: "" },
  routing: { mode: "", description: "", fixed_priority: [], business_tasks: [] },
  intent: { realtime_intents: [], analytics_scoring: {} },
  emotion: { weak_evidence: [], labels: [] },
  system_boundaries: [],
};

const OWNER_LABELS: Record<string, string> = {
  business: "业务配置",
  ai: "AI 判断",
  system: "系统固定",
  analysis: "仅分析",
};

export function AiSalesPolicyWorkbench() {
  const [policy, setPolicy] = useState<AiSalesPolicy>(EMPTY_POLICY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadPolicy() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/ai-sales-policy", { cache: "no-store" });
      const data = (await response.json()) as AiSalesPolicy & { detail?: string; error?: string };
      if (!response.ok) {
        throw new Error(data.detail || data.error || "加载 AI 策略失败");
      }
      setPolicy(data);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载 AI 策略失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadPolicy();
  }, []);

  return (
    <main className="mx-auto max-w-7xl space-y-5 p-5">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">AI 回复策略配置</h1>
            <Badge variant="secondary">V3 运行合同 · 只读</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            当前页面展示 V3 的决策和安全合同；逼单规则、策略、节点和话术由外部业务平台统一维护。
          </p>
        </div>
        <Button variant="outline" onClick={() => void loadPolicy()} disabled={loading}>
          <RefreshCw className={loading ? "animate-spin" : ""} />
          刷新
        </Button>
      </div>

      {error ? (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertTriangle className="size-4" />
          {error}
        </div>
      ) : null}

      <section className="grid gap-3 md:grid-cols-4">
        <SummaryCard label="配置版本" value={policy.policy_version || "—"} detail={policy.schema_version} />
        <SummaryCard label="实时会话" value={policy.runtime_mode === "active" ? "已生效" : policy.runtime_mode} detail="不增加模型调用" />
        <SummaryCard label="延时逼单" value={policy.closing.silent_tasks_mode === "shadow" ? "仅观察" : policy.closing.silent_tasks_mode} detail="当前不会真实发送" />
        <SummaryCard
          label="配置校验"
          value={policy.audit?.status === "ok" ? "通过" : policy.audit?.status || "—"}
          detail={`${policy.audit?.error_count ?? 0} 错误 / ${policy.audit?.warning_count ?? 0} 警告`}
        />
      </section>

      <Card className="gap-4 py-5">
        <CardHeader className="px-5">
          <CardTitle className="text-base">这份配置如何生效</CardTitle>
          <CardDescription>{policy.purpose}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 px-5 md:grid-cols-2">
          {Object.entries(policy.ownership).map(([owner, description]) => (
            <div key={owner} className="rounded-lg border bg-muted/20 p-3">
              <Badge variant="outline">{OWNER_LABELS[owner] || owner}</Badge>
              <p className="mt-2 text-sm leading-6">{description}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Tabs defaultValue="closing" className="gap-4">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="closing">逼单策略</TabsTrigger>
          <TabsTrigger value="routing">决策路由</TabsTrigger>
          <TabsTrigger value="intent">意向判断</TabsTrigger>
          <TabsTrigger value="emotion">情绪判断</TabsTrigger>
          <TabsTrigger value="boundaries">系统边界</TabsTrigger>
          <TabsTrigger value="raw">原始 JSON</TabsTrigger>
        </TabsList>

        <TabsContent value="closing" className="space-y-4">
          <Card className="gap-4 py-5">
            <CardHeader className="px-5">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-base">外部业务策略目录</CardTitle>
                <Badge variant={policy.runtime_mode === "active" && policy.closing.enabled ? "default" : "secondary"}>
                  {policy.runtime_mode === "active" && policy.closing.enabled ? "实时判断已启用" : "待验收 / 未启用"}
                </Badge>
                <Badge variant="outline">{policy.closing.catalog_source || "未配置来源"}</Badge>
              </div>
              <CardDescription>{policy.closing.description}</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 px-5 md:grid-cols-3">
              <SummaryCard label="规则与策略" value="业务平台维护" detail="本地不再保存演示序列" />
              <SummaryCard label="本轮实时推进" value="V3 Reply 判断" detail="必须引用外部真实规则、序列和节点" />
              <SummaryCard label="延时节点" value={policy.closing.silent_tasks_mode === "shadow" ? "仅观察" : policy.closing.silent_tasks_mode} detail="不会调用真实发送适配器" />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="routing">
          <TwoColumnLists
            leftTitle="系统固定优先级"
            leftItems={policy.routing.fixed_priority}
            rightTitle="可由 AI 排序的业务任务"
            rightItems={policy.routing.business_tasks}
          />
        </TabsContent>

        <TabsContent value="intent">
          <PolicyTable
            items={policy.intent.realtime_intents}
            description="实时意图用于本轮语义判断。意向评分只进入 BI，不直接触发回复或逼单。"
          />
        </TabsContent>

        <TabsContent value="emotion" className="space-y-4">
          <PolicyTable
            items={policy.emotion.labels}
            description={`只调整语气和压力。弱证据不能单独定性：${policy.emotion.weak_evidence.join("、")}`}
          />
        </TabsContent>

        <TabsContent value="boundaries">
          <Card className="gap-3 py-5">
            <CardHeader className="px-5">
              <CardTitle className="text-base">不可被业务配置关闭</CardTitle>
              <CardDescription>这些边界由代码和权威事实执行。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 px-5">
              {policy.system_boundaries.map((boundary) => (
                <div key={boundary} className="flex gap-2 rounded-lg border p-3 text-sm leading-6">
                  <CheckCircle2 className="mt-1 size-4 shrink-0 text-emerald-600" />
                  {boundary}
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="raw">
          <Card className="gap-3 py-5">
            <CardHeader className="px-5">
              <CardTitle className="text-base">V3 当前读取的完整配置</CardTitle>
              <CardDescription>
                来源：{policy.storage?.provider || "—"} / {policy.storage?.source || "—"} · {policy.storage?.path || "未记录路径"}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-5">
              <pre className="max-h-[640px] overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-5 text-slate-100">
                {JSON.stringify(policy, null, 2)}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </main>
  );
}

function SummaryCard({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <Card className="gap-1 py-4">
      <CardContent className="px-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-2 text-lg font-semibold">{value}</div>
        {detail ? <div className="mt-1 truncate text-xs text-muted-foreground">{detail}</div> : null}
      </CardContent>
    </Card>
  );
}

function TwoColumnLists({
  leftTitle,
  leftItems,
  rightTitle,
  rightItems,
}: {
  leftTitle: string;
  leftItems: KeyedItem[];
  rightTitle: string;
  rightItems: KeyedItem[];
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ItemList title={leftTitle} items={leftItems} />
      <ItemList title={rightTitle} items={rightItems} />
    </div>
  );
}

function ItemList({ title, items }: { title: string; items: KeyedItem[] }) {
  return (
    <Card className="gap-3 py-5">
      <CardHeader className="px-5"><CardTitle className="text-base">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3 px-5">
        {items.map((item, index) => (
          <div key={item.key} className="rounded-lg border p-3">
            <div className="flex items-center gap-2">
              <Badge variant="outline">{index + 1}</Badge>
              <span className="font-medium">{item.name}</span>
              <code className="text-xs text-muted-foreground">{item.key}</code>
            </div>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{item.action || item.goal || item.description}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function PolicyTable({ items, description }: { items: KeyedItem[]; description: string }) {
  return (
    <Card className="gap-3 py-5">
      <CardHeader className="px-5"><CardDescription>{description}</CardDescription></CardHeader>
      <CardContent className="overflow-x-auto px-5">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="min-w-40">标签</TableHead>
              <TableHead className="min-w-64">定义 / 回复影响</TableHead>
              <TableHead className="min-w-64">V3 用途</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.key}>
                <TableCell className="font-medium">{item.name}<div className="font-mono text-xs text-muted-foreground">{item.key}</div></TableCell>
                <TableCell className="leading-6">{item.definition || item.reply_effect}</TableCell>
                <TableCell className="leading-6 text-muted-foreground">{item.usage || item.flow_action}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
