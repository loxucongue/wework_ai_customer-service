import { NextRequest } from "next/server";

import { proxyAiPathsAdmin, requireExternalApiKey } from "../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) return authError;
  return proxyAiPathsAdmin("/admin/sop-objection-materials");
}

export async function PUT(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) return authError;
  return proxyAiPathsAdmin("/admin/sop-objection-materials", {
    method: "PUT",
    body: await request.text(),
  });
}
