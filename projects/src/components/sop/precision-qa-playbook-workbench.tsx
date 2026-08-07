"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, Copy, ImagePlus, Plus, RefreshCw, Save, Search, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type MessageType = "text" | "image" | "image_reference" | "video_reference" | "media_reference";
type ReplyMessage = { type: MessageType; content: string; source_missing?: boolean };
type BlockerItem = { blocker_type: string; applicable_scene: string; content_id: string; reply_messages: ReplyMessage[] };
type Config = {
  version: number;
  updated_at: string;
  items: BlockerItem[];
  audit?: { warning_count?: number; error_count?: number; issues?: Array<{ content_id?: string; message?: string }> };
};

const EMPTY: Config = { version: 4, updated_at: "", items: [] };
const MESSAGE_TYPES: MessageType[] = ["text", "image", "image_reference", "video_reference", "media_reference"];

export function PrecisionQaPlaybookWorkbench() {
  const [config, setConfig] = useState<Config>(EMPTY);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sceneFilter, setSceneFilter] = useState("");
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState<BlockerItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const types = useMemo(() => [...new Set(config.items.map((item) => item.blocker_type))].sort(), [config.items]);
  const scenes = useMemo(() => [...new Set(config.items.map((item) => item.applicable_scene))].sort(), [config.items]);
  const filtered = useMemo(() => config.items.filter((item) => {
    const text = `${item.content_id} ${item.blocker_type} ${item.applicable_scene} ${item.reply_messages.map((message) => message.content).join(" ")}`.toLowerCase();
    return (!query.trim() || text.includes(query.trim().toLowerCase()))
      && (!typeFilter || item.blocker_type === typeFilter)
      && (!sceneFilter || item.applicable_scene === sceneFilter);
  }), [config.items, query, sceneFilter, typeFilter]);

  const missingCount = config.items.flatMap((item) => item.reply_messages).filter((message) => message.source_missing).length;
  const imageCount = config.items.flatMap((item) => item.reply_messages).filter((message) => message.type === "image").length;

  async function load() {
    setLoading(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/precision-qa-playbook", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || payload.error || "加载预约卡点话术失败");
      setConfig(normalize(payload));
      setNotice("已加载线上配置");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "加载失败"); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, []);

  async function save() {
    const validation = validate(config);
    if (validation) { setError(validation); return; }
    setSaving(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/precision-qa-playbook", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || payload.error || "保存失败");
      setConfig(normalize(payload)); setNotice("已保存，模型节点将读取新配置");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "保存失败"); }
    finally { setSaving(false); }
  }

  function openEditor(item: BlockerItem) { setEditingId(item.content_id); setDraft(structuredClone(item)); }
  function commitDraft() {
    if (!draft) return;
    const itemError = validateItem(draft);
    if (itemError) { setError(itemError); return; }
    const duplicate = config.items.some((item) => item.content_id === draft.content_id && item.content_id !== editingId);
    if (duplicate) { setError(`内容编号 ${draft.content_id} 已存在`); return; }
    setConfig((current) => ({ ...current, items: current.items.map((item) => item.content_id === editingId ? draft : item) }));
    setDraft(null); setEditingId(""); setError("");
  }
  function addItem() {
    const number = Math.max(0, ...config.items.map((item) => Number(item.content_id.replace(/\D/g, "")) || 0)) + 1;
    const item: BlockerItem = { blocker_type: "", applicable_scene: "", content_id: `YYHF-${String(number).padStart(4, "0")}`, reply_messages: [{ type: "text", content: "" }] };
    setConfig((current) => ({ ...current, items: [...current.items, item] })); openEditor(item);
  }
  function duplicate(item: BlockerItem) {
    const number = Math.max(0, ...config.items.map((candidate) => Number(candidate.content_id.replace(/\D/g, "")) || 0)) + 1;
    const copy = { ...structuredClone(item), content_id: `YYHF-${String(number).padStart(4, "0")}` };
    setConfig((current) => ({ ...current, items: [...current.items, copy] })); openEditor(copy);
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-4 lg:p-6">
      <div className="flex flex-col justify-between gap-3 border-b pb-4 lg:flex-row lg:items-end">
        <div><h2 className="text-base font-semibold">预约卡点话术库</h2><p className="mt-1 text-sm text-zinc-500">Gate 只读取适用场景，Reply 在命中后参考同场景候选内容。</p></div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "animate-spin" : ""} />刷新</Button>
          <Button variant="outline" onClick={addItem}><Plus />新增</Button>
          <Button onClick={() => void save()} disabled={saving}><Save />{saving ? "保存中" : "保存配置"}</Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Summary label="话术条目" value={config.items.length} />
        <Summary label="有效图片实例" value={imageCount} />
        <Summary label="缺失媒体" value={missingCount} warning />
      </div>

      {(notice || error) && <div className={`rounded-md border px-3 py-2 text-sm ${error ? "border-red-200 bg-red-50 text-red-700" : "border-emerald-200 bg-emerald-50 text-emerald-700"}`}>{error || notice}</div>}

      <div className="grid gap-2 rounded-md border bg-white p-3 md:grid-cols-[minmax(240px,1fr)_220px_320px]">
        <div className="relative"><Search className="absolute left-3 top-2.5 size-4 text-zinc-400" /><Input className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索编号、场景或话术" /></div>
        <select className="h-9 rounded-md border bg-white px-3 text-sm" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">全部卡点类型</option>{types.map((value) => <option key={value}>{value}</option>)}</select>
        <select className="h-9 min-w-0 rounded-md border bg-white px-3 text-sm" value={sceneFilter} onChange={(event) => setSceneFilter(event.target.value)}><option value="">全部适用场景</option>{scenes.map((value) => <option key={value}>{value}</option>)}</select>
      </div>

      <div className="space-y-2 md:hidden">
        {filtered.map((item) => {
          const missing = item.reply_messages.filter((message) => message.source_missing).length;
          return <article key={item.content_id} className="rounded-md border bg-white p-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-medium">{item.blocker_type}</span><span className="font-mono text-xs text-zinc-500">{item.content_id}</span></div><p className="mt-2 text-sm text-zinc-600">{item.applicable_scene}</p></div><Button size="icon" variant="ghost" onClick={() => openEditor(item)} title="编辑"><Search /></Button></div><div className="mt-3 line-clamp-3 whitespace-pre-wrap border-t pt-3 text-sm text-zinc-500">{item.reply_messages.map((message) => message.type === "text" ? message.content : `[${message.type}] ${message.content}`).join("\n")}</div>{missing > 0 && <Badge variant="outline" className="mt-2 border-amber-300 text-amber-700"><AlertTriangle />{missing} 个媒体缺失</Badge>}<div className="mt-2 flex justify-end gap-1"><Button size="sm" variant="ghost" onClick={() => duplicate(item)}><Copy />复制</Button><Button size="sm" variant="ghost" onClick={() => setConfig((current) => ({ ...current, items: current.items.filter((candidate) => candidate.content_id !== item.content_id) }))}><Trash2 />删除</Button></div></article>;
        })}
      </div>

      <div className="hidden overflow-hidden rounded-md border bg-white md:block">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] table-fixed text-left text-sm">
            <thead className="border-b bg-zinc-50 text-xs text-zinc-500"><tr><th className="w-36 px-3 py-3">卡点类型</th><th className="w-[34%] px-3 py-3">适用场景</th><th className="w-32 px-3 py-3">内容编号</th><th className="px-3 py-3">话术内容</th><th className="w-28 px-3 py-3 text-right">操作</th></tr></thead>
            <tbody className="divide-y">{filtered.map((item) => {
              const missing = item.reply_messages.filter((message) => message.source_missing).length;
              return <tr key={item.content_id} className="align-top hover:bg-zinc-50/70"><td className="px-3 py-3 font-medium">{item.blocker_type}</td><td className="px-3 py-3 text-zinc-600">{item.applicable_scene}</td><td className="px-3 py-3 font-mono text-xs">{item.content_id}</td><td className="px-3 py-3"><div className="line-clamp-3 whitespace-pre-wrap text-zinc-600">{item.reply_messages.map((message) => message.type === "text" ? message.content : `[${message.type}] ${message.content}`).join("\n")}</div>{missing > 0 && <Badge variant="outline" className="mt-2 border-amber-300 text-amber-700"><AlertTriangle />{missing} 个媒体缺失</Badge>}</td><td className="px-3 py-3"><div className="flex justify-end gap-1"><Button size="icon" variant="ghost" onClick={() => openEditor(item)} title="编辑"><Search /></Button><Button size="icon" variant="ghost" onClick={() => duplicate(item)} title="复制"><Copy /></Button><Button size="icon" variant="ghost" onClick={() => setConfig((current) => ({ ...current, items: current.items.filter((candidate) => candidate.content_id !== item.content_id) }))} title="删除"><Trash2 /></Button></div></td></tr>;
            })}</tbody>
          </table>
        </div>
        {!loading && filtered.length === 0 && <div className="p-10 text-center text-sm text-zinc-400">没有符合条件的话术</div>}
      </div>

      <EditorDialog draft={draft} onChange={setDraft} onClose={() => { setDraft(null); setEditingId(""); }} onSave={commitDraft} />
    </div>
  );
}

