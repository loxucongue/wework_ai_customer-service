import { NextRequest } from "next/server";
import { getAiPathsCustomerMemory, jsonResponse } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const customerId = request.nextUrl.searchParams.get("customer_id")?.trim() || "";
  if (!customerId) {
    return jsonResponse({ error: "customer_id is required" }, 400);
  }

  try {
    const response = await getAiPathsCustomerMemory(customerId);
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
      return jsonResponse({
        customer_id: customerId,
        memory: JSON.parse(text),
      });
    } catch {
      return jsonResponse({ customer_id: customerId, memory: {} });
    }
  } catch (error) {
    console.error("Failed to load customer memory:", error);
    return jsonResponse({ error: "Failed to load customer memory" }, 500);
  }
}
