import { NextRequest } from "next/server";

import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../../../_lib/ai-paths";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ contactKey: string; planId: string }> },
) {
  const authError = requireExternalApiKey(request);
  if (authError) return authError;
  const { contactKey, planId } = await context.params;
  return proxyAiPathsAdmin(
    `/admin/outreach/customer-logs/${encodeURIComponent(contactKey)}/plans/${encodeURIComponent(planId)}`,
  );
}
