import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }
  return proxyAiPathsAdmin("/admin/precision-qa-playbook");
}

export async function PUT(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }
  const body = await request.text();
  return proxyAiPathsAdmin("/admin/precision-qa-playbook", {
    method: "PUT",
    body,
  });
}
