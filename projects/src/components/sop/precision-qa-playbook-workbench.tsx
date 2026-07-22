"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Copy,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

type GlobalAnswerPolicy = {
  first_answer: string;
  confidence: string;
  mainline_resume: string;
  variation: string;
  facts: string;
  [key: string]: unknown;
};

type ReplyExample = {
  context: string;
  reply: string[];
  [key: string]: unknown;
};

type PrecisionQuestion = {
  id: string;
  intent_definition: string;
  customer_psychology: string;
  question_role: string;
  must_answer: string[];
  must_not_substitute: string[];
  first_ask_strategy: string;
  repeated_ask_strategy: string;
  allowed_confidence: string[];
  forbidden_claims: string[];
  evidence_requirement: string;
  resume_mainline_stage: string;
  reply_examples: ReplyExample[];
  [key: string]: unknown;
};

type PrecisionAuditIssue = {
  severity: "error" | "warning";
  code: string;
  question_id: string;
  message: string;
};

type PrecisionAudit = {
  status: "ok" | "warning" | "error";
  error_count: number;
  warning_count: number;
  issues: PrecisionAuditIssue[];
};

type PrecisionQaConfig = {
  version: number;
  updated_at: string;
  purpose: string;
  global_answer_policy: GlobalAnswerPolicy;
  questions: PrecisionQuestion[];
  audit?: PrecisionAudit;
  storage?: Record<string, unknown>;
  [key: string]: unknown;
};

const GLOBAL_ID = "__global";

const EMPTY_CONFIG: PrecisionQaConfig = {
  version: 1,
  updated_at: "",
  purpose: "",
  global_answer_policy: {
    first_answer: "",
    confidence: "",
    mainline_resume: "",
    variation: "",
    facts: "",
  },
  questions: [],
};

const POLICY_FIELDS: Array<{ key: keyof GlobalAnswerPolicy; label: string }> = [
  { key: "first_answer", label: "优先回答原则" },
  { key: "confidence", label: "信心表达原则" },
  { key: "mainline_resume", label: "回答后恢复主线" },
  { key: "variation", label: "多样化与重复追问" },
  { key: "facts", label: "事实边界" },
];

const QUESTION_ROLES = ["core_blocker", "mainline_slot", "side_question", "tool_fact"];
const EVIDENCE_REQUIREMENTS = ["none", "business_rule", "case_image", "store_fact", "payment_fact", "appointment_fact"];
const MAINLINE_STAGES = ["opening_and_positioning", "store_match", "need_and_case", "activity_and_price", "deposit_decision", "post_paid_registration"];

