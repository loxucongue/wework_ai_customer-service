import { NextRequest } from "next/server";

export type ChatRequestBody = {
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

type WorkflowMessage = {
  content?: unknown;
  direction?: unknown;
  msgid?: unknown;
  msgtime?: unknown;
  msgtype?: unknown;
  sender_id?: unknown;
  sender_name?: unknown;
};

type WorkflowContent = {
  content?: unknown;
  msgid?: unknown;
  msgtime?: unknown;
  msgtype?: unknown;
  location?: unknown;
};

type WorkflowCompatibleBody = {
  workflow_id?: unknown;
  parameters?: Record<string, unknown>;
};

export type AiPathsReplyMessage = {
  type?: "text" | "image" | "human_handoff";
  order?: number;
  content?: string | Record<string, unknown>;
};

export type AiPathsResponse = {
  request_id?: string;
  reply_messages?: AiPathsReplyMessage[];
  scene?: string;
  intent?: string;
  subflow?: string;
  trace_url?: string;
  meta?: Record<string, unknown>;
};

export async function parseChatRequest(request: NextRequest) {
  try {
    const body = (await request.json()) as ChatRequestBody;
    const error = validateChatRequest(body);
    return { body, error };
  } catch {
    return {
      body: null,
      error: jsonResponse({ error: "invalid json body" }, 400),
    };
  }
}

export function validateChatRequest(body: ChatRequestBody) {
  if (!body.content && !body.file_image) {
    return jsonResponse({ error: "content or file_image is required" }, 400);
  }
  if (!body.customer_id) {
    return jsonResponse({ error: "customer_id is required" }, 400);
  }
  return null;
}

export async function parseWorkflowCompatibleRequest(request: NextRequest) {
  try {
    const body = (await request.json()) as WorkflowCompatibleBody | Record<string, unknown>;
    const normalized = normalizeWorkflowCompatibleBody(body);
    const error = validateChatRequest(normalized);
    return { body: normalized, error };
  } catch {
    return {
      body: null,
      error: jsonResponse({ code: 400, msg: "invalid json body", data: { error: "invalid json body" } }, 400),
    };
  }
}

export function normalizeWorkflowCompatibleBody(
  body: WorkflowCompatibleBody | Record<string, unknown>
): ChatRequestBody {
  const wrapper = body as WorkflowCompatibleBody;
  const parameters = isRecord(wrapper.parameters) ? wrapper.parameters : (body as Record<string, unknown>);
  const contentField = parameters.content;
  const contentObject = isRecord(contentField) ? (contentField as WorkflowContent) : {};
  const content = stringValue(contentObject.content) || stringValue(contentField);
  const image = stringValue(parameters.image) || stringValue(parameters.file_image);
  const messages = Array.isArray(parameters.messages) ? parameters.messages : [];
  const messageSummary = Array.isArray(parameters.messages) ? "" : stringValue(parameters.messages);
  const conversationHistory = Array.isArray(parameters.conversation_history)
    ? parameters.conversation_history.map((item) => stringValue(item)).filter(Boolean)
    : messages.length
      ? workflowMessagesToHistory(messages)
      : messageSummary
        ? [`对话摘要: ${messageSummary}`]
        : [];
  const requestContext = isRecord(parameters.request_context) ? { ...parameters.request_context } : {};

  const workflowId = stringValue(wrapper.workflow_id) || stringValue(parameters.workflow_id);
  const categoryId = stringValue(parameters.category_id);
  const msgid = stringValue(contentObject.msgid);
  const msgtime = stringValue(contentObject.msgtime);
  const msgtype = stringValue(contentObject.msgtype) || stringValue(parameters.msgtype);
  const location = stringValue(contentObject.location);

  Object.assign(requestContext, compactObject({
    source_protocol: "workflow-compatible",
    workflow_id: workflowId,
    category_id: categoryId,
    msgid,
    msgtime,
    msgtype,
    location,
    raw_message_count: messages.length ? String(messages.length) : messageSummary ? "summary" : "",
  }));

  const customerId =
    stringValue(parameters.customer_id) ||
    stringValue(parameters.external_userid) ||
    stringValue(requestContext.customer_id);

  return {
    content,
    customer_id: customerId,
    corp_id: stringValue(parameters.corp_id) || customerId,
    conversation_history: conversationHistory,
    file_image: image || undefined,
    user_id: numberValue(parameters.user_id),
    wechat: stringValue(parameters.wechat) || undefined,
    external_userid: stringValue(parameters.external_userid) || undefined,
    customer_add_wechat_id: stringValue(parameters.customer_add_wechat_id) || undefined,
    confirmed_store_id: stringValue(parameters.confirmed_store_id) || undefined,
    confirmed_store_name: stringValue(parameters.confirmed_store_name) || undefined,
    store_id: stringValue(parameters.store_id) || undefined,
    store_name: stringValue(parameters.store_name) || undefined,
    appointment_id: stringValue(parameters.appointment_id) || undefined,
    appointment_time: stringValue(parameters.appointment_time) || undefined,
    request_context: requestContext,
  };
}

export function requireExternalApiKey(request: NextRequest) {
  const expected = process.env.AI_EXTERNAL_API_KEY || "";
  if (!expected) {
    return null;
  }
  const authorization = request.headers.get("authorization") || "";
  const [scheme, token] = authorization.split(" ");
  if (scheme?.toLowerCase() === "bearer" && token === expected) {
    return null;
  }
  return jsonResponse({ error: "unauthorized" }, 401);
}

export async function callAiPathsBackend(body: ChatRequestBody) {
  const apiBase = process.env.AI_PATHS_API_BASE || "http://127.0.0.1:8000";
  const payload = {
    content: body.content || "",
    customer_id: body.customer_id,
    corp_id: body.corp_id || process.env.DEFAULT_CORP_ID || body.customer_id || "",
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

  const headers: Record<string, string> = { "Content-Type": "application/json; charset=utf-8" };
  if (process.env.AI_PATHS_API_KEY) {
    headers.Authorization = `Bearer ${process.env.AI_PATHS_API_KEY}`;
  }

  return fetch(`${apiBase.replace(/\/$/, "")}/chat`, {
    method: "POST",
    headers,
    body: Buffer.from(JSON.stringify(payload), "utf8"),
  });
}

export async function callAiPathsHealth() {
  const apiBase = process.env.AI_PATHS_API_BASE || "http://127.0.0.1:8000";
  return fetch(`${apiBase.replace(/\/$/, "")}/health`, {
    method: "GET",
    cache: "no-store",
  });
}

export async function clearAiPathsCustomerMemory(customerId: string) {
  const apiBase = process.env.AI_PATHS_API_BASE || "http://127.0.0.1:8000";
  const headers: Record<string, string> = {};
  if (process.env.AI_PATHS_API_KEY) {
    headers.Authorization = `Bearer ${process.env.AI_PATHS_API_KEY}`;
  }

  return fetch(
    `${apiBase.replace(/\/$/, "")}/admin/customers/${encodeURIComponent(customerId)}/memory`,
    {
      method: "DELETE",
      headers,
      cache: "no-store",
    }
  );
}

export type AiPathsRunsQuery = {
  limit?: string;
  customer_id?: string;
  conversation_id?: string;
  has_error?: string;
};

export async function listAiPathsRuns(query: AiPathsRunsQuery) {
  const apiBase = process.env.AI_PATHS_API_BASE || "http://127.0.0.1:8000";
  const headers: Record<string, string> = {};
  if (process.env.AI_PATHS_API_KEY) {
    headers.Authorization = `Bearer ${process.env.AI_PATHS_API_KEY}`;
  }

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value) {
      search.set(key, value);
    }
  }

  return fetch(`${apiBase.replace(/\/$/, "")}/admin/runs?${search.toString()}`, {
    method: "GET",
    headers,
    cache: "no-store",
  });
}

