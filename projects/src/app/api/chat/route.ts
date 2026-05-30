import { NextRequest } from "next/server";

const COZE_MAIN_WORKFLOW_ID = "7639623828015988742";

type ChatRequestBody = {
  content: string;
  customer_id: string;
  corp_id?: string;
  conversation_history?: string[];
  file_image?: string;
  user_id?: number;
  wechat?: string;
  external_userid?: string;
  customer_add_wechat_id?: string | number;
  confirmed_store_id?: string | number;
  confirmed_store_name?: string;
  store_id?: string | number;
  store_name?: string;
  appointment_id?: string | number;
  appointment_time?: string;
  request_context?: Record<string, unknown>;
};

type AiPathsReplyMessage = {
  type?: "text" | "image";
  order?: number;
  content?: string;
};

type AiPathsResponse = {
  request_id?: string;
  reply_messages?: AiPathsReplyMessage[];
  scene?: string;
  intent?: string;
  subflow?: string;
  trace_url?: string;
  meta?: Record<string, unknown>;
};

export async function POST(request: NextRequest) {
  const body = (await request.json()) as ChatRequestBody;
  const { content, customer_id, conversation_history, file_image } = body;

  if (!content && !file_image) {
    return jsonResponse({ error: "content or file_image is required" }, 400);
  }
  if (!customer_id) {
    return jsonResponse({ error: "customer_id is required" }, 400);
  }

  const backend = process.env.CHAT_BACKEND || "ai_paths";
  if (backend === "coze") {
    return callCozeMainWorkflow(body);
  }

  return callAiPaths(body);
}

async function callAiPaths(body: ChatRequestBody) {
  const apiBase = process.env.AI_PATHS_API_BASE || "http://127.0.0.1:8000";
  const payload = {
    content: body.content || "",
    customer_id: body.customer_id,
    corp_id: body.corp_id || process.env.DEFAULT_CORP_ID || "",
    conversation_history: body.conversation_history || [],
    file_image: body.file_image || null,
    user_id: body.user_id ?? (process.env.DEFAULT_USER_ID ? Number(process.env.DEFAULT_USER_ID) : null),
    wechat: body.wechat || process.env.DEFAULT_WECHAT || "",
    external_userid: body.external_userid || null,
    customer_add_wechat_id: body.customer_add_wechat_id ?? null,
    confirmed_store_id: body.confirmed_store_id ?? null,
    confirmed_store_name: body.confirmed_store_name || null,
    store_id: body.store_id ?? null,
    store_name: body.store_name || null,
    appointment_id: body.appointment_id ?? null,
    appointment_time: body.appointment_time || null,
    request_context: body.request_context || {},
  };

  try {
    const headers: Record<string, string> = { "Content-Type": "application/json; charset=utf-8" };
    if (process.env.AI_PATHS_API_KEY) {
      headers.Authorization = `Bearer ${process.env.AI_PATHS_API_KEY}`;
    }
    const response = await fetch(`${apiBase.replace(/\/$/, "")}/chat`, {
      method: "POST",
      headers,
      body: Buffer.from(JSON.stringify(payload), "utf8"),
    });

    const text = await response.text();
    if (!response.ok) {
      return jsonResponse(
        {
          error: `AI Paths API returned ${response.status}`,
          detail: text,
        },
        response.status
      );
    }

    const result = JSON.parse(text) as AiPathsResponse;
    const output = (result.reply_messages || [])
      .filter((item) => item.content)
      .map((item, index) => ({
        type: item.type || "text",
        order: item.order || index + 1,
        content: item.content || "",
      }));

    return jsonResponse({
      output,
      scene: result.scene || "",
      intent: result.intent || "",
      subflow: result.subflow || "",
      request_id: result.request_id || "",
      trace_url: result.trace_url || "",
      meta: result.meta || {},
    });
  } catch (error) {
    console.error("AI Paths call failed:", error);
    return jsonResponse({ error: "Failed to call AI Paths API" }, 500);
  }
}

async function callCozeMainWorkflow(body: ChatRequestBody) {
  const cozeApiToken = process.env.COZE_WORKLOAD_API_TOKEN;
  const cozeApiBase = process.env.COZE_API_BASE_URL || "https://api.coze.cn";

  if (!cozeApiToken) {
    return jsonResponse({ error: "COZE_WORKLOAD_API_TOKEN is not configured" }, 500);
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${cozeApiToken}`,
    "Content-Type": "application/json; charset=utf-8",
  };

  const extraHeaders = process.env.COZE_EXTRA_HEADERS || "";
  for (const pair of extraHeaders.split(";")) {
    const idx = pair.indexOf("=");
    if (idx > 0) {
      headers[pair.slice(0, idx).trim()] = pair.slice(idx + 1).trim();
    }
  }

  const parameters: Record<string, unknown> = {
    content: body.content,
    customer_id: body.customer_id,
    conversation_history: body.conversation_history || [],
    corp_id: body.corp_id || body.customer_id || "",
  };

  if (body.file_image) {
    parameters.file_image = body.file_image;
  }
  if (body.user_id) {
    parameters.user_id = body.user_id;
  }
  if (body.wechat) {
    parameters.wechat = body.wechat;
  }
  if (body.external_userid) {
    parameters.external_userid = body.external_userid;
  }

  try {
    const response = await fetch(`${cozeApiBase}/v1/workflow/run`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        workflow_id: COZE_MAIN_WORKFLOW_ID,
        parameters,
      }),
    });

    const text = await response.text();
    if (!response.ok) {
      return jsonResponse(
        {
          error: `Workflow API returned ${response.status}`,
          detail: text,
        },
        response.status
      );
    }

    return new Response(text, {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Workflow call failed:", error);
    return jsonResponse({ error: "Failed to call workflow" }, 500);
  }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
