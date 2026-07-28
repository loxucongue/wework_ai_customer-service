import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../../_lib/ai-paths";

type RouteContext = {
  params: Promise<{ customerId: string }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const { customerId } = await context.params;
  const query = request.nextUrl.searchParams.toString();
  const path = `/admin/outreach/customers/${encodeURIComponent(customerId)}/detail${query ? `?${query}` : ""}`;
  return proxyAiPathsAdmin(path);
}
