import { NextRequest } from "next/server";

import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../_lib/ai-paths";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ contactKey: string }> },
) {
  const authError = requireExternalApiKey(request);
  if (authError) return authError;
  const { contactKey } = await context.params;
  const query = request.nextUrl.searchParams.toString();
  return proxyAiPathsAdmin(
    `/admin/outreach/customer-logs/${encodeURIComponent(contactKey)}${query ? `?${query}` : ""}`,
  );
}
