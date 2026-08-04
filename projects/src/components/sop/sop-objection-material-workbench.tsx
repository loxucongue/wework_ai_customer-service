"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Plus, RefreshCw, Save, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

type Material = {
  material_id: string;
  name: string;
  category: string;
  tags: string[];
  applicable_scenes: string[];
  response_approach: string;
  example_contents: string[];
};

type MaterialConfig = {
  version: number;
  updated_at: string;
  materials: Material[];
};

const EMPTY_CONFIG: MaterialConfig = { version: 1, updated_at: "", materials: [] };

export function SopObjectionMaterialWorkbench() {
  const [config, setConfig] = useState<MaterialConfig>(EMPTY_CONFIG);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const selected = useMemo(
    () => config.materials.find((item) => item.material_id === selectedId),
    [config.materials, selectedId]
  );

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/sop-objection-materials", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || payload?.error || "加载素材库失败");
      const normalized = normalizeConfig(payload);
      setConfig(normalized);
      setSelectedId((current) =>
        normalized.materials.some((item) => item.material_id === current)
          ? current
          : normalized.materials[0]?.material_id || ""
      );
      setNotice("已加载最新素材库");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载素材库失败");
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    const duplicate = findDuplicateId(config.materials);
    if (duplicate) {
      setError(`素材 ID 重复：${duplicate}`);
      return;
    }
    if (config.materials.some((item) => !item.material_id.trim())) {
      setError("每条素材都必须填写素材 ID");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/sop-objection-materials", {
        method: "PUT",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(config),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || payload?.error || "保存素材库失败");
      setConfig(normalizeConfig(payload));
      setNotice("已保存，后续第三方 SOP 判断会读取这份素材库");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存素材库失败");
    } finally {
      setSaving(false);
    }
  }

  function addMaterial() {
    const materialId = uniqueId(config.materials, "material");
    const material: Material = {
      material_id: materialId,
      name: "新素材",
      category: "",
      tags: [],
      applicable_scenes: [],
      response_approach: "",
      example_contents: [],
    };
    setConfig((current) => ({ ...current, materials: [...current.materials, material] }));
    setSelectedId(materialId);
  }

  function updateSelected(patch: Partial<Material>) {
    setConfig((current) => ({
      ...current,
      materials: current.materials.map((item) =>
        item.material_id === selectedId ? { ...item, ...patch } : item
      ),
    }));
    if (patch.material_id) setSelectedId(patch.material_id);
  }

  function removeSelected() {
    if (!selected) return;
    const remaining = config.materials.filter((item) => item.material_id !== selected.material_id);
    setConfig((current) => ({ ...current, materials: remaining }));
    setSelectedId(remaining[0]?.material_id || "");
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <header className="border-b bg-white px-5 py-4">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="outline" size="icon" title="返回第三方 SOP 任务">
              <Link href="/logs/sop-platform"><ArrowLeft /></Link>
            </Button>
            <div>
              <h1 className="text-lg font-semibold">SOP 异议素材库</h1>
              <p className="text-sm text-slate-500">供第三方 SOP 的 AI 改写任务选择，不参与普通 AI 回复主线配置。</p>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={loading ? "animate-spin" : ""} />刷新
            </Button>
            <Button onClick={() => void save()} disabled={saving}>
              <Save />{saving ? "保存中" : "保存"}
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-5 p-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="overflow-hidden rounded-md border bg-white">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div>
              <div className="font-medium">素材切片</div>
              <div className="text-xs text-slate-500">共 {config.materials.length} 条</div>
            </div>
            <Button size="icon" variant="outline" onClick={addMaterial} title="新增素材"><Plus /></Button>
          </div>
          <div className="max-h-[calc(100vh-190px)] overflow-y-auto p-2">
            {config.materials.map((item) => (
              <button
                key={item.material_id}
                type="button"
                onClick={() => setSelectedId(item.material_id)}
                className={`mb-2 w-full rounded-md border px-3 py-3 text-left ${
                  selectedId === item.material_id ? "border-slate-950 bg-slate-50" : "hover:bg-slate-50"
                }`}
              >
                <div className="truncate text-sm font-medium">{item.name || item.material_id}</div>
                <div className="mt-1 truncate text-xs text-slate-500">{item.material_id}</div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {item.category ? <Badge variant="secondary">{item.category}</Badge> : null}
                  {item.tags.slice(0, 2).map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}
                </div>
              </button>
            ))}
            {!config.materials.length ? (
              <div className="px-3 py-10 text-center text-sm text-slate-500">素材库为空，可新增第一条素材。</div>
            ) : null}
          </div>
        </aside>

        <section className="rounded-md border bg-white p-5">
          {selected ? (
            <div className="space-y-5">
              <div className="flex items-start justify-between gap-3 border-b pb-4">
                <div>
                  <h2 className="font-semibold">编辑素材切片</h2>
                  <p className="mt-1 text-sm text-slate-500">示例用于启发模型，不是强制逐字发送。</p>
                </div>
                <Button variant="outline" onClick={removeSelected} className="text-red-600"><Trash2 />删除</Button>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="素材 ID"><Input value={selected.material_id} onChange={(e) => updateSelected({ material_id: e.target.value })} /></Field>
                <Field label="名称"><Input value={selected.name} onChange={(e) => updateSelected({ name: e.target.value })} /></Field>
                <Field label="标签分类"><Input value={selected.category} onChange={(e) => updateSelected({ category: e.target.value })} placeholder="例如：价格顾虑" /></Field>
                <Field label="标签（逗号分隔）"><Input value={selected.tags.join(", ")} onChange={(e) => updateSelected({ tags: splitList(e.target.value, ",") })} placeholder="价格, 信任, 低风险" /></Field>
              </div>
              <Field label="应对场景（每行一个）">
                <Textarea rows={4} value={selected.applicable_scenes.join("\n")} onChange={(e) => updateSelected({ applicable_scenes: splitList(e.target.value, "\n") })} />
              </Field>
              <Field label="应对思路">
                <Textarea rows={5} value={selected.response_approach} onChange={(e) => updateSelected({ response_approach: e.target.value })} placeholder="说明模型应如何理解客户心理、先解决什么、如何自然承接。" />
              </Field>
              <Field label="示例内容（每行一条）">
                <Textarea rows={8} value={selected.example_contents.join("\n")} onChange={(e) => updateSelected({ example_contents: splitList(e.target.value, "\n") })} />
              </Field>
            </div>
          ) : (
            <div className="flex min-h-[420px] items-center justify-center text-sm text-slate-500">选择或新增一条素材后编辑。</div>
          )}
          {notice ? <p className="mt-5 text-sm text-emerald-700">{notice}</p> : null}
          {error ? <p className="mt-5 text-sm text-red-600">{error}</p> : null}
        </section>
      </div>
    </main>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="space-y-2"><Label>{label}</Label>{children}</div>;
}

