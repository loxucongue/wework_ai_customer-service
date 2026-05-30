import { NextRequest } from "next/server";
import { S3Storage } from "coze-coding-dev-sdk";

export async function POST(request: NextRequest) {
  try {
    const endpointUrl = process.env.COZE_BUCKET_ENDPOINT_URL;
    const bucketName = process.env.COZE_BUCKET_NAME;

    if (!endpointUrl || !bucketName) {
      return new Response(
        JSON.stringify({
          error: "Image upload storage is not configured",
          missing: {
            COZE_BUCKET_ENDPOINT_URL: !endpointUrl,
            COZE_BUCKET_NAME: !bucketName,
          },
        }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }

    const formData = await request.formData();
    const file = formData.get("file");

    if (!file || !(file instanceof File)) {
      return new Response(
        JSON.stringify({ error: "No file provided" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    const bytes = await file.arrayBuffer();
    const buffer = Buffer.from(bytes);

    // Sanitize filename
    const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "upload";
    const fileName = `chat_images/${Date.now()}_${safeName}`;

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
      contentType: file.type || "image/jpeg",
    });

    const url = await storage.generatePresignedUrl({
      key,
      expireTime: 86400, // 1 day
    });

    return new Response(
      JSON.stringify({ url, key }),
      { headers: { "Content-Type": "application/json" } }
    );
  } catch (error) {
    console.error("Upload failed:", error);
    return new Response(
      JSON.stringify({ error: "Failed to upload file" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}
