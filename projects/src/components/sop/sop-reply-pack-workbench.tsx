"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Copy,
  Handshake,
  ImageIcon,
  LoaderCircle,
  MapPin,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Upload,
  Video,
  WalletCards,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

type MessageType = "text" | "image" | "video" | "payment_collection" | "store_address" | "human_handoff" | "human_handoff_notice";

type ReplyMessage = {
  type: MessageType;
  order: number;
  content: Record<string, unknown>;
};

type SopPack = {
  id: string;
  enabled: boolean;
  scope: string;
  scopes: string[];
  sop_category: string;
  name: string;
  purpose: string;
  order: number;
  send_once: boolean;
  event_type: string;
  delay_minutes: number;
  day_stage: string;
  customer_state: string;
  stage_tag: string;
  triggers: string[];
  reply_messages: ReplyMessage[];
};

type SopConfig = {
  version: number;
  updated_at: string;
  packs: SopPack[];
  audit?: SopAudit;
};

type SopAuditIssue = {
  severity: "error" | "warning";
  code: string;
  pack_id: string;
  message: string;
  message_order?: number;
};

type SopAudit = {
  status: "ok" | "warning" | "error";
  error_count: number;
  warning_count: number;
  issues: SopAuditIssue[];
};

const EMPTY_CONFIG: SopConfig = {
  version: 1,
  updated_at: "",
  packs: [],
};

const MESSAGE_TYPES: Array<{ value: MessageType; label: string }> = [
  { value: "text", label: "文本" },
  { value: "image", label: "图片 URL" },
  { value: "video", label: "视频 URL" },
  { value: "payment_collection", label: "10 元预约金" },
  { value: "store_address", label: "门店地址卡" },
  { value: "human_handoff_notice", label: "内部关注" },
];

const SOP_EVENT_TYPES = [
  { value: "__any", label: "不限制" },
  { value: "sop_friend_added_immediate", label: "首次加微立即通知" },
  { value: "sop_friend_added_schedule_batch", label: "首次加微定时通知" },
  { value: "sop_platform_task", label: "平台任务即时转发" },
];

const SOP_SCOPES = [
  { value: "chat_gate", label: "AI 回复入口 SOP Gate" },
  { value: "event_first_add", label: "/sop/events 首次加微" },
  { value: "event_platform_task", label: "/sop/events 平台任务" },
];

const SOP_CATEGORIES = [
  "opening",
  "intro",
  "effect_case",
  "activity_intro",
  "store_prompt",
  "store_address",
  "price_quote",
  "deposit_push",
  "payment_followup",
  "final_close",
  "platform_actions",
];

