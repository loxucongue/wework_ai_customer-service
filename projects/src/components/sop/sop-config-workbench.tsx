import { BookOpenText, MessagesSquare } from "lucide-react";
import Link from "next/link";

import { PrecisionQaPlaybookWorkbench } from "@/components/sop/precision-qa-playbook-workbench";
import { SopReplyPackWorkbench } from "@/components/sop/sop-reply-pack-workbench";
import { Button } from "@/components/ui/button";

export type SopConfigSection = "packs" | "precision";

export function SopConfigWorkbench({ section }: { section: SopConfigSection }) {
  return (
    <div>
      <div className="sticky top-0 z-50 border-b bg-background px-5 py-2">
        <div className="flex items-center gap-2">
          <Button
            asChild
            size="sm"
            variant={section === "packs" ? "default" : "ghost"}
          >
            <Link href="/sop">
              <MessagesSquare />
              话术包
            </Link>
          </Button>
          <Button
            asChild
            size="sm"
            variant={section === "precision" ? "default" : "ghost"}
          >
            <Link href="/sop/precision">
              <BookOpenText />
              精准回复
            </Link>
          </Button>
        </div>
      </div>
      {section === "packs" ? <SopReplyPackWorkbench /> : <PrecisionQaPlaybookWorkbench />}
    </div>
  );
}
