import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  return proxyAiPathsAdmin(`/admin/outreach/sops?${request.nextUrl.searchParams.toString()}`);
}

export async function POST(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const body = await request.text();
  return proxyAiPathsAdmin("/admin/outreach/sops", {
    method: "POST",
    body,
  });
}
