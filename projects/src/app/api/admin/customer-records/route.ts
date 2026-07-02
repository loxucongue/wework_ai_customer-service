import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }
  const customerId = request.nextUrl.searchParams.get("customer_id") || "";
  return proxyAiPathsAdmin(`/admin/customer-records?customer_id=${encodeURIComponent(customerId)}`);
}
