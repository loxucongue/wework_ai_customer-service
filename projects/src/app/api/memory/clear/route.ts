import { NextRequest } from "next/server";
import {
  clearAiPathsCustomerMemory,
  frontendTestCustomerId,
  jsonResponse,
} from "../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  let body: { customer_id?: string };
  try {
    body = (await request.json()) as { customer_id?: string };
  } catch {
    return jsonResponse({ error: "invalid json body" }, 400);
  }

  const customerId = frontendTestCustomerId() || String(body.customer_id || "").trim();
  if (!customerId) {
    return jsonResponse({ error: "customer_id is required" }, 400);
  }

  try {
    const response = await clearAiPathsCustomerMemory(customerId);
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

    try {
      return jsonResponse(JSON.parse(text));
    } catch {
      return jsonResponse({ status: "ok", customer_id: customerId });
    }
  } catch (error) {
    console.error("Failed to clear customer memory:", error);
    return jsonResponse({ error: "Failed to clear customer memory" }, 500);
  }
}