export async function getAiPathsRun(requestId: string) {
  const apiBase = process.env.AI_PATHS_API_BASE || "http://127.0.0.1:8000";
  const headers: Record<string, string> = {};
  if (process.env.AI_PATHS_API_KEY) {
    headers.Authorization = `Bearer ${process.env.AI_PATHS_API_KEY}`;
  }

  return fetch(`${apiBase.replace(/\/$/, "")}/admin/runs/${encodeURIComponent(requestId)}`, {
    method: "GET",
    headers,
    cache: "no-store",
  });
}

export async function proxyAiPathsChatRaw(body: ChatRequestBody) {
  try {
    const response = await callAiPathsBackend(body);
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
    return new Response(text, {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("AI Paths call failed:", error);
    return jsonResponse({ error: "Failed to call AI Paths API" }, 500);
  }
}

export async function proxyAiPathsChatWorkflowCompatible(body: ChatRequestBody) {
  try {
    const response = await callAiPathsBackend(body);
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse(
        {
          code: response.status,
          msg: `AI Paths API returned ${response.status}`,
          execute_id: "",
          data: {
            versions: "1",
            reply_messages: [],
            trace_id: "",
            step: "",
            has_knowledge: "",
            error: text,
          },
        },
        response.status
      );
    }

    const result = JSON.parse(text) as AiPathsResponse;
    return jsonResponse({
      code: 0,
      msg: "success",
      execute_id: result.request_id || "",
      data: {
        versions: "1",
        reply_messages: normalizeWorkflowReplyMessages(result.reply_messages || []),
        trace_id: result.request_id || "",
        step: result.subflow || result.intent || result.scene || "",
        has_knowledge: hasKnowledge(result.meta) ? "true" : "",
        error: "",
      },
      detail: {
        logid: result.request_id || "",
      },
    });
  } catch (error) {
    console.error("AI Paths workflow-compatible call failed:", error);
    return jsonResponse(
      {
        code: 500,
        msg: "Failed to call AI Paths API",
        execute_id: "",
        data: {
          versions: "1",
          reply_messages: [],
          trace_id: "",
          step: "",
          has_knowledge: "",
          error: "Failed to call AI Paths API",
        },
      },
      500
    );
  }
}

export async function proxyAiPathsChatForFrontend(body: ChatRequestBody) {
  try {
    const response = await callAiPathsBackend(body);
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
      .filter((item) => replyMessageContent(item))
      .map((item, index) => ({
        type: item.type || "text",
        order: item.order || index + 1,
        content: replyMessageContent(item),
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

export function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function workflowMessagesToHistory(messages: unknown[]) {
  return messages
    .map((item) => {
      if (!isRecord(item)) {
        return "";
      }
      const message = item as WorkflowMessage;
      const role = workflowDirectionLabel(stringValue(message.direction));
      const text = stringValue(message.content);
      return text ? `${role}: ${text}` : "";
    })
    .filter(Boolean)
    .slice(-10);
}

function workflowDirectionLabel(direction: string) {
  if (["customer", "user", "external"].includes(direction)) {
    return "用户";
  }
  if (["staff", "assistant", "service"].includes(direction)) {
    return "小贝";
  }
  return "对话";
}

function normalizeWorkflowReplyMessages(messages: AiPathsReplyMessage[]) {
  return messages
    .filter((item) => replyMessageContent(item, item.type === "human_handoff" ? "handoff_reason" : "text"))
    .map((item, index) => {
      const type = item.type || "text";
      if (type === "human_handoff") {
        const reason = replyMessageContent(item, "handoff_reason");
        return {
          type,
          order: item.order || index + 1,
          content: { handoff_reason: reason },
        };
      }
      const content = replyMessageContent(item, type === "image" ? "url" : "text");
      return {
        type,
        order: item.order || index + 1,
        content: type === "image" ? { url: content } : { text: content },
      };
    });
}

function hasKnowledge(meta: Record<string, unknown> | undefined) {
  const keys = meta?.tool_result_keys;
  return Array.isArray(keys) && keys.length > 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).trim();
}

function replyMessageContent(item: AiPathsReplyMessage, preferredKey = "text") {
  const content = item.content;
  if (isRecord(content)) {
    const preferred = stringValue(content[preferredKey]);
    if (preferred) {
      return preferred;
    }
    if (preferredKey === "handoff_reason") {
      return "";
    }
    return (
      stringValue(content.text) ||
      stringValue(content.url) ||
      stringValue(content.handoff_reason)
    );
  }
  return stringValue(content);
}

function numberValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function compactObject(values: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ""));
}
