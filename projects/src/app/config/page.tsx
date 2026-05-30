"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { PriceConfig } from "@/types/config";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import {
  ArrowLeft,
  Plus,
  Pencil,
  Trash2,
  Search,
  RefreshCw,
} from "lucide-react";

/** 调用配置工作流（原始响应） */
async function callConfigWorkflowRaw(input: string): Promise<unknown> {
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = (err as { error?: string; detail?: string }).error
      || (err as { detail?: string }).detail
      || "请求失败";
    throw new Error(detail);
  }
  return res.json();
}

/** 调用配置工作流，返回行数组（用于查询） */
async function callConfigQuery(input: string): Promise<Record<string, unknown>[]> {
  const json = await callConfigWorkflowRaw(input);
  // 工作流返回格式：{ data: "..." } 或 { output: [...] }
  const data = (json as { data?: string; output?: unknown }).data;
  if (typeof data === "string") {
    try {
      const parsed = JSON.parse(data);
      if (Array.isArray(parsed)) return parsed as Record<string, unknown>[];
      if (parsed.output && Array.isArray(parsed.output)) return parsed.output as Record<string, unknown>[];
      return [parsed];
    } catch {
      return [];
    }
  }
  if (Array.isArray((json as Record<string, unknown>).output)) {
    return (json as { output: Record<string, unknown>[] }).output;
  }
  if (Array.isArray(json)) return json as Record<string, unknown>[];
  return [];
}

/** 调用配置工作流（增删改），检查是否成功 */
async function callConfigMutation(input: string): Promise<void> {
  const json = await callConfigWorkflowRaw(input) as Record<string, unknown>;
  console.log("[callConfigMutation] Response:", JSON.stringify(json).slice(0, 500));
  // 检查工作流是否返回了错误
  const code = json.code;
  if (code != null && code !== 0) {
    const msg = json.msg || json.message || "操作失败";
    throw new Error(String(msg));
  }
}

/** 安全转为数字，兼容字符串 / 数字 / null */
function toNum(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") { const n = Number(v); return Number.isNaN(n) ? 0 : n; }
  return 0;
}
function toNumOpt(v: unknown): number | undefined {
  if (v == null || v === "") return undefined;
  if (typeof v === "number") return v;
  if (typeof v === "string") { const n = Number(v); return Number.isNaN(n) ? undefined : n; }
  return undefined;
}
function toStr(v: unknown): string | undefined {
  if (v == null || v === "") return undefined;
  return String(v);
}
function toBool(v: unknown): boolean {
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return v === "true" || v === "1";
  return true;
}

/** 将工作流返回的行映射为 PriceConfig */
function mapRow(row: Record<string, unknown>): PriceConfig {
  return {
    id: row.id != null ? String(row.id) : undefined,
    sys_platform: toStr(row.sys_platform),
    uuid: toStr(row.uuid),
    bstudio_create_time: toStr(row.bstudio_create_time),
    project_name: typeof row.project_name === "string" ? row.project_name : String(row.project_name ?? ""),
    daily_price: toNum(row.daily_price),
    new_price: toNum(row.new_price),
    old_price: toNum(row.old_price),
    old_card: toStr(row.old_card),
    promo_price: toNumOpt(row.promo_price),
    promo_target: toStr(row.promo_target),
    promo_start: toStr(row.promo_start),
    promo_end: toStr(row.promo_end),
    gift_item: toStr(row.gift_item),
    gift_scene: toStr(row.gift_scene),
    status: toBool(row.status),
    price_note: toStr(row.price_note),
  };
}

/** 空表单 */
const emptyForm: PriceConfig = {
  project_name: "",
  daily_price: 0,
  new_price: 0,
  old_price: 0,
  status: true,
};

