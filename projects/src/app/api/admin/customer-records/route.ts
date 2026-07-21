import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }
  const customerId = request.nextUrl.searchParams.get("customer_id") || "";
  const query = new URLSearchParams({
    customer_id: customerId,
    wechat: request.nextUrl.searchParams.get("wechat") || "",
  });
  for (const key of ["corp_id", "external_userid"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value) query.set(key, value);
  }
  return proxyAiPathsAdmin(`/admin/customer-records?${query}`);
}
