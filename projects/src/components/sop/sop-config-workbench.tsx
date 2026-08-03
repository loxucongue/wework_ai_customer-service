import { BookOpenText, MessagesSquare } from "lucide-react";
import Link from "next/link";

import { PrecisionQaPlaybookWorkbench } from "@/components/sop/precision-qa-playbook-workbench";
import { SopReplyPackWorkbench } from "@/components/sop/sop-reply-pack-workbench";
import { Button } from "@/components/ui/button";

export type SopConfigSection = "packs" | "precision";

export function SopConfigWorkbench({ section }: { section: SopConfigSection }) {
  return (
    <div>
      <div className="sticky top-0 z-50 h-16 border-b bg-background/95 px-5 backdrop-blur">
        <div className="mx-auto flex h-full max-w-7xl items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="text-sm font-semibold">AI回复主线话术</div>
            <div className="hidden text-xs text-muted-foreground sm:block">
              仅用于客户主动消息进入 AI 回复链路时的 SOP Gate 与精准回复
            </div>
          </div>
          <nav
            className="flex shrink-0 items-center gap-1 rounded-md border bg-muted/40 p-1"
            aria-label="AI回复主线话术"
          >
            <Button
              asChild
              size="sm"
              variant={section === "packs" ? "default" : "ghost"}
            >
              <Link href="/sop" aria-current={section === "packs" ? "page" : undefined}>
                <MessagesSquare />
                主线话术
              </Link>
            </Button>
            <Button
              asChild
              size="sm"
              variant={section === "precision" ? "default" : "ghost"}
            >
              <Link
                href="/sop/precision"
                aria-current={section === "precision" ? "page" : undefined}
              >
                <BookOpenText />
                精准回复
              </Link>
            </Button>
          </nav>
        </div>
      </div>
      {section === "packs" ? <SopReplyPackWorkbench /> : <PrecisionQaPlaybookWorkbench />}
    </div>
  );
}
