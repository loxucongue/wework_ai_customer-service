import { NextRequest } from "next/server";
import {
  ChatRequestBody,
  jsonResponse,
  parseChatRequest,
  proxyAiPathsChatForFrontend,
} from "../_lib/ai-paths";

const COZE_MAIN_WORKFLOW_ID = "7639623828015988742";

export async function POST(request: NextRequest) {
  const { body, error } = await parseChatRequest(request);
  if (error) {
    return error;
  }
  if (!body) {
    return jsonResponse({ error: "invalid chat request" }, 400);
  }

  const backend = process.env.CHAT_BACKEND || "ai_paths";
  if (backend === "coze") {
    return callCozeMainWorkflow(body);
  }

  return proxyAiPathsChatForFrontend(body);
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
