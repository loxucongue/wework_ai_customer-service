import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }
  return proxyAiPathsAdmin("/admin/sop-reply-packs/event-first-add-templates", {
    method: "POST",
  });
}
