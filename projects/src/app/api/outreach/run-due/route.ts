import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const limit = request.nextUrl.searchParams.get("limit") || "20";
  return proxyAiPathsAdmin(`/admin/outreach/run-due?limit=${encodeURIComponent(limit)}`, {
    method: "POST",
  });
}
