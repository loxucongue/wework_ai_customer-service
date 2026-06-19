import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  return proxyAiPathsAdmin(`/admin/outreach/events?${request.nextUrl.searchParams.toString()}`);
}