export function PrecisionQaPlaybookWorkbench() {
  const [config, setConfig] = useState<PrecisionQaConfig>(EMPTY_CONFIG);
  const [selectedId, setSelectedId] = useState(GLOBAL_ID);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  const selectedQuestion = useMemo(
    () => config.questions.find((question) => question.id === selectedId),
    [config.questions, selectedId]
  );
  const diagnostics = useMemo(
    () => [...validatePrecisionConfig(config), ...auditDiagnostics(config.audit)],
    [config]
  );
  const blockingErrors = diagnostics.filter((item) => item.level === "error");
  const warnings = diagnostics.filter((item) => item.level === "warning");

  useEffect(() => {
    void loadConfig();
  }, []);

  useEffect(() => {
    if (selectedId !== GLOBAL_ID && !config.questions.some((question) => question.id === selectedId)) {
      setSelectedId(config.questions[0]?.id || GLOBAL_ID);
    }
  }, [config.questions, selectedId]);

  async function loadConfig() {
    setLoading(true);
    setError("");
    setStatus("");
    try {
      const response = await fetch("/api/precision-qa-playbook", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "加载精准回复配置失败");
      }
      setConfig(normalizePrecisionConfig(data));
      setStatus("已加载最新配置");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载精准回复配置失败");
    } finally {
      setLoading(false);
    }
  }

  async function saveConfig() {
    if (blockingErrors.length) {
      setStatus("");
      setError("请先处理红色校验项");
      return;
    }
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const response = await fetch("/api/precision-qa-playbook", {
        method: "PUT",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(config),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "保存精准回复配置失败");
      }
      setConfig(normalizePrecisionConfig(data));
      setStatus("已保存，模型节点已读取新版本");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存精准回复配置失败");
    } finally {
      setSaving(false);
    }
  }

  function addQuestion() {
    const question = createQuestion(uniqueQuestionId(config.questions, "precision_question"));
    setConfig((current) => ({ ...current, questions: [...current.questions, question] }));
    setSelectedId(question.id);
  }

  function duplicateQuestion(question: PrecisionQuestion) {
    const copied = {
      ...clone(question),
      id: uniqueQuestionId(config.questions, `${question.id}_copy`),
    };
    setConfig((current) => ({ ...current, questions: [...current.questions, copied] }));
    setSelectedId(copied.id);
  }

  function deleteQuestion(questionId: string) {
    setConfig((current) => ({
      ...current,
      questions: current.questions.filter((question) => question.id !== questionId),
    }));
  }

  function moveQuestion(questionId: string, direction: -1 | 1) {
    setConfig((current) => {
      const questions = current.questions.slice();
      const index = questions.findIndex((question) => question.id === questionId);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= questions.length) return current;
      const [question] = questions.splice(index, 1);
      questions.splice(nextIndex, 0, question);
      return { ...current, questions };
    });
  }

  function updateQuestion(questionId: string, patch: Partial<PrecisionQuestion>) {
    setConfig((current) => ({
      ...current,
      questions: current.questions.map((question) =>
        question.id === questionId ? { ...question, ...patch } : question
      ),
    }));
    if (patch.id && selectedId === questionId) {
      setSelectedId(patch.id);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="sticky top-12 z-20 border-b bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="outline" size="icon" aria-label="返回">
              <Link href="/">
                <ArrowLeft />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-semibold leading-tight">精准回复配置</h1>
              <p className="text-sm text-zinc-500">配置高频问题的语义边界、回答目标和主线衔接。</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={loadConfig} disabled={loading || saving}>
              <RefreshCw className={loading ? "animate-spin" : ""} />
              刷新
            </Button>
            <Button onClick={saveConfig} disabled={saving || loading}>
              <Save />
              {saving ? "保存中" : "保存精准回复"}
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-5 px-5 py-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-3">
          <div className="rounded-lg border bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">精准回复</div>
                <div className="text-xs text-zinc-500">{config.questions.length} 个高频问题</div>
              </div>
              <Button size="sm" onClick={addQuestion}>
                <Plus />
                新增
              </Button>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setSelectedId(GLOBAL_ID)}
            className={`w-full rounded-lg border bg-white p-3 text-left transition hover:border-zinc-400 ${
              selectedId === GLOBAL_ID ? "border-zinc-950 shadow-sm" : "border-zinc-200"
            }`}
          >
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Settings2 className="size-4" />
              全局回答策略
            </div>
            <div className="mt-1 text-xs text-zinc-500">适用于全部精准问题</div>
          </button>

          <div className="space-y-2">
            {config.questions.map((question) => (
              <button
                key={question.id}
                type="button"
                onClick={() => setSelectedId(question.id)}
                className={`w-full rounded-lg border bg-white p-3 text-left transition hover:border-zinc-400 ${
                  selectedId === question.id ? "border-zinc-950 shadow-sm" : "border-zinc-200"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-sm font-semibold">{question.id}</div>
                  <Badge variant="outline">{question.question_role || "未分类"}</Badge>
                </div>
                <div className="mt-2 line-clamp-2 text-xs text-zinc-500">
                  {question.intent_definition || "未填写意图定义"}
                </div>
                <div className="mt-2 truncate text-xs text-zinc-500">
                  回到 {question.resume_mainline_stage || "未配置主线"}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="space-y-4">
          {(error || status || blockingErrors.length > 0 || warnings.length > 0) && (
            <div className="rounded-lg border bg-white p-4 text-sm">
              {error && <div className="text-red-600">{error}</div>}
              {status && !error && <div className="text-emerald-700">{status}</div>}
              {blockingErrors.length > 0 && (
                <div className="mt-2 space-y-1 text-red-600">
                  {blockingErrors.map((item, index) => <div key={`${item.message}-${index}`}>{item.message}</div>)}
                </div>
              )}
              {warnings.length > 0 && (
                <div className="mt-2 space-y-1 text-amber-700">
                  {warnings.map((item, index) => <div key={`${item.message}-${index}`}>{item.message}</div>)}
                </div>
              )}
            </div>
          )}

          {loading ? (
            <div className="rounded-lg border bg-white p-8 text-sm text-zinc-500">正在加载配置...</div>
          ) : selectedId === GLOBAL_ID ? (
            <GlobalPolicyEditor
              config={config}
              onChange={(patch) => setConfig((current) => ({ ...current, ...patch }))}
            />
          ) : selectedQuestion ? (
            <PrecisionQuestionEditor
              question={selectedQuestion}
              index={config.questions.findIndex((question) => question.id === selectedQuestion.id)}
              total={config.questions.length}
              onChange={(patch) => updateQuestion(selectedQuestion.id, patch)}
              onDuplicate={() => duplicateQuestion(selectedQuestion)}
              onDelete={() => deleteQuestion(selectedQuestion.id)}
              onMove={(direction) => moveQuestion(selectedQuestion.id, direction)}
            />
          ) : (
            <div className="rounded-lg border bg-white p-8 text-sm text-zinc-500">请选择一条精准回复。</div>
          )}
        </section>
      </div>
    </main>
  );
}

function GlobalPolicyEditor({
  config,
  onChange,
}: {
  config: PrecisionQaConfig;
  onChange: (patch: Partial<PrecisionQaConfig>) => void;
}) {
  function updatePolicy(key: keyof GlobalAnswerPolicy, value: string) {
    onChange({ global_answer_policy: { ...config.global_answer_policy, [key]: value } });
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-5">
        <div>
          <h2 className="text-lg font-semibold">全局回答策略</h2>
          <p className="text-sm text-zinc-500">版本 {config.version} · 最近保存 {formatUpdatedAt(config.updated_at)}</p>
        </div>
        <div className="mt-5">
          <Field label="精准回复库目的">
            <Textarea value={config.purpose} onChange={(event) => onChange({ purpose: event.target.value })} className="min-h-24" />
          </Field>
        </div>
      </div>
      <div className="rounded-lg border bg-white p-5">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {POLICY_FIELDS.map((field) => (
            <Field key={String(field.key)} label={field.label} className={field.key === "facts" ? "col-span-2" : ""}>
              <Textarea
                value={stringValue(config.global_answer_policy[field.key])}
                onChange={(event) => updatePolicy(field.key, event.target.value)}
                className="min-h-28"
              />
            </Field>
          ))}
        </div>
      </div>
      <JsonPreview title="当前全局策略 JSON" value={{ purpose: config.purpose, global_answer_policy: config.global_answer_policy }} />
    </div>
  );
}

function PrecisionQuestionEditor({
  question,
  index,
  total,
  onChange,
  onDuplicate,
  onDelete,
  onMove,
}: {
  question: PrecisionQuestion;
  index: number;
  total: number;
  onChange: (patch: Partial<PrecisionQuestion>) => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onMove: (direction: -1 | 1) => void;
}) {
  function updateExample(exampleIndex: number, patch: Partial<ReplyExample>) {
    onChange({
      reply_examples: question.reply_examples.map((example, currentIndex) =>
        currentIndex === exampleIndex ? { ...example, ...patch } : example
      ),
    });
  }

  function addExample() {
    onChange({ reply_examples: [...question.reply_examples, { context: "", reply: [""] }] });
  }

  function deleteExample(exampleIndex: number) {
    onChange({ reply_examples: question.reply_examples.filter((_, currentIndex) => currentIndex !== exampleIndex) });
  }

  function moveExample(exampleIndex: number, direction: -1 | 1) {
    const nextIndex = exampleIndex + direction;
    if (nextIndex < 0 || nextIndex >= question.reply_examples.length) return;
    const examples = question.reply_examples.slice();
    const [example] = examples.splice(exampleIndex, 1);
    examples.splice(nextIndex, 0, example);
    onChange({ reply_examples: examples });
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">{question.id}</h2>
            <p className="text-sm text-zinc-500">精准问题 #{index + 1}</p>
          </div>
          <div className="flex flex-wrap gap-1">
            <Button variant="ghost" size="icon-sm" onClick={() => onMove(-1)} disabled={index <= 0} aria-label="上移">
              <ChevronUp />
            </Button>
            <Button variant="ghost" size="icon-sm" onClick={() => onMove(1)} disabled={index >= total - 1} aria-label="下移">
              <ChevronDown />
            </Button>
            <Button variant="outline" size="sm" onClick={onDuplicate}>
              <Copy />
              复制
            </Button>
            <Button variant="outline" size="sm" onClick={onDelete}>
              <Trash2 />
              删除
            </Button>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="ID">
            <Input value={question.id} onChange={(event) => onChange({ id: cleanIdentifier(event.target.value) })} />
          </Field>
          <Field label="问题角色">
            <Input list="precision-question-roles" value={question.question_role} onChange={(event) => onChange({ question_role: event.target.value })} />
            <datalist id="precision-question-roles">{QUESTION_ROLES.map((value) => <option key={value} value={value} />)}</datalist>
          </Field>
          <Field label="意图定义" className="col-span-2">
            <Textarea value={question.intent_definition} onChange={(event) => onChange({ intent_definition: event.target.value })} className="min-h-24" />
          </Field>
          <Field label="客户心理" className="col-span-2">
            <Textarea value={question.customer_psychology} onChange={(event) => onChange({ customer_psychology: event.target.value })} className="min-h-24" />
          </Field>
          <Field label="事实要求">
            <Input list="precision-evidence-requirements" value={question.evidence_requirement} onChange={(event) => onChange({ evidence_requirement: event.target.value })} />
            <datalist id="precision-evidence-requirements">{EVIDENCE_REQUIREMENTS.map((value) => <option key={value} value={value} />)}</datalist>
          </Field>
          <Field label="回答后恢复主线">
            <Input list="precision-mainline-stages" value={question.resume_mainline_stage} onChange={(event) => onChange({ resume_mainline_stage: event.target.value })} />
            <datalist id="precision-mainline-stages">{MAINLINE_STAGES.map((value) => <option key={value} value={value} />)}</datalist>
          </Field>
        </div>
      </div>

      <div className="rounded-lg border bg-white p-5">
        <h3 className="font-semibold">回答内容边界</h3>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <LineListField label="必须回答，一行一条" value={question.must_answer} onChange={(value) => onChange({ must_answer: value })} />
          <LineListField label="不能用来替代答案，一行一条" value={question.must_not_substitute} onChange={(value) => onChange({ must_not_substitute: value })} />
          <LineListField label="允许的信心表达，一行一条" value={question.allowed_confidence} onChange={(value) => onChange({ allowed_confidence: value })} />
          <LineListField label="禁止承诺，一行一条" value={question.forbidden_claims} onChange={(value) => onChange({ forbidden_claims: value })} />
        </div>
      </div>

      <div className="rounded-lg border bg-white p-5">
        <h3 className="font-semibold">回答策略</h3>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="首次提问">
            <Textarea value={question.first_ask_strategy} onChange={(event) => onChange({ first_ask_strategy: event.target.value })} className="min-h-28" />
          </Field>
          <Field label="重复追问">
            <Textarea value={question.repeated_ask_strategy} onChange={(event) => onChange({ repeated_ask_strategy: event.target.value })} className="min-h-28" />
          </Field>
        </div>
      </div>

      <div className="rounded-lg border bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="font-semibold">优秀回复示例</h3>
          <Button variant="outline" size="sm" onClick={addExample}>
            <Plus />
            新增示例
          </Button>
        </div>
        <div className="mt-4 space-y-3">
          {question.reply_examples.map((example, exampleIndex) => (
            <div key={exampleIndex} className="rounded-lg border border-zinc-200 p-4">
              <div className="flex items-center justify-between gap-3">
                <Badge variant="outline">示例 {exampleIndex + 1}</Badge>
                <div className="flex flex-wrap gap-1">
                  <Button variant="ghost" size="icon-sm" onClick={() => moveExample(exampleIndex, -1)} disabled={exampleIndex === 0} aria-label="示例上移"><ChevronUp /></Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => moveExample(exampleIndex, 1)} disabled={exampleIndex === question.reply_examples.length - 1} aria-label="示例下移"><ChevronDown /></Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => deleteExample(exampleIndex)} aria-label="删除示例"><Trash2 /></Button>
                </div>
              </div>
              <div className="mt-3 space-y-3">
                <Field label="适用上下文">
                  <Input value={example.context} onChange={(event) => updateExample(exampleIndex, { context: event.target.value })} />
                </Field>
                <LineListField label="回复消息，一行一条" value={example.reply} onChange={(value) => updateExample(exampleIndex, { reply: value })} minHeight="min-h-28" />
              </div>
            </div>
          ))}
          {question.reply_examples.length === 0 && (
            <div className="rounded-lg border border-dashed p-8 text-center text-sm text-zinc-500">还没有优秀回复示例。</div>
          )}
        </div>
      </div>

      <JsonPreview title="当前精准回复 JSON" value={question} />
    </div>
  );
}

function LineListField({
  label,
  value,
  onChange,
  minHeight = "min-h-36",
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  minHeight?: string;
}) {
  return (
    <Field label={label}>
      <Textarea
        value={value.join("\n")}
        onChange={(event) => onChange(lines(event.target.value))}
        className={minHeight}
      />
    </Field>
  );
}

function JsonPreview({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="rounded-lg border bg-white p-5">
      <h3 className="font-semibold">{title}</h3>
      <pre className="mt-3 max-h-[360px] overflow-auto rounded-md bg-zinc-950 p-4 text-xs text-zinc-50">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function Field({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <div className={className}>
      <Label className="mb-2 text-xs text-zinc-500">{label}</Label>
      {children}
    </div>
  );
}

function normalizePrecisionConfig(value: unknown): PrecisionQaConfig {
  if (!isRecord(value)) return EMPTY_CONFIG;
  const rawPolicy = isRecord(value.global_answer_policy) ? value.global_answer_policy : {};
  return {
    ...value,
    version: numberValue(value.version, 1),
    updated_at: stringValue(value.updated_at),
    purpose: stringValue(value.purpose),
    global_answer_policy: {
      ...rawPolicy,
      first_answer: stringValue(rawPolicy.first_answer),
      confidence: stringValue(rawPolicy.confidence),
      mainline_resume: stringValue(rawPolicy.mainline_resume),
      variation: stringValue(rawPolicy.variation),
      facts: stringValue(rawPolicy.facts),
    },
    questions: Array.isArray(value.questions) ? value.questions.map(normalizeQuestion) : [],
    audit: normalizeAudit(value.audit),
    storage: isRecord(value.storage) ? value.storage : undefined,
  };
}

function normalizeQuestion(value: unknown): PrecisionQuestion {
  const record = isRecord(value) ? value : {};
  return {
    ...record,
    id: cleanIdentifier(stringValue(record.id)),
    intent_definition: stringValue(record.intent_definition),
    customer_psychology: stringValue(record.customer_psychology),
    question_role: stringValue(record.question_role),
    must_answer: stringList(record.must_answer),
    must_not_substitute: stringList(record.must_not_substitute),
    first_ask_strategy: stringValue(record.first_ask_strategy),
    repeated_ask_strategy: stringValue(record.repeated_ask_strategy),
    allowed_confidence: stringList(record.allowed_confidence),
    forbidden_claims: stringList(record.forbidden_claims),
    evidence_requirement: stringValue(record.evidence_requirement),
    resume_mainline_stage: stringValue(record.resume_mainline_stage),
    reply_examples: Array.isArray(record.reply_examples) ? record.reply_examples.map(normalizeExample) : [],
  };
}

function normalizeExample(value: unknown): ReplyExample {
  const record = isRecord(value) ? value : {};
  return { ...record, context: stringValue(record.context), reply: stringList(record.reply) };
}

function normalizeAudit(value: unknown): PrecisionAudit | undefined {
  if (!isRecord(value)) return undefined;
  const issues = Array.isArray(value.issues)
    ? value.issues.map((item) => {
        const record = isRecord(item) ? item : {};
        return {
          severity: stringValue(record.severity) === "error" ? "error" as const : "warning" as const,
          code: stringValue(record.code),
          question_id: stringValue(record.question_id),
          message: stringValue(record.message),
        };
      })
    : [];
  const status = stringValue(value.status);
  return {
    status: status === "error" || status === "warning" ? status : "ok",
    error_count: numberValue(value.error_count, 0),
    warning_count: numberValue(value.warning_count, 0),
    issues,
  };
}

function validatePrecisionConfig(config: PrecisionQaConfig): Array<{ level: "error" | "warning"; message: string }> {
  const diagnostics: Array<{ level: "error" | "warning"; message: string }> = [];
  const ids = new Set<string>();
  for (const question of config.questions) {
    if (!question.id) diagnostics.push({ level: "error", message: "存在空的精准问题 ID" });
    if (ids.has(question.id)) diagnostics.push({ level: "error", message: `精准问题 ID 重复：${question.id}` });
    ids.add(question.id);
    if (!question.intent_definition.trim()) diagnostics.push({ level: "error", message: `${question.id || "未命名问题"} 缺少意图定义` });
    if (question.must_answer.length === 0) diagnostics.push({ level: "error", message: `${question.id || "未命名问题"} 至少需要一条必须回答` });
    if (question.reply_examples.length === 0) diagnostics.push({ level: "warning", message: `${question.id || "未命名问题"} 没有优秀回复示例` });
  }
  return diagnostics;
}

function auditDiagnostics(audit: PrecisionAudit | undefined): Array<{ level: "error" | "warning"; message: string }> {
  if (!audit?.issues?.length) return [];
  return audit.issues.map((issue) => ({
    level: issue.severity,
    message: issue.question_id ? `${issue.question_id}: ${issue.message}` : issue.message,
  }));
}

function createQuestion(id: string): PrecisionQuestion {
  return {
    id,
    intent_definition: "",
    customer_psychology: "",
    question_role: "core_blocker",
    must_answer: [],
    must_not_substitute: [],
    first_ask_strategy: "",
    repeated_ask_strategy: "",
    allowed_confidence: [],
    forbidden_claims: [],
    evidence_requirement: "none",
    resume_mainline_stage: "need_and_case",
    reply_examples: [],
  };
}

function uniqueQuestionId(questions: PrecisionQuestion[], baseId: string) {
  const existing = new Set(questions.map((question) => question.id));
  let next = cleanIdentifier(baseId) || "precision_question";
  let index = 2;
  while (existing.has(next)) {
    next = `${cleanIdentifier(baseId)}_${index}`;
    index += 1;
  }
  return next;
}

function lines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.map(stringValue).filter(Boolean) : [];
}

function cleanIdentifier(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, "");
}

function numberValue(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function stringValue(value: unknown) {
  if (value === null || value === undefined) return "";
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function formatUpdatedAt(value: string) {
  if (!value) return "尚未保存到独立配置";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}