export default function ConfigPage() {
  const [rows, setRows] = useState<PriceConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [form, setForm] = useState<PriceConfig>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PriceConfig | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [lastSyncTime, setLastSyncTime] = useState<string | null>(null);

  /** 同步数据到知识库 */
  const syncToKnowledge = useCallback(async () => {
    setSyncing(true);
    try {
      const documentId = localStorage.getItem("knowledge_document_id") || "7644156342265806857";
      const res = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ documentId }),
      });
      const result = await res.json() as { success?: boolean; documentId?: string; error?: string; rowCount?: number };
      if (result.success && result.documentId) {
        localStorage.setItem("knowledge_document_id", result.documentId);
        setLastSyncTime(new Date().toLocaleTimeString());
        console.log("[Sync] 成功, documentId:", result.documentId);
      } else {
        console.error("[Sync] 失败:", result.error);
        alert(`同步知识库失败: ${result.error || "未知错误"}`);
      }
    } catch (err) {
      console.error("[Sync] 异常:", err);
      alert(`同步知识库异常: ${err instanceof Error ? err.message : "未知错误"}`);
    } finally {
      setSyncing(false);
    }
  }, []);

  const fetchRows = useCallback(async () => {
    setLoading(true);
    try {
      const sql = "SELECT * FROM items_pricing_system ORDER BY id DESC";
      const result = await callConfigQuery(sql);
      setRows(result.map(mapRow));
    } catch (err) {
      console.error("查询失败:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRows();
  }, [fetchRows]);

  const filteredRows = rows.filter((r) =>
    r.project_name?.toLowerCase().includes(search.toLowerCase())
  );

  /** 打开新增弹窗 */
  const handleAdd = () => {
    setEditIndex(null);
    setForm(emptyForm);
    setDialogOpen(true);
  };

  /** 打开编辑弹窗 */
  const handleEdit = (index: number) => {
    setEditIndex(index);
    const row = filteredRows[index];
    setForm({ ...row });
    setDialogOpen(true);
  };

  /** 保存（新增/编辑） */
  const handleSave = async () => {
    if (!form.project_name) return;
    setSaving(true);
    try {
      const statusVal = form.status ? "true" : "false";
      const esc = (v: string | number | boolean | undefined | null) =>
        v == null ? "NULL" : typeof v === "string" ? `'${v.replace(/'/g, "''")}'` : String(v);

      let sql: string;
      if (editIndex !== null && form.id != null) {
        // UPDATE
        sql = `UPDATE items_pricing_system SET project_name=${esc(form.project_name)}, daily_price=${esc(form.daily_price)}, new_price=${esc(form.new_price)}, old_price=${esc(form.old_price)}, old_card=${esc(form.old_card)}, promo_price=${esc(form.promo_price)}, promo_target=${esc(form.promo_target)}, promo_start=${esc(form.promo_start)}, promo_end=${esc(form.promo_end)}, gift_item=${esc(form.gift_item)}, gift_scene=${esc(form.gift_scene)}, status=${statusVal}, price_note=${esc(form.price_note)} WHERE id=${form.id}`;
      } else {
        // INSERT
        const cols = "project_name, daily_price, new_price, old_price, old_card, promo_price, promo_target, promo_start, promo_end, gift_item, gift_scene, status, price_note";
        const vals = `${esc(form.project_name)}, ${esc(form.daily_price)}, ${esc(form.new_price)}, ${esc(form.old_price)}, ${esc(form.old_card)}, ${esc(form.promo_price)}, ${esc(form.promo_target)}, ${esc(form.promo_start)}, ${esc(form.promo_end)}, ${esc(form.gift_item)}, ${esc(form.gift_scene)}, ${statusVal}, ${esc(form.price_note)}`;
        sql = `INSERT INTO items_pricing_system (${cols}) VALUES (${vals})`;
      }
      console.log("[ConfigPage] Saving SQL:", sql, "form.id:", form.id, "editIndex:", editIndex);
      await callConfigMutation(sql);
      console.log("[ConfigPage] Save succeeded");
      setDialogOpen(false);
      await new Promise((r) => setTimeout(r, 800));
      await fetchRows();
      // 同步到知识库（不阻塞）
      syncToKnowledge();
    } catch (err) {
      console.error("[ConfigPage] Save failed:", err);
      alert(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
    } finally {
      setSaving(false);
    }
  };

  /** 删除 */
  const handleDelete = async () => {
    if (!deleteTarget || deleteTarget.id == null) return;
    setDeleting(true);
    try {
      const sql = `DELETE FROM items_pricing_system WHERE id=${deleteTarget.id}`;
      console.log("[ConfigPage] Deleting SQL:", sql, "deleteTarget.id:", deleteTarget.id);
      await callConfigMutation(sql);
      console.log("[ConfigPage] Delete succeeded");
      setDeleteTarget(null);
      await new Promise((r) => setTimeout(r, 800));
      await fetchRows();
      // 同步到知识库（不阻塞）
      syncToKnowledge();
    } catch (err) {
      console.error("[ConfigPage] Delete failed:", err);
      alert(`删除失败: ${err instanceof Error ? err.message : "未知错误"}`);
    } finally {
      setDeleting(false);
    }
  };

  const updateField = <K extends keyof PriceConfig>(key: K, value: PriceConfig[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* 顶部导航 */}
      <header className="sticky top-0 z-30 border-b bg-white shadow-sm">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
          <Link href="/">
            <Button variant="ghost" size="sm" className="gap-1">
              <ArrowLeft className="h-4 w-4" />
              返回对话
            </Button>
          </Link>
          <h1 className="text-lg font-semibold">项目价格 / 活动配置</h1>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6">
        {/* 工具栏 */}
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="relative w-72">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索项目名称..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={fetchRows} disabled={loading}>
              <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </Button>
            <Button variant="outline" size="sm" onClick={syncToKnowledge} disabled={syncing}>
              <RefreshCw className={`mr-1 h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
              {syncing ? "同步中..." : "同步知识库"}
            </Button>
            {lastSyncTime && (
              <span className="text-xs text-muted-foreground">上次同步: {lastSyncTime}</span>
            )}
            <Button size="sm" onClick={handleAdd}>
              <Plus className="mr-1 h-4 w-4" />
              新增项目
            </Button>
          </div>
        </div>

        {/* 表格 */}
        <div className="rounded-lg border bg-white shadow-sm">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">状态</TableHead>
                <TableHead>项目名称</TableHead>
                <TableHead className="text-right">日常价</TableHead>
                <TableHead className="text-right">新客价</TableHead>
                <TableHead className="text-right">老客价</TableHead>
                <TableHead>老客卡项</TableHead>
                <TableHead className="text-right">活动价</TableHead>
                <TableHead>活动人群</TableHead>
                <TableHead>活动时间</TableHead>
                <TableHead>赠送福利</TableHead>
                <TableHead>备注</TableHead>
                <TableHead className="w-24 text-center">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={12} className="h-32 text-center">
                    <Spinner className="mx-auto mb-2" />
                    <span className="text-sm text-muted-foreground">加载中...</span>
                  </TableCell>
                </TableRow>
              ) : filteredRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={12} className="h-24 text-center text-muted-foreground">
                    暂无数据
                  </TableCell>
                </TableRow>
              ) : (
                filteredRows.map((row, idx) => (
                  <TableRow key={row.id ?? idx} className={!row.status ? "opacity-50" : ""}>
                    <TableCell>
                      <Badge variant={row.status ? "default" : "secondary"}>
                        {row.status ? "启用" : "停用"}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium">{row.project_name}</TableCell>
                    <TableCell className="text-right">¥{row.daily_price}</TableCell>
                    <TableCell className="text-right">¥{row.new_price}</TableCell>
                    <TableCell className="text-right">¥{row.old_price}</TableCell>
                    <TableCell>{row.old_card || "-"}</TableCell>
                    <TableCell className="text-right">
                      {row.promo_price != null ? `¥${row.promo_price}` : "-"}
                    </TableCell>
                    <TableCell>{row.promo_target || "-"}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      {row.promo_start && row.promo_end
                        ? `${row.promo_start.slice(0, 10)} ~ ${row.promo_end.slice(0, 10)}`
                        : "-"}
                    </TableCell>
                    <TableCell>{row.gift_item || "-"}</TableCell>
                    <TableCell className="max-w-[120px] truncate">{row.price_note || "-"}</TableCell>
                    <TableCell className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handleEdit(idx)}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-destructive"
                          onClick={() => setDeleteTarget(row)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </main>

      {/* 新增/编辑弹窗 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editIndex !== null ? "编辑项目" : "新增项目"}</DialogTitle>
            <DialogDescription>
              {editIndex !== null ? "修改项目价格与活动信息" : "填写项目价格与活动信息"}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4">
            {/* 基本信息 */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>项目名称 *</Label>
                <Input
                  value={form.project_name}
                  onChange={(e) => updateField("project_name", e.target.value)}
                  placeholder="如：热玛吉"
                />
              </div>
              <div className="flex items-end gap-3">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={form.status ?? true}
                    onCheckedChange={(v) => updateField("status", v)}
                  />
                  <Label>{form.status ? "启用" : "停用"}</Label>
                </div>
              </div>
            </div>

            {/* 价格 */}
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>日常单次价 *</Label>
                <Input
                  type="number"
                  value={form.daily_price}
                  onChange={(e) => updateField("daily_price", Number(e.target.value))}
                  min={0}
                />
              </div>
              <div className="space-y-2">
                <Label>新客体验价 *</Label>
                <Input
                  type="number"
                  value={form.new_price}
                  onChange={(e) => updateField("new_price", Number(e.target.value))}
                  min={0}
                />
              </div>
              <div className="space-y-2">
                <Label>老客单次价 *</Label>
                <Input
                  type="number"
                  value={form.old_price}
                  onChange={(e) => updateField("old_price", Number(e.target.value))}
                  min={0}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>老客推荐卡项</Label>
              <Input
                value={form.old_card || ""}
                onChange={(e) => updateField("old_card", e.target.value || undefined)}
                placeholder="如：10次卡 5999元"
              />
            </div>

            {/* 活动信息 */}
            <div className="rounded-lg border bg-gray-50 p-4">
              <h4 className="mb-3 font-medium text-sm">活动信息</h4>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>活动价</Label>
                  <Input
                    type="number"
                    value={form.promo_price ?? ""}
                    onChange={(e) =>
                      updateField("promo_price", e.target.value ? Number(e.target.value) : undefined)
                    }
                    min={0}
                    placeholder="选填"
                  />
                </div>
                <div className="space-y-2">
                  <Label>活动适用人群</Label>
                  <Input
                    value={form.promo_target || ""}
                    onChange={(e) => updateField("promo_target", e.target.value || undefined)}
                    placeholder="如：新客专享"
                  />
                </div>
                <div className="space-y-2">
                  <Label>活动开始时间</Label>
                  <Input
                    type="datetime-local"
                    value={form.promo_start ? form.promo_start.slice(0, 16) : ""}
                    onChange={(e) =>
                      updateField("promo_start", e.target.value || undefined)
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label>活动结束时间</Label>
                  <Input
                    type="datetime-local"
                    value={form.promo_end ? form.promo_end.slice(0, 16) : ""}
                    onChange={(e) =>
                      updateField("promo_end", e.target.value || undefined)
                    }
                  />
                </div>
              </div>
            </div>

            {/* 福利信息 */}
            <div className="rounded-lg border bg-gray-50 p-4">
              <h4 className="mb-3 font-medium text-sm">福利信息</h4>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>可赠送福利</Label>
                  <Input
                    value={form.gift_item || ""}
                    onChange={(e) => updateField("gift_item", e.target.value || undefined)}
                    placeholder="如：修复面膜1盒"
                  />
                </div>
                <div className="space-y-2">
                  <Label>福利触发场景</Label>
                  <Input
                    value={form.gift_scene || ""}
                    onChange={(e) => updateField("gift_scene", e.target.value || undefined)}
                    placeholder="如：首次到店"
                  />
                </div>
              </div>
            </div>

            {/* 备注 */}
            <div className="space-y-2">
              <Label>报价备注（内部）</Label>
              <Textarea
                value={form.price_note || ""}
                onChange={(e) => updateField("price_note", e.target.value || undefined)}
                placeholder="内部备注信息，不会展示给客户"
                rows={2}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={saving || !form.project_name}>
              {saving && <Spinner className="mr-2 h-4 w-4" />}
              {editIndex !== null ? "保存修改" : "确认新增"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除项目「{deleteTarget?.project_name}」吗？此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={deleting}>
              {deleting && <Spinner className="mr-2 h-4 w-4" />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
