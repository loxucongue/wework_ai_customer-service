import { NextRequest } from "next/server";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createHmac } from "node:crypto";

export const runtime = "nodejs";

const MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024;
const DEFAULT_OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com";
const DEFAULT_OSS_CDN_DOMAIN = "test.by4dev.4ba.cn";

function jsonResponse(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
}

function publicBaseUrl(request: NextRequest) {
  const configuredBaseUrl = process.env.NEXT_PUBLIC_APP_BASE_URL || process.env.APP_PUBLIC_BASE_URL;
  if (configuredBaseUrl) {
    return configuredBaseUrl.replace(/\/$/, "");
  }

  const forwardedProto = request.headers.get("x-forwarded-proto") || "http";
  const forwardedHost = request.headers.get("x-forwarded-host");
  const host = forwardedHost || request.headers.get("host") || "";
  if (host && !host.startsWith("0.0.0.0") && !host.startsWith("127.0.0.1")) {
    return `${forwardedProto}://${host}`.replace(/\/$/, "");
  }

  const requestUrl = new URL(request.url);
  return requestUrl.origin.replace(/\/$/, "");
}

async function saveLocalUpload({
  buffer,
  safeName,
  contentType,
  request,
}: {
  buffer: Buffer;
  safeName: string;
  contentType: string;
  request: NextRequest;
}) {
  const mediaKind = contentType.startsWith("video/") ? "videos" : "images";
  const relativeKey = `uploads/${mediaKind}/${Date.now()}_${safeName}`;
  const uploadPath = path.join(process.cwd(), "public", relativeKey);
  await mkdir(path.dirname(uploadPath), { recursive: true });
  await writeFile(uploadPath, buffer);
  return {
    key: relativeKey,
    url: `${publicBaseUrl(request)}/${relativeKey.replace(/\\/g, "/")}`,
    storage: "local",
  };
}

type OssUploadConfig = {
  bucket: string;
  accessKeyId: string;
  accessKeySecret: string;
  endpoint: string;
  cdnDomain: string;
};

function loadOssUploadConfig(): OssUploadConfig | null {
  const bucket =
    process.env.OSS_BUCKET ||
    process.env.ALIYUN_OSS_BUCKET ||
    process.env.BUCKET;
  const accessKeyId =
    process.env.OSS_ACCESS_KEY_ID ||
    process.env.ALIYUN_OSS_ACCESS_KEY_ID;
  const accessKeySecret =
    process.env.OSS_ACCESS_KEY_SECRET ||
    process.env.ALIYUN_OSS_ACCESS_KEY_SECRET;
  const endpoint =
    process.env.OSS_ENDPOINT ||
    process.env.ALIYUN_OSS_ENDPOINT ||
    DEFAULT_OSS_ENDPOINT;
  const cdnDomain =
    process.env.OSS_CDN_DOMAIN ||
    process.env.ALIYUN_OSS_CDN_DOMAIN ||
    DEFAULT_OSS_CDN_DOMAIN;
  if (!bucket || !accessKeyId || !accessKeySecret) return null;
  return { bucket, accessKeyId, accessKeySecret, endpoint, cdnDomain };
}

function errorSummary(error: unknown) {
  if (error instanceof Error) {
    return { name: error.name, message: error.message.slice(0, 300) };
  }
  return { message: String(error).slice(0, 300) };
}

async function uploadToOss({
  buffer,
  safeName,
  contentType,
}: {
  buffer: Buffer;
  safeName: string;
  contentType: string;
}) {
  const config = loadOssUploadConfig();
  if (!config) return null;

  const mediaKind = contentType.startsWith("video/") ? "videos" : "images";
  const objectKey = `uploads/${mediaKind}/${Date.now()}_${safeName}`;
  const date = new Date().toUTCString();
  const resource = `/${config.bucket}/${objectKey}`;
  const stringToSign = ["PUT", "", contentType, date, resource].join("\n");
  const signature = createHmac("sha1", config.accessKeySecret).update(stringToSign).digest("base64");
  const endpoint = config.endpoint.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const response = await fetch(`https://${config.bucket}.${endpoint}/${objectKey}`, {
    method: "PUT",
    headers: {
      Authorization: `OSS ${config.accessKeyId}:${signature}`,
      Date: date,
      "Content-Type": contentType,
    },
    body: new Uint8Array(buffer),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`OSS upload failed: ${response.status} ${detail.slice(0, 200)}`);
  }
  return {
    key: objectKey,
    url: `https://${config.cdnDomain.replace(/^https?:\/\//, "").replace(/\/$/, "")}/${objectKey}`,
    storage: "oss",
  };
}

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const file = formData.get("file");

    if (!file || !(file instanceof File)) {
      return jsonResponse({ error: "No file provided" }, { status: 400 });
    }

    const bytes = await file.arrayBuffer();
    const buffer = Buffer.from(bytes);
    if (buffer.byteLength > MAX_UPLOAD_SIZE_BYTES) {
      return jsonResponse(
        { error: "File is too large", maxBytes: MAX_UPLOAD_SIZE_BYTES },
        { status: 413 }
      );
    }

    // Sanitize filename
    const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "upload";
    const contentType = file.type || "application/octet-stream";
    const endpointUrl = process.env.COZE_BUCKET_ENDPOINT_URL;
    const bucketName = process.env.COZE_BUCKET_NAME;

    if (endpointUrl && bucketName) {
      try {
        const { S3Storage } = await import("coze-coding-dev-sdk");
        const fileName = `chat_uploads/${Date.now()}_${safeName}`;
        const storage = new S3Storage({
          endpointUrl,
          accessKey: "",
          secretKey: "",
          bucketName,
          region: "cn-beijing",
        });

        const key = await storage.uploadFile({
          fileContent: buffer,
          fileName,
          contentType,
        });

        const url = await storage.generatePresignedUrl({
          key,
          expireTime: 86400, // 1 day
        });

        return jsonResponse({ url, key, storage: "s3" });
      } catch (error) {
        console.error("S3 upload failed, falling back to local upload:", errorSummary(error));
      }
    }

    const ossUpload = await uploadToOss({ buffer, safeName, contentType }).catch((error) => {
      console.error("OSS upload failed, falling back to local upload:", errorSummary(error));
      return null;
    });
    if (ossUpload) {
      return jsonResponse(ossUpload);
    }

    const localUpload = await saveLocalUpload({
      buffer,
      safeName,
      contentType,
      request,
    });
    return jsonResponse(localUpload);
  } catch (error) {
    console.error("Upload failed:", errorSummary(error));
    return jsonResponse({ error: "Failed to upload file" }, { status: 500 });
  }
}