function normalizeConfig(payload: Partial<MaterialConfig>): MaterialConfig {
  return {
    version: Number(payload.version || 1),
    updated_at: String(payload.updated_at || ""),
    materials: Array.isArray(payload.materials)
      ? payload.materials.map((item) => ({
          material_id: String(item.material_id || ""),
          name: String(item.name || item.material_id || ""),
          category: String(item.category || ""),
          tags: Array.isArray(item.tags) ? item.tags.map(String) : [],
          applicable_scenes: Array.isArray(item.applicable_scenes) ? item.applicable_scenes.map(String) : [],
          response_approach: String(item.response_approach || ""),
          example_contents: Array.isArray(item.example_contents) ? item.example_contents.map(String) : [],
        }))
      : [],
  };
}

function splitList(value: string, separator: string) {
  return value.split(separator).map((item) => item.trim()).filter(Boolean);
}

function uniqueId(materials: Material[], prefix: string) {
  const ids = new Set(materials.map((item) => item.material_id));
  let index = materials.length + 1;
  while (ids.has(`${prefix}_${index}`)) index += 1;
  return `${prefix}_${index}`;
}

function findDuplicateId(materials: Material[]) {
  const seen = new Set<string>();
  for (const item of materials) {
    const id = item.material_id.trim();
    if (seen.has(id)) return id;
    seen.add(id);
  }
  return "";
}
