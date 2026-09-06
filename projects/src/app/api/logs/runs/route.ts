import { NextRequest } from "next/server";
import { getAiPathsRun, getAiPathsRunNode, jsonResponse, listAiPathsRuns } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const requestId = searchParams.get("request_id") || "";
  const nodeId = searchParams.get("node_id") || "";

  try {
    let response: Response;
    if (requestId && nodeId) {
      response = await getAiPathsRunNode(requestId, nodeId);
    } else if (requestId) {
      response = await getAiPathsRun(requestId, searchParams.get("include_debug") === "true");
    } else {
      response = await listAiPathsRuns({
          limit: searchParams.get("limit") || "50",
          customer_id: searchParams.get("customer_id") || "",
          conversation_id: searchParams.get("conversation_id") || "",
          has_error: searchParams.get("has_error") || "",
          started_from: searchParams.get("started_from") || "",
          started_to: searchParams.get("started_to") || "",
          wechat: searchParams.get("wechat") || "",
          run_status: searchParams.get("run_status") || "",
          intent_code: searchParams.get("intent_code") || "",
          emotion_code: searchParams.get("emotion_code") || "",
          checkpoint_code: searchParams.get("checkpoint_code") || "",
          decision_status: searchParams.get("decision_status") || "",
          sequence_matched: searchParams.get("sequence_matched") || "",
          sequence_adopted: searchParams.get("sequence_adopted") || "",
          script_adopted: searchParams.get("script_adopted") || "",
          node_failed: searchParams.get("node_failed") || "",
      });
    }
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

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return jsonResponse(
        {
          error: "AI Paths API returned non-json response",
          detail: compactResponseText(text),
        },
        502
      );
    }

    return new Response(text, {
      status: response.status,
      headers: { "Content-Type": contentType || "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Failed to load AI Paths logs:", error);
    return jsonResponse({ error: "Failed to load AI Paths logs" }, 500);
  }
}

function compactResponseText(text: string) {
  return text.replace(/\s+/g, " ").slice(0, 1000);
}