function EditorDialog({ draft, onChange, onClose, onSave }: { draft: BlockerItem | null; onChange: (value: BlockerItem | null) => void; onClose: () => void; onSave: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadingIndex, setUploadingIndex] = useState(-1);
  if (!draft) return null;
  const update = (patch: Partial<BlockerItem>) => onChange({ ...draft, ...patch });
  const updateMessage = (index: number, patch: Partial<ReplyMessage>) => update({ reply_messages: draft.reply_messages.map((message, current) => current === index ? { ...message, ...patch } : message) });
  const move = (index: number, direction: -1 | 1) => { const messages = [...draft.reply_messages]; const target = index + direction; if (target < 0 || target >= messages.length) return; [messages[index], messages[target]] = [messages[target], messages[index]]; update({ reply_messages: messages }); };
  async function upload(file: File | undefined, index: number) {
    if (!file) return; setUploadingIndex(index);
    try { const body = new FormData(); body.append("file", file); const response = await fetch("/api/upload?scope=outreach&requireOss=1", { method: "POST", body }); const result = await response.json(); if (!response.ok || !result.url || result.storage !== "oss") throw new Error(result.error || "OSS 上传失败"); updateMessage(index, { type: "image", content: result.url, source_missing: false }); } finally { setUploadingIndex(-1); if (fileRef.current) fileRef.current.value = ""; }
  }
  return <Dialog open onOpenChange={(open) => !open && onClose()}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"><DialogHeader><DialogTitle>编辑预约卡点话术</DialogTitle><DialogDescription>四个业务字段与 Excel 结构一致；消息顺序即 Reply 的参考顺序。</DialogDescription></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><Field label="卡点类型"><Input value={draft.blocker_type} onChange={(event) => update({ blocker_type: event.target.value })} /></Field><Field label="内容编号"><Input value={draft.content_id} onChange={(event) => update({ content_id: event.target.value })} /></Field><Field label="适用场景" wide><Textarea rows={3} value={draft.applicable_scene} onChange={(event) => update({ applicable_scene: event.target.value })} /></Field></div><div className="space-y-3"><div className="flex items-center justify-between"><div className="text-sm font-medium">话术内容</div><Button size="sm" variant="outline" onClick={() => update({ reply_messages: [...draft.reply_messages, { type: "text", content: "" }] })}><Plus />添加消息</Button></div>{draft.reply_messages.map((message, index) => <div key={`${index}-${message.type}`} className="rounded-md border p-3"><div className="mb-2 flex flex-wrap items-center gap-2"><select className="h-8 rounded-md border px-2 text-sm" value={message.type} onChange={(event) => updateMessage(index, { type: event.target.value as MessageType, source_missing: event.target.value.endsWith("_reference") || event.target.value === "media_reference" ? message.source_missing : false })}>{MESSAGE_TYPES.map((type) => <option key={type}>{type}</option>)}</select>{message.source_missing && <Badge variant="outline" className="border-amber-300 text-amber-700"><AlertTriangle />源文件缺失，不发送</Badge>}<div className="ml-auto flex gap-1"><Button size="icon" variant="ghost" onClick={() => move(index, -1)} disabled={index === 0}><ChevronUp /></Button><Button size="icon" variant="ghost" onClick={() => move(index, 1)} disabled={index === draft.reply_messages.length - 1}><ChevronDown /></Button><Button size="icon" variant="ghost" onClick={() => update({ reply_messages: draft.reply_messages.filter((_, current) => current !== index) })}><Trash2 /></Button></div></div>{message.type === "text" ? <Textarea rows={5} value={message.content} onChange={(event) => updateMessage(index, { content: event.target.value })} /> : <><Input value={message.content} onChange={(event) => updateMessage(index, { content: event.target.value })} placeholder="OSS URL 或缺失媒体名称" /><div className="mt-2 flex items-center gap-2"><input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(event) => void upload(event.target.files?.[0], index)} /><Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={uploadingIndex === index}><ImagePlus />{uploadingIndex === index ? "上传中" : "上传图片到 OSS"}</Button>{message.type === "image" && message.content && <img src={message.content} alt="话术素材预览" className="h-16 w-16 rounded-md border object-cover" />}</div></>}</div>)}</div><DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button onClick={onSave}>应用修改</Button></DialogFooter></DialogContent></Dialog>;
}

function Field({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) { return <label className={wide ? "sm:col-span-2" : ""}><span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>{children}</label>; }
function Summary({ label, value, warning = false }: { label: string; value: number; warning?: boolean }) { return <div className="rounded-md border bg-white p-3"><div className="text-xs text-zinc-500">{label}</div><div className={`mt-1 text-xl font-semibold tabular-nums ${warning && value ? "text-amber-700" : ""}`}>{value}</div></div>; }
function normalize(payload: Partial<Config>): Config { return { version: Number(payload.version || 4), updated_at: String(payload.updated_at || ""), items: Array.isArray(payload.items) ? payload.items : [], audit: payload.audit }; }
function validate(config: Config) { const ids = new Set<string>(); for (const item of config.items) { const issue = validateItem(item); if (issue) return issue; if (ids.has(item.content_id)) return `内容编号 ${item.content_id} 重复`; ids.add(item.content_id); } return ""; }
function validateItem(item: BlockerItem) { if (!item.blocker_type.trim() || !item.applicable_scene.trim() || !item.content_id.trim()) return "卡点类型、适用场景和内容编号不能为空"; if (!item.reply_messages.length || item.reply_messages.some((message) => !message.content.trim())) return `${item.content_id} 的话术消息不能为空`; return ""; }