export function SopReplyPackWorkbench() {
  const [config, setConfig] = useState<SopConfig>(EMPTY_CONFIG);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  const selectedPack = useMemo(
    () => config.packs.find((pack) => pack.id === selectedId) || config.packs[0],
    [config.packs, selectedId]
  );

  const diagnostics = useMemo(() => [...validateConfig(config), ...auditDiagnostics(config.audit)], [config]);
  const blockingErrors = diagnostics.filter((item) => item.level === "error");
  const warnings = diagnostics.filter((item) => item.level === "warning");

  useEffect(() => {
    void loadConfig();
  }, []);

  useEffect(() => {
    if (!selectedId && config.packs[0]) {
      setSelectedId(config.packs[0].id);
      return;
    }
    if (selectedId && !config.packs.some((pack) => pack.id === selectedId)) {
      setSelectedId(config.packs[0]?.id || "");
    }
  }, [config.packs, selectedId]);

  async function loadConfig() {
    setLoading(true);
    setError("");
    setStatus("");
    try {
      const response = await fetch("/api/sop-reply-packs", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "加载 SOP 配置失败");
      }
      setConfig(normalizeConfig(data));
      setStatus("已加载最新配置");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 SOP 配置失败");
    } finally {
      setLoading(false);
    }
  }

  async function saveConfig() {
    if (blockingErrors.length) {
      setError("");
      setStatus("");
      return;
    }
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const response = await fetch("/api/sop-reply-packs", {
        method: "PUT",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(config),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "保存 SOP 配置失败");
      }
      setConfig(normalizeConfig(data));
      setStatus("已保存");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存 SOP 配置失败");
    } finally {
      setSaving(false);
    }
  }

  async function appendEventTemplates() {
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const response = await fetch("/api/sop-reply-packs/event-first-add-templates", {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "追加事件专用包失败");
      }
      setConfig(normalizeConfig(data));
      const appended = Array.isArray(data?.appended_pack_ids) ? data.appended_pack_ids.length : 0;
      setStatus(appended > 0 ? `已追加 ${appended} 个事件专用包` : "事件专用包已存在，无需追加");
    } catch (err) {
      setError(err instanceof Error ? err.message : "追加事件专用包失败");
    } finally {
      setSaving(false);
    }
  }

  function addPack() {
    const nextOrder = config.packs.reduce((max, pack) => Math.max(max, pack.order), 0) + 10;
    const pack: SopPack = {
      id: uniquePackId(config.packs, "s10_sop_pack"),
      enabled: false,
      scope: "chat_gate",
      scopes: ["chat_gate"],
      sop_category: "opening",
      name: "新的 SOP 话术包",
      purpose: "",
      order: nextOrder,
      send_once: true,
      event_type: "sop_friend_added_schedule_batch",
      delay_minutes: 0,
      day_stage: "day1",
      customer_state: "",
      stage_tag: "",
      triggers: [],
      reply_messages: [],
    };
    setConfig((current) => ({ ...current, packs: [...current.packs, pack] }));
    setSelectedId(pack.id);
  }

  function duplicatePack(pack: SopPack) {
    const copied: SopPack = {
      ...clone(pack),
      id: uniquePackId(config.packs, `${pack.id}_copy`),
      name: `${pack.name} 副本`,
      enabled: false,
      order: config.packs.reduce((max, item) => Math.max(max, item.order), 0) + 10,
    };
    setConfig((current) => ({ ...current, packs: [...current.packs, copied] }));
    setSelectedId(copied.id);
  }

  function deletePack(packId: string) {
    setConfig((current) => ({
      ...current,
      packs: current.packs.filter((pack) => pack.id !== packId),
    }));
  }

  function updatePack(packId: string, patch: Partial<SopPack>) {
    setConfig((current) => ({
      ...current,
      packs: current.packs.map((pack) => (pack.id === packId ? { ...pack, ...patch } : pack)),
    }));
    if (patch.id && packId === selectedId) {
      setSelectedId(patch.id);
    }
  }

  function updateMessages(packId: string, messages: ReplyMessage[]) {
    updatePack(packId, { reply_messages: reindexMessages(messages) });
  }

  return (
    <main className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="sticky top-12 z-20 border-b bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="outline" size="icon" aria-label="返回">
              <Link href="/">
                <ArrowLeft />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-semibold leading-tight">SOP 话术包配置</h1>
              <p className="text-sm text-zinc-500">配置固定消息顺序、触发标识和发送类型；不使用模板参数。</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={loadConfig} disabled={loading || saving}>
              <RefreshCw className={loading ? "animate-spin" : ""} />
              刷新
            </Button>
            <Button variant="outline" onClick={appendEventTemplates} disabled={loading || saving}>
              <Plus />
              追加事件专用包
            </Button>
            <Button onClick={saveConfig} disabled={saving || loading}>
              <Save />
              {saving ? "保存中" : "保存配置"}
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl grid-cols-[320px_minmax(0,1fr)] gap-5 px-5 py-5">
        <aside className="space-y-3">
          <div className="rounded-lg border bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">话术包</div>
                <div className="text-xs text-zinc-500">{config.packs.length} 个配置项</div>
              </div>
              <Button size="sm" onClick={addPack}>
                <Plus />
                新增
              </Button>
            </div>
          </div>

          <div className="space-y-2">
            {config.packs
              .slice()
              .sort((left, right) => left.order - right.order)
              .map((pack) => (
                <button
                  key={pack.id}
                  type="button"
                  onClick={() => setSelectedId(pack.id)}
                  className={`w-full rounded-lg border bg-white p-3 text-left transition hover:border-zinc-400 ${
                    selectedPack?.id === pack.id ? "border-zinc-950 shadow-sm" : "border-zinc-200"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 truncate text-sm font-semibold">{pack.name || pack.id}</div>
                    <Badge variant={pack.enabled ? "default" : "outline"}>{pack.enabled ? "启用" : "停用"}</Badge>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-xs text-zinc-500">
                    <span>顺序 {pack.order}</span>
                    <span>{pack.reply_messages.length} 条消息</span>
                  </div>
                  <div className="mt-1 truncate text-xs text-zinc-500">{pack.purpose || "未填写目的"}</div>
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
                  {blockingErrors.map((item) => (
                    <div key={item.message}>{item.message}</div>
                  ))}
                </div>
              )}
              {warnings.length > 0 && (
                <div className="mt-2 space-y-1 text-amber-700">
                  {warnings.map((item) => (
                    <div key={item.message}>{item.message}</div>
                  ))}
                </div>
              )}
            </div>
          )}

          {loading ? (
            <div className="rounded-lg border bg-white p-8 text-sm text-zinc-500">正在加载配置...</div>
          ) : selectedPack ? (
            <PackEditor
              pack={selectedPack}
              onChange={(patch) => updatePack(selectedPack.id, patch)}
              onDelete={() => deletePack(selectedPack.id)}
              onDuplicate={() => duplicatePack(selectedPack)}
              onMessagesChange={(messages) => updateMessages(selectedPack.id, messages)}
            />
          ) : (
            <div className="rounded-lg border bg-white p-8 text-sm text-zinc-500">
              还没有 SOP 话术包，点击左侧新增。
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function PackEditor({
  pack,
  onChange,
  onDelete,
  onDuplicate,
  onMessagesChange,
}: {
  pack: SopPack;
  onChange: (patch: Partial<SopPack>) => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onMessagesChange: (messages: ReplyMessage[]) => void;
}) {
  const triggerText = pack.triggers.join("\n");
  const scopes = normalizeScopes(pack);

  function addMessage(type: MessageType = "text") {
    onMessagesChange([...pack.reply_messages, createMessage(type, pack.reply_messages.length + 1)]);
  }

  function updateMessage(index: number, patch: Partial<ReplyMessage>) {
    onMessagesChange(
      pack.reply_messages.map((message, messageIndex) =>
        messageIndex === index ? { ...message, ...patch } : message
      )
    );
  }

  function updateMessageType(index: number, type: MessageType) {
    updateMessage(index, {
      type,
      content: defaultContent(type),
    });
  }

  function updateMessageContent(index: number, patch: Record<string, unknown>) {
    const message = pack.reply_messages[index];
    updateMessage(index, {
      content: { ...message.content, ...patch },
    });
  }

  function deleteMessage(index: number) {
    onMessagesChange(pack.reply_messages.filter((_, messageIndex) => messageIndex !== index));
  }

  function moveMessage(index: number, direction: -1 | 1) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= pack.reply_messages.length) {
      return;
    }
    const messages = pack.reply_messages.slice();
    const [message] = messages.splice(index, 1);
    messages.splice(nextIndex, 0, message);
    onMessagesChange(messages);
  }

  function toggleScope(scope: string, checked: boolean) {
    const nextScopes = checked ? [...scopes, scope] : scopes.filter((item) => item !== scope);
    const normalized = uniqueScopes(nextScopes);
    onChange({ scopes: normalized, scope: normalized[0] || "chat_gate" });
  }

  return (
    <>
      <div className="rounded-lg border bg-white p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">{pack.name || pack.id}</h2>
            <p className="text-sm text-zinc-500">SOP ID: {pack.id}</p>
          </div>
          <div className="flex gap-2">
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

        <div className="mt-5 grid grid-cols-2 gap-4">
          <Field label="ID">
            <Input value={pack.id} onChange={(event) => onChange({ id: cleanIdentifier(event.target.value) })} />
          </Field>
          <Field label="名称">
            <Input value={pack.name} onChange={(event) => onChange({ name: event.target.value })} />
          </Field>
          <Field label="顺序">
            <Input
              type="number"
              min={1}
              value={pack.order}
              onChange={(event) => onChange({ order: positiveNumber(event.target.value, pack.order) })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <ToggleField label="启用" checked={pack.enabled} onCheckedChange={(checked) => onChange({ enabled: checked })} />
            <ToggleField
              label="同客户只发一次"
              checked={pack.send_once}
              onCheckedChange={(checked) => onChange({ send_once: checked })}
            />
          </div>
          <Field label="执行范围">
            <div className="space-y-2 rounded-md border bg-white p-3">
              {SOP_SCOPES.map((item) => (
                <label key={item.value} className="flex items-center justify-between gap-3 text-sm">
                  <span>{item.label}</span>
                  <input
                    type="checkbox"
                    className="size-4 accent-zinc-950"
                    checked={scopes.includes(item.value)}
                    onChange={(event) => toggleScope(item.value, event.target.checked)}
                  />
                </label>
              ))}
            </div>
          </Field>
          <Field label="去重类目">
            <Input
              list="sop-category-options"
              value={pack.sop_category}
              onChange={(event) => onChange({ sop_category: cleanIdentifier(event.target.value) })}
            />
            <datalist id="sop-category-options">
              {SOP_CATEGORIES.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </Field>
          <Field label="目的" className="col-span-2">
            <Textarea
              value={pack.purpose}
              onChange={(event) => onChange({ purpose: event.target.value })}
              className="min-h-20"
            />
          </Field>
          <Field label="企微事件类型">
            <Select
              value={pack.event_type || "__any"}
              onValueChange={(value) => onChange({ event_type: value === "__any" ? "" : value })}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SOP_EVENT_TYPES.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="延迟分钟，0 为不限制">
            <Input
              type="number"
              min={0}
              value={pack.delay_minutes}
              onChange={(event) => onChange({ delay_minutes: nonNegativeNumber(event.target.value, pack.delay_minutes) })}
            />
          </Field>
          <Field label="day_stage">
            <Input value={pack.day_stage} onChange={(event) => onChange({ day_stage: event.target.value })} />
          </Field>
          <Field label="customer_state">
            <Input value={pack.customer_state} onChange={(event) => onChange({ customer_state: event.target.value })} />
          </Field>
          <Field label="stage_tag" className="col-span-2">
            <Input value={pack.stage_tag} onChange={(event) => onChange({ stage_tag: event.target.value })} />
          </Field>
          <Field label="触发标识，一行一个" className="col-span-2">
            <Textarea
              value={triggerText}
              onChange={(event) =>
                onChange({
                  triggers: event.target.value
                    .split(/\r?\n/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
              placeholder="new_customer_added"
              className="min-h-24"
            />
          </Field>
        </div>
      </div>

      <div className="rounded-lg border bg-white p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold">回复消息顺序</h3>
            <p className="text-sm text-zinc-500">这里配置最终发送给客户的固定消息数组，顺序会自动重排。</p>
          </div>
          <div className="flex gap-2">
            <MessageTypeAddButton onSelect={addMessage} />
          </div>
        </div>

        <div className="mt-4 space-y-3">
          {pack.reply_messages.map((message, index) => (
            <div key={`${message.type}-${index}`} className="rounded-lg border border-zinc-200 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <MessageIcon type={message.type} />
                  <Badge variant="outline">#{index + 1}</Badge>
                  <Select value={message.type} onValueChange={(value) => updateMessageType(index, value as MessageType)}>
                    <SelectTrigger className="w-40">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {MESSAGE_TYPES.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex gap-1">
                  <Button variant="ghost" size="icon-sm" onClick={() => moveMessage(index, -1)} disabled={index === 0}>
                    <ChevronUp />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => moveMessage(index, 1)}
                    disabled={index === pack.reply_messages.length - 1}
                  >
                    <ChevronDown />
                  </Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => deleteMessage(index)}>
                    <Trash2 />
                  </Button>
                </div>
              </div>
              <div className="mt-3">
                <MessageContentEditor
                  message={message}
                  onChange={(patch) => updateMessageContent(index, patch)}
                />
              </div>
            </div>
          ))}
          {pack.reply_messages.length === 0 && (
            <div className="rounded-lg border border-dashed p-8 text-center text-sm text-zinc-500">
              还没有消息。新增文本、图片、视频、预约金、门店地址卡或专业协助消息。
            </div>
          )}
        </div>
      </div>

      <div className="rounded-lg border bg-white p-5">
        <h3 className="font-semibold">当前 reply_messages 预览</h3>
        <pre className="mt-3 max-h-[360px] overflow-auto rounded-md bg-zinc-950 p-4 text-xs text-zinc-50">
          {JSON.stringify(pack.reply_messages, null, 2)}
        </pre>
      </div>
    </>
  );
}

function MessageTypeAddButton({ onSelect }: { onSelect: (type: MessageType) => void }) {
  return (
    <div className="flex flex-wrap justify-end gap-2">
      {MESSAGE_TYPES.map((item) => (
        <Button key={item.value} type="button" variant="outline" size="sm" onClick={() => onSelect(item.value)}>
          <Plus />
          {item.label}
        </Button>
      ))}
    </div>
  );
}

function MessageContentEditor({
  message,
  onChange,
}: {
  message: ReplyMessage;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  if (message.type === "image" || message.type === "video") {
    return (
      <MediaMessageEditor
        mediaType={message.type}
        url={stringValue(message.content.url)}
        storageKey={stringValue(message.content.key)}
        onChange={onChange}
      />
    );
  }

  if (message.type === "payment_collection") {
    return (
      <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-3">
        <Field label="金额">
          <Input
            type="number"
            min={1}
            value={numberValue(message.content.amount, 10)}
            onChange={(event) => onChange({ amount: positiveNumber(event.target.value, 10) })}
          />
        </Field>
        <Field label="备注">
          <Input value={stringValue(message.content.remark)} onChange={(event) => onChange({ remark: event.target.value })} />
        </Field>
      </div>
    );
  }

  if (message.type === "store_address") {
    return (
      <Field label="固定门店 ID">
        <Input
          value={stringValue(message.content.store_id)}
          onChange={(event) => onChange({ store_id: event.target.value })}
          placeholder="例如 467"
        />
      </Field>
    );
  }

  if (message.type === "human_handoff" || message.type === "human_handoff_notice") {
    return (
      <Field label="专业协助原因">
        <Textarea
          value={stringValue(message.content.handoff_reason)}
          onChange={(event) => onChange({ handoff_reason: event.target.value })}
        />
      </Field>
    );
  }

  return (
    <Field label="文本">
      <Textarea
        value={stringValue(message.content.text)}
        onChange={(event) => onChange({ text: event.target.value })}
        className="min-h-24"
      />
    </Field>
  );
}

function MediaMessageEditor({
  mediaType,
  url,
  storageKey,
  onChange,
}: {
  mediaType: "image" | "video";
  url: string;
  storageKey: string;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");

  async function uploadFile(file: File | undefined) {
    if (!file) {
      return;
    }
    setUploading(true);
    setUploadError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });
      const responseText = await response.text();
      let data: Record<string, unknown> = {};
      if (responseText) {
        try {
          data = JSON.parse(responseText) as Record<string, unknown>;
        } catch {
          throw new Error(responseText.slice(0, 200));
        }
      }
      if (!response.ok || !data?.url) {
        throw new Error(
          typeof data?.error === "string"
            ? data.error
            : mediaType === "video"
              ? "视频上传失败"
              : "图片上传失败"
        );
      }
      onChange({ url: String(data.url), key: data.key ? String(data.key) : "" });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : mediaType === "video" ? "视频上传失败" : "图片上传失败");
    } finally {
      setUploading(false);
    }
  }

  const label = mediaType === "video" ? "视频 URL" : "图片 URL";
  const uploadLabel = mediaType === "video" ? "上传本地视频" : "上传本地图片";
  const accept = mediaType === "video" ? "video/*" : "image/*";

  return (
    <div className="space-y-3">
      <Field label={label}>
        <Input value={url} onChange={(event) => onChange({ url: event.target.value })} />
      </Field>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-3">
        <Field label={uploadLabel}>
          <Input
            type="file"
            accept={accept}
            disabled={uploading}
            onChange={(event) => void uploadFile(event.target.files?.[0])}
          />
        </Field>
        <div className="flex h-9 items-center gap-2 rounded-md border px-3 text-sm text-zinc-600">
          {uploading ? <LoaderCircle className="animate-spin" /> : <Upload />}
          {uploading ? "上传中" : "选择后自动填 URL"}
        </div>
      </div>
      {storageKey && <div className="text-xs text-zinc-500">存储 key: {storageKey}</div>}
      {uploadError && <div className="text-sm text-red-600">{uploadError}</div>}
      {url && (
        <div className="rounded-md border bg-zinc-50 p-3">
          {mediaType === "video" ? (
            <video src={url} controls className="max-h-64 rounded object-contain" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={url} alt="SOP 图片预览" className="max-h-48 rounded object-contain" />
          )}
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={className}>
      <Label className="mb-2 text-xs text-zinc-500">{label}</Label>
      {children}
    </div>
  );
}

function ToggleField({
  label,
  checked,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex h-full items-center justify-between rounded-md border px-3 py-2">
      <Label className="text-sm">{label}</Label>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

function MessageIcon({ type }: { type: MessageType }) {
  if (type === "image") return <ImageIcon className="size-4 text-zinc-500" />;
  if (type === "video") return <Video className="size-4 text-zinc-500" />;
  if (type === "payment_collection") return <WalletCards className="size-4 text-zinc-500" />;
  if (type === "store_address") return <MapPin className="size-4 text-zinc-500" />;
  if (type === "human_handoff" || type === "human_handoff_notice") return <Handshake className="size-4 text-zinc-500" />;
  return <span className="flex size-4 items-center justify-center text-xs text-zinc-500">T</span>;
}

function normalizeConfig(value: unknown): SopConfig {
  if (!isRecord(value)) return EMPTY_CONFIG;
  const packs = Array.isArray(value.packs) ? value.packs.map(normalizePack).filter(Boolean) : [];
  return {
    version: numberValue(value.version, 1),
    updated_at: stringValue(value.updated_at),
    packs,
    audit: normalizeAudit(value.audit),
  };
}

function normalizeAudit(value: unknown): SopAudit | undefined {
  if (!isRecord(value)) return undefined;
  const issues = Array.isArray(value.issues)
    ? value.issues.map(normalizeAuditIssue).filter(Boolean)
    : [];
  const status = stringValue(value.status);
  return {
    status: status === "error" || status === "warning" ? status : "ok",
    error_count: numberValue(value.error_count, issues.filter((item) => item.severity === "error").length),
    warning_count: numberValue(value.warning_count, issues.filter((item) => item.severity === "warning").length),
    issues,
  };
}

function normalizeAuditIssue(value: unknown): SopAuditIssue {
  const record = isRecord(value) ? value : {};
  const severity = stringValue(record.severity) === "error" ? "error" : "warning";
  return {
    severity,
    code: stringValue(record.code),
    pack_id: stringValue(record.pack_id),
    message: stringValue(record.message),
    message_order: numberValue(record.message_order, 0) || undefined,
  };
}

function normalizePack(value: unknown): SopPack {
  const record = isRecord(value) ? value : {};
  const messages = Array.isArray(record.reply_messages)
    ? record.reply_messages.map(normalizeMessage).filter(Boolean)
    : [];
  return {
    id: cleanIdentifier(stringValue(record.id) || "sop_pack"),
    enabled: Boolean(record.enabled),
    scope: normalizeScopes(record)[0],
    scopes: normalizeScopes(record),
    sop_category: cleanIdentifier(stringValue(record.sop_category) || stringValue(record.id) || "sop_pack"),
    name: stringValue(record.name),
    purpose: stringValue(record.purpose),
    order: numberValue(record.order, 10),
    send_once: record.send_once !== false,
    event_type: stringValue(record.event_type),
    delay_minutes: nonNegativeNumber(record.delay_minutes, 0),
    day_stage: stringValue(record.day_stage),
    customer_state: stringValue(record.customer_state),
    stage_tag: stringValue(record.stage_tag),
    triggers: Array.isArray(record.triggers) ? record.triggers.map(stringValue).filter(Boolean) : [],
    reply_messages: reindexMessages(messages),
  };
}

function normalizeScopes(value: unknown): string[] {
  const record = isRecord(value) ? value : {};
  const values = Array.isArray(record.scopes) ? record.scopes : [record.scope];
  return uniqueScopes(values);
}

function uniqueScopes(values: unknown[]): string[] {
  const allowed = new Set(SOP_SCOPES.map((item) => item.value));
  const scopes: string[] = [];
  for (const value of values) {
    const scope = stringValue(value);
    if (allowed.has(scope) && !scopes.includes(scope)) {
      scopes.push(scope);
    }
  }
  return scopes.length ? scopes : ["chat_gate"];
}

function normalizeMessage(value: unknown): ReplyMessage {
  const record = isRecord(value) ? value : {};
  const type = MESSAGE_TYPES.some((item) => item.value === record.type) ? (record.type as MessageType) : "text";
  return {
    type,
    order: numberValue(record.order, 1),
    content: isRecord(record.content) ? { ...record.content } : defaultContent(type),
  };
}

function validateConfig(config: SopConfig): Array<{ level: "error" | "warning"; message: string }> {
  const diagnostics: Array<{ level: "error" | "warning"; message: string }> = [];
  const ids = new Set<string>();
  for (const pack of config.packs) {
    if (!pack.id) diagnostics.push({ level: "error", message: "存在空 SOP ID" });
    if (ids.has(pack.id)) diagnostics.push({ level: "error", message: `SOP ID 重复：${pack.id}` });
    ids.add(pack.id);
    if (packContainsTemplateMarker(pack)) {
      diagnostics.push({ level: "error", message: `${pack.name || pack.id} 包含 {{ }} 模板占位符，请改成固定内容` });
    }
    if (pack.enabled && pack.reply_messages.length === 0) {
      diagnostics.push({ level: "warning", message: `${pack.name || pack.id} 已启用但没有消息` });
    }
    for (const message of pack.reply_messages) {
      if (!messageContentFilled(message)) {
        diagnostics.push({ level: "warning", message: `${pack.name || pack.id} 的第 ${message.order} 条消息内容为空` });
      }
    }
  }
  return diagnostics;
}

function auditDiagnostics(audit: SopAudit | undefined): Array<{ level: "error" | "warning"; message: string }> {
  if (!audit?.issues?.length) return [];
  return audit.issues.map((issue) => {
    const position = [issue.pack_id, issue.message_order ? `#${issue.message_order}` : ""].filter(Boolean).join(" ");
    return {
      level: issue.severity,
      message: position ? `${position}: ${issue.message}` : issue.message,
    };
  });
}

function createMessage(type: MessageType, order: number): ReplyMessage {
  return {
    type,
    order,
    content: defaultContent(type),
  };
}

function defaultContent(type: MessageType): Record<string, unknown> {
  if (type === "image" || type === "video") return { url: "", key: "" };
  if (type === "payment_collection") return { amount: 10, remark: "" };
  if (type === "store_address") return { store_id: "" };
  if (type === "human_handoff" || type === "human_handoff_notice") return { handoff_reason: "" };
  return { text: "" };
}

function reindexMessages(messages: ReplyMessage[]) {
  return messages.map((message, index) => ({ ...message, order: index + 1 }));
}

function messageContentFilled(message: ReplyMessage) {
  if (message.type === "payment_collection") return numberValue(message.content.amount, 10) > 0;
  if (message.type === "store_address") return stringValue(message.content.store_id) !== "";
  if (message.type === "image" || message.type === "video") return stringValue(message.content.url) !== "";
  if (message.type === "human_handoff" || message.type === "human_handoff_notice") return stringValue(message.content.handoff_reason) !== "";
  return stringValue(message.content.text) !== "";
}

function packContainsTemplateMarker(pack: SopPack) {
  const values = [
    pack.name,
    pack.purpose,
    pack.scope,
    ...pack.scopes,
    pack.sop_category,
    pack.event_type,
    pack.day_stage,
    pack.customer_state,
    pack.stage_tag,
    ...pack.triggers,
  ];
  for (const message of pack.reply_messages) {
    for (const value of Object.values(message.content)) {
      values.push(String(value ?? ""));
    }
  }
  return values.some((value) => value.includes("{{") || value.includes("}}"));
}

function uniquePackId(packs: SopPack[], baseId: string) {
  const existing = new Set(packs.map((pack) => pack.id));
  let next = cleanIdentifier(baseId) || "sop_pack";
  let index = 2;
  while (existing.has(next)) {
    next = `${cleanIdentifier(baseId)}_${index}`;
    index += 1;
  }
  return next;
}

function cleanIdentifier(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, "");
}

function positiveNumber(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function nonNegativeNumber(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
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
