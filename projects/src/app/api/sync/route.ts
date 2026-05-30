import { NextResponse } from "next/server";
import {
  Document,
  Packer,
  Paragraph,
  TextRun,
} from "docx";
import { S3Storage } from "coze-coding-dev-sdk";

const SYNC_WORKFLOW_ID = "7644090458134609974";
const DB_WORKFLOW_ID = "7641872030061117450";

// 字段英文名 → 中文标签映射（不含 sys_platform 和 uuid）
const FIELD_LABELS: { key: string; label: string }[] = [
  { key: "id", label: "项目id" },
  { key: "bstudio_create_time", label: "创建时间" },
  { key: "project_name", label: "项目名称" },
  { key: "daily_price", label: "日常单次价" },
  { key: "new_price", label: "新客体验价" },
  { key: "old_price", label: "老客单次价" },
  { key: "old_card", label: "老客推荐卡项" },
  { key: "promo_price", label: "活动价" },
  { key: "promo_target", label: "活动适用人群" },
  { key: "promo_start", label: "活动开始时间" },
  { key: "promo_end", label: "活动结束时间" },
  { key: "gift_item", label: "可赠送福利" },
  { key: "gift_scene", label: "福利触发场景" },
  { key: "status", label: "状态" },
  { key: "price_note", label: "报价备注" },
];

async function callWorkflow(
  workflowId: string,
  parameters: Record<string, unknown>,
  usePat = false
): Promise<Record<string, unknown>> {
  const satToken = process.env.COZE_WORKLOAD_API_TOKEN;
  const patToken = process.env.COZE_PAT_TOKEN;
  const token = usePat && patToken ? patToken : satToken;
  const baseUrl = process.env.COZE_API_BASE_URL || "https://api.coze.cn";

  const res = await fetch(`${baseUrl}/v1/workflow/run`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ workflow_id: workflowId, parameters }),
  });

  if (!res.ok) {
    throw new Error(`工作流请求失败: HTTP ${res.status}`);
  }

  return (await res.json()) as Record<string, unknown>;
}

/** 查询全量数据 */
async function fetchAllRows(): Promise<Record<string, string>[]> {
  const json = await callWorkflow(DB_WORKFLOW_ID, {
    input: "SELECT * FROM items_pricing_system ORDER BY id",
  });

  const code = json.code;
  if (code != null && code !== 0) {
    throw new Error(`查询数据失败: ${String(json.msg)}`);
  }

  const data = json.data as string | undefined;
  if (!data) return [];

  const parsed = typeof data === "string" ? JSON.parse(data) : data;
  const output = parsed.output;
  if (Array.isArray(output)) return output;
  return [];
}

/** 生成 Word 文档 Buffer，格式为 ### 分隔的条目 */
async function generateDocx(rows: Record<string, string>[]): Promise<Buffer> {
  const children: Paragraph[] = [];

  rows.forEach((row, idx) => {
    // 每个条目前加 ###
    if (idx > 0) {
      children.push(new Paragraph({ text: "" })); // 空行间隔
    }
    children.push(
      new Paragraph({
        children: [new TextRun({ text: "###", bold: true, size: 28 })],
      })
    );

    // 逐字段输出：标签：值
    for (const { key, label } of FIELD_LABELS) {
      const val = row[key] != null ? String(row[key]) : "";
      children.push(
        new Paragraph({
          children: [
            new TextRun({ text: `${label}：`, bold: true, size: 24 }),
            new TextRun({ text: val, size: 24 }),
          ],
        })
      );
    }
  });

  const doc = new Document({
    sections: [{ children }],
  });

  const buf = await Packer.toBuffer(doc);
  return Buffer.from(buf);
}

/** 上传 docx 到对象存储，返回可访问的签名 URL */
async function uploadToStorage(
  buf: Buffer,
  fileName: string
): Promise<string> {
  const storage = new S3Storage({
    endpointUrl: process.env.COZE_BUCKET_ENDPOINT_URL || "",
    accessKey: "",
    secretKey: "",
    bucketName: process.env.COZE_BUCKET_NAME || "",
    region: "cn-beijing",
  });

  // 上传文件
  const key = await storage.uploadFile({
    fileContent: buf,
    fileName: `sync/${fileName}`,
    contentType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });

  // 生成 24 小时有效的签名 URL
  const url = await storage.generatePresignedUrl({
    key,
    expireTime: 86400,
  });

  return url;
}

/** 调用同步工作流，参数名为 documentID / documentfile（与工作流定义一致） */
async function syncKnowledge(
  documentID: string,
  documentfileUrl: string
): Promise<string> {
  const json = await callWorkflow(
    SYNC_WORKFLOW_ID,
    {
      documentID,
      documentfile: documentfileUrl,
    },
    true // 使用 PAT 调用同步工作流
  );

  const code = json.code;
  if (code != null && code !== 0) {
    throw new Error(
      `同步工作流失败(code=${code}): ${String(json.msg)}`
    );
  }

  const data = json.data as string | undefined;
  if (!data) throw new Error("同步工作流未返回数据");

  const parsed = typeof data === "string" ? JSON.parse(data) : data;
  const output = parsed.output;
  if (typeof output === "string") return output;
  throw new Error(`同步工作流返回格式异常: ${JSON.stringify(output)}`);
}

export async function POST(request: Request): Promise<NextResponse> {
  try {
    const body = (await request.json()) as { documentId?: string };
    const prevDocumentID = body.documentId || "";

    console.log(
      `[sync] 开始同步，prevDocumentID: ${prevDocumentID || "(首次)"}`
    );

    // 1. 查询全量数据
    console.log("[sync] 查询全量数据...");
    const rows = await fetchAllRows();
    console.log(`[sync] 查询到 ${rows.length} 条数据`);

    // 2. 生成 Word 文档
    console.log("[sync] 生成 Word 文档...");
    const docxBuf = await generateDocx(rows);
    console.log(`[sync] Word 文档生成完成，大小: ${docxBuf.length} bytes`);

    // 3. 上传到对象存储，获取签名 URL
    console.log("[sync] 上传文件到对象存储...");
    const fileUrl = await uploadToStorage(docxBuf, "items_pricing_system.docx");
    console.log(`[sync] 上传成功，文件 URL: ${fileUrl.substring(0, 80)}...`);

    // 4. 调用同步工作流（documentID + documentfile=URL）
    console.log("[sync] 调用同步工作流...");
    const newDocumentID = await syncKnowledge(prevDocumentID, fileUrl);
    console.log(`[sync] 同步成功，newDocumentID: ${newDocumentID}`);

    return NextResponse.json({
      success: true,
      documentId: newDocumentID,
      rowCount: rows.length,
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[sync] 同步失败:", message);
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
