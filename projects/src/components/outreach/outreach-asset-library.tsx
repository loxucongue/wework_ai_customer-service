"use client";

import Link from "next/link";
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Image as ImageIcon,
  Link2,
  LoaderCircle,
  Save,
  Trash2,
  Upload,
  Video,
} from "lucide-react";

type AssetType = "image" | "video";

type OutreachAsset = {
  id: string;
  enabled: boolean;
  type: AssetType;
  name: string;
  url: string;
  annotation: string;
  use_cases: string[];
  avoid_when: string[];
  tags: string[];
  storage: "oss";
  source_url?: string;
  created_at?: string;
  updated_at?: string;
};

type AssetLibrary = {
  version: number;
  purpose: string;
  updated_at?: string;
  assets: OutreachAsset[];
};

type UploadResult = {
  url?: string;
  storage?: string;
  sourceUrl?: string;
  contentType?: string;
  error?: string;
};

const inputClass =
  "w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-zinc-400";

function parseList(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter((item, index, values) => item && values.indexOf(item) === index);
}

function listText(values: string[]) {
  return (values || []).join("，");
}

function assetType(contentType: string, url: string): AssetType {
  if (contentType.startsWith("video/") || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(url)) return "video";
  return "image";
}

function newAsset(result: UploadResult, fallbackName: string): OutreachAsset {
  const now = new Date().toISOString();
  const type = assetType(result.contentType || "", result.url || "");
  return {
    id: `outreach-${type}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    enabled: true,
    type,
    name: fallbackName.replace(/\.[^.]+$/, "") || (type === "video" ? "操作视频" : "参考图片"),
    url: result.url || "",
    annotation: "",
    use_cases: [],
    avoid_when: [],
    tags: [],
    storage: "oss",
    source_url: result.sourceUrl || "",
    created_at: now,
    updated_at: now,
  };
}

export function OutreachAssetLibrary() {
  const [library, setLibrary] = useState<AssetLibrary>({
    version: 1,
    purpose: "个性化主动唤醒独立素材库，仅供 Outreach 使用，不与 SOP 话术包共用。",
    assets: [],
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/outreach/assets", { cache: "no-store" })
      .then(async (response) => {
        const data = (await response.json()) as AssetLibrary & { detail?: string; error?: string };
        if (!response.ok) throw new Error(data.detail || data.error || "读取素材库失败");
        setLibrary({
          version: data.version || 1,
          purpose: data.purpose || "个性化主动唤醒独立素材库",
          updated_at: data.updated_at,
          assets: Array.isArray(data.assets) ? data.assets : [],
        });
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setLoading(false));
  }, []);

  const enabledCount = useMemo(
    () => library.assets.filter((asset) => asset.enabled).length,
    [library.assets]
  );

  async function transferMedia(input: File | string) {
    setTransferring(true);
    setError("");
    setNotice("");
    try {
      const init: RequestInit =
        typeof input === "string"
          ? {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ sourceUrl: input }),
            }
          : (() => {
              const body = new FormData();
              body.append("file", input);
              return { method: "POST", body };
            })();
      const response = await fetch("/api/upload?scope=outreach&requireOss=1", init);
      const result = (await response.json()) as UploadResult;
      if (!response.ok || !result.url || result.storage !== "oss") {
        throw new Error(result.error || "素材转存 OSS 失败");
      }
      const fallbackName =
        typeof input === "string"
          ? decodeURIComponent(new URL(input).pathname.split("/").pop() || "外部素材")
          : input.name;
      setLibrary((current) => ({
        ...current,
        assets: [...current.assets, newAsset(result, fallbackName)],
      }));
      setSourceUrl("");
      setNotice("素材已转存到 OSS，请补充名称和用途注释后保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTransferring(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function updateAsset(index: number, patch: Partial<OutreachAsset>) {
    setLibrary((current) => ({
      ...current,
      assets: current.assets.map((asset, assetIndex) =>
        assetIndex === index ? { ...asset, ...patch, updated_at: new Date().toISOString() } : asset
      ),
    }));
  }

  async function saveLibrary() {
    const incomplete = library.assets.find((asset) => !asset.name.trim() || !asset.annotation.trim());
    if (incomplete) {
      setError(`素材“${incomplete.name || incomplete.id}”缺少名称或用途注释`);
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/outreach/assets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(library),
      });
      const data = (await response.json()) as AssetLibrary & { detail?: string; error?: string };
      if (!response.ok) throw new Error(data.detail || data.error || "保存素材库失败");
      setLibrary({
        version: data.version || 1,
        purpose: data.purpose || library.purpose,
        updated_at: data.updated_at,
        assets: Array.isArray(data.assets) ? data.assets : [],
      });
      setNotice("素材库已保存，新生成的主动唤醒计划会使用最新配置。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void transferMedia(file);
  }

  return (
    <main className="min-h-screen bg-[#f7f8fb] text-[#171717]">
      <header className="sticky top-0 z-20 flex min-h-14 flex-wrap items-center justify-between gap-3 border-b border-zinc-200 bg-white px-4 py-2 sm:px-6">
        <div className="flex items-center gap-3">
          <Link
            href="/outreach"
            className="rounded-md border border-zinc-200 p-2 text-zinc-600 hover:bg-zinc-50"
            title="返回主动唤醒"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <h1 className="text-base font-semibold">主动唤醒素材库</h1>
            <p className="text-xs text-zinc-500">独立于 SOP 话术包，素材统一转存 OSS 后供模型选择</p>
          </div>
        </div>
        <button
          type="button"
          onClick={saveLibrary}
          disabled={saving || loading}
          className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800 disabled:opacity-50"
        >
          {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存配置
        </button>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6">
        <section className="border-b border-zinc-200 pb-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">添加素材</h2>
              <p className="mt-1 text-xs text-zinc-500">
                支持图片和视频文件，或粘贴公网 URL。最大 50MB，转存成功后才可进入素材库。
              </p>
            </div>
            <div className="text-xs text-zinc-500">
              共 {library.assets.length} 个，启用 {enabledCount} 个
            </div>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-[220px_1fr_auto]">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,video/*"
              className="hidden"
              onChange={handleFile}
            />
            <button
              type="button"
              disabled={transferring}
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm hover:bg-zinc-50 disabled:opacity-50"
            >
              <Upload className="h-4 w-4" />
              上传图片或视频
            </button>
            <div className="relative">
              <Link2 className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" />
              <input
                value={sourceUrl}
                onChange={(event) => setSourceUrl(event.target.value)}
                placeholder="粘贴外部图片或视频 URL"
                className={`${inputClass} pl-9`}
              />
            </div>
            <button
              type="button"
              disabled={transferring || !sourceUrl.trim()}
              onClick={() => void transferMedia(sourceUrl.trim())}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm hover:bg-zinc-50 disabled:opacity-50"
            >
              {transferring ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
              转存 URL
            </button>
          </div>
          {notice ? <p className="mt-3 text-sm text-emerald-700">{notice}</p> : null}
          {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
        </section>

        <section className="py-5">
          {loading ? (
            <div className="flex items-center justify-center py-20 text-sm text-zinc-500">
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
              正在读取素材库
            </div>
          ) : library.assets.length === 0 ? (
            <div className="border border-dashed border-zinc-300 bg-white py-16 text-center">
              <ImageIcon className="mx-auto h-7 w-7 text-zinc-400" />
              <p className="mt-3 text-sm font-medium">还没有主动唤醒素材</p>
              <p className="mt-1 text-xs text-zinc-500">上传文件或转存 URL 后，再补充给模型理解的用途注释。</p>
            </div>
          ) : (
            <div className="space-y-3">
              {library.assets.map((asset, index) => (
                <article key={asset.id} className="grid gap-4 rounded-md border border-zinc-200 bg-white p-4 lg:grid-cols-[220px_1fr]">
                  <div>
                    <div className="aspect-video overflow-hidden rounded-md border border-zinc-200 bg-zinc-100">
                      {asset.type === "video" ? (
                        <video src={asset.url} controls preload="metadata" className="h-full w-full object-contain" />
                      ) : (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={asset.url} alt={asset.name} className="h-full w-full object-contain" />
                      )}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-2 text-xs text-zinc-500">
                      <span className="inline-flex items-center gap-1">
                        {asset.type === "video" ? <Video className="h-3.5 w-3.5" /> : <ImageIcon className="h-3.5 w-3.5" />}
                        {asset.type === "video" ? "视频" : "图片"} · OSS
                      </span>
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={asset.enabled}
                          onChange={(event) => updateAsset(index, { enabled: event.target.checked })}
                        />
                        启用
                      </label>
                    </div>
                  </div>
                  <div className="grid gap-3">
                    <div className="flex items-start gap-2">
                      <div className="flex-1">
                        <label className="mb-1 block text-xs font-medium text-zinc-600">素材名称</label>
                        <input
                          value={asset.name}
                          onChange={(event) => updateAsset(index, { name: event.target.value })}
                          className={inputClass}
                        />
                      </div>
                      <button
                        type="button"
                        title="删除素材"
                        onClick={() =>
                          setLibrary((current) => ({
                            ...current,
                            assets: current.assets.filter((_, assetIndex) => assetIndex !== index),
                          }))
                        }
                        className="mt-5 rounded-md border border-zinc-200 p-2 text-zinc-500 hover:bg-red-50 hover:text-red-600"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-zinc-600">给模型的素材注释</label>
                      <textarea
                        value={asset.annotation}
                        onChange={(event) => updateAsset(index, { annotation: event.target.value })}
                        rows={3}
                        placeholder="说明画面内容、能提供什么价值、适合解决什么顾虑。不要写未经证实的效果或承诺。"
                        className={inputClass}
                      />
                    </div>
                    <div className="grid gap-3 md:grid-cols-3">
                      <div>
                        <label className="mb-1 block text-xs font-medium text-zinc-600">适用场景</label>
                        <input
                          value={listText(asset.use_cases)}
                          onChange={(event) => updateAsset(index, { use_cases: parseList(event.target.value) })}
                          placeholder="效果信任，专业流程"
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="mb-1 block text-xs font-medium text-zinc-600">避免使用</label>
                        <input
                          value={listText(asset.avoid_when)}
                          onChange={(event) => updateAsset(index, { avoid_when: parseList(event.target.value) })}
                          placeholder="刚发过同类案例时"
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className="mb-1 block text-xs font-medium text-zinc-600">标签</label>
                        <input
                          value={listText(asset.tags)}
                          onChange={(event) => updateAsset(index, { tags: parseList(event.target.value) })}
                          placeholder="案例，操作，护理"
                          className={inputClass}
                        />
                      </div>
                    </div>
                    <p className="break-all text-[11px] text-zinc-400">{asset.url}</p>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
