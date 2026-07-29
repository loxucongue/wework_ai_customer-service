import { NextRequest } from "next/server";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createHmac } from "node:crypto";
import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

export const runtime = "nodejs";

const MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024;
const DEFAULT_OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com";
const DEFAULT_OSS_CDN_DOMAIN = "test.by4dev.4ba.cn";
const MAX_REMOTE_REDIRECTS = 3;
const REMOTE_FETCH_TIMEOUT_MS = 20_000;
const ALLOWED_MEDIA_PREFIXES = ["image/", "video/"];

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
  objectPrefix = "uploads",
}: {
  buffer: Buffer;
  safeName: string;
  contentType: string;
  objectPrefix?: string;
}) {
  const config = loadOssUploadConfig();
  if (!config) return null;

  const mediaKind = contentType.startsWith("video/") ? "videos" : "images";
  const objectKey = `${objectPrefix}/${mediaKind}/${Date.now()}_${safeName}`;
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

function isAllowedMediaType(contentType: string) {
  return ALLOWED_MEDIA_PREFIXES.some((prefix) => contentType.toLowerCase().startsWith(prefix));
}

function privateAddress(address: string) {
  if (address === "::1" || address === "0:0:0:0:0:0:0:1") return true;
  if (address.startsWith("fe80:") || address.startsWith("fc") || address.startsWith("fd")) return true;
  if (isIP(address) !== 4) return false;
  const parts = address.split(".").map(Number);
  if (parts[0] === 10 || parts[0] === 127 || parts[0] === 0) return true;
  if (parts[0] === 169 && parts[1] === 254) return true;
  if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true;
  if (parts[0] === 192 && parts[1] === 168) return true;
  return false;
}

async function validateRemoteUrl(rawUrl: string) {
  const url = new URL(rawUrl);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("Only public HTTP(S) media URLs are supported");
  }
  const addresses = await lookup(url.hostname, { all: true, verbatim: true });
  if (!addresses.length || addresses.some((item) => privateAddress(item.address))) {
    throw new Error("Private or unresolved media URLs are not supported");
  }
  return url;
}

async function readLimitedBody(response: Response) {
  const declaredLength = Number(response.headers.get("content-length") || 0);
  if (declaredLength > MAX_UPLOAD_SIZE_BYTES) {
    throw new Error("Remote file is too large");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Remote file has no response body");
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_UPLOAD_SIZE_BYTES) {
      await reader.cancel();
      throw new Error("Remote file is too large");
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)), total);
}

function mediaFileName(url: URL, contentType: string) {
  const rawName = decodeURIComponent(url.pathname.split("/").pop() || "remote-media");
  const sanitized = rawName.replace(/[^a-zA-Z0-9._-]/g, "_");
  if (sanitized.includes(".")) return sanitized;
  const subtype = contentType.split(";")[0].split("/")[1]?.replace(/[^a-zA-Z0-9]/g, "") || "bin";
  return `${sanitized || "remote-media"}.${subtype}`;
}

async function fetchRemoteMedia(rawUrl: string) {
  let current = await validateRemoteUrl(rawUrl);
  for (let redirect = 0; redirect <= MAX_REMOTE_REDIRECTS; redirect += 1) {
    const response = await fetch(current, {
      redirect: "manual",
      signal: AbortSignal.timeout(REMOTE_FETCH_TIMEOUT_MS),
      headers: { "User-Agent": "AICS-Outreach-Media-Transfer/1.0" },
    });
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (!location || redirect === MAX_REMOTE_REDIRECTS) {
        throw new Error("Remote media redirect limit exceeded");
      }
      current = await validateRemoteUrl(new URL(location, current).toString());
      continue;
    }
    if (!response.ok) throw new Error(`Remote media fetch failed: ${response.status}`);
    const contentType = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
    if (!isAllowedMediaType(contentType)) {
      throw new Error("Remote URL must return an image or video");
    }
    return {
      buffer: await readLimitedBody(response),
      contentType,
      safeName: mediaFileName(current, contentType),
      sourceUrl: current.toString(),
    };
  }
  throw new Error("Remote media fetch failed");
}

export async function POST(request: NextRequest) {
  try {
    const scope = request.nextUrl.searchParams.get("scope") || "";
    const requireOss = request.nextUrl.searchParams.get("requireOss") === "1";
    const isOutreach = scope === "outreach";
    const requestContentType = request.headers.get("content-type") || "";
    let buffer: Buffer;
    let safeName: string;
    let contentType: string;
    let sourceUrl = "";

    if (requestContentType.includes("application/json")) {
      if (!isOutreach || !requireOss) {
        return jsonResponse({ error: "URL transfer is only available for the Outreach asset library" }, { status: 400 });
      }
      const payload = (await request.json()) as { sourceUrl?: unknown };
      if (typeof payload.sourceUrl !== "string" || !payload.sourceUrl.trim()) {
        return jsonResponse({ error: "sourceUrl is required" }, { status: 400 });
      }
      const remote = await fetchRemoteMedia(payload.sourceUrl.trim());
      buffer = remote.buffer;
      safeName = remote.safeName;
      contentType = remote.contentType;
      sourceUrl = payload.sourceUrl.trim();
    } else {
      const formData = await request.formData();
      const file = formData.get("file");
      if (!file || !(file instanceof File)) {
        return jsonResponse({ error: "No file provided" }, { status: 400 });
      }
      const bytes = await file.arrayBuffer();
      buffer = Buffer.from(bytes);
      safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "upload";
      contentType = file.type || "application/octet-stream";
    }

    if (buffer.byteLength > MAX_UPLOAD_SIZE_BYTES) {
      return jsonResponse(
        { error: "File is too large", maxBytes: MAX_UPLOAD_SIZE_BYTES },
        { status: 413 }
      );
    }
    if (isOutreach && !isAllowedMediaType(contentType)) {
      return jsonResponse({ error: "Outreach assets must be images or videos" }, { status: 415 });
    }

    if (isOutreach || requireOss) {
      try {
        const ossUpload = await uploadToOss({
          buffer,
          safeName,
          contentType,
          objectPrefix: "ai-outreach/assets",
        });
        if (!ossUpload) {
          return jsonResponse({ error: "OSS is not configured" }, { status: 503 });
        }
        return jsonResponse({ ...ossUpload, sourceUrl, contentType });
      } catch (error) {
        console.error("Required OSS upload failed:", errorSummary(error));
        return jsonResponse({ error: "Failed to transfer media to OSS" }, { status: 502 });
      }
    }

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
