import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  return proxyAiPathsAdmin(`/admin/outreach/sops?${request.nextUrl.searchParams.toString()}`);
}

export async function POST(request: NextRequest) {
  const body = await request.text();
  return proxyAiPathsAdmin("/admin/outreach/sops", {
    method: "POST",
    body,
  });
}
