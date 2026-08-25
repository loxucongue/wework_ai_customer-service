import { SopPlatformLogViewer } from "@/components/logs/sop-platform-log-viewer";
import { QuietBacklogLogViewer } from "@/components/logs/quiet-backlog-log-viewer";

export default async function SopPlatformLogsPage({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const params = await searchParams;
  return params.view === "quiet-backlog" ? <QuietBacklogLogViewer /> : <SopPlatformLogViewer />;
}
