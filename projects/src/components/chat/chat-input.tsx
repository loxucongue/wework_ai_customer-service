"use client";

import { useState, useRef, useCallback } from "react";
import { Send, Paperclip, X, Image as ImageIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface PendingImage {
  file: File;
  preview: string;
}

interface ChatInputProps {
  onSend: (content: string, imageFile?: File) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [input, setInput] = useState("");
  const [pendingImage, setPendingImage] = useState<PendingImage | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const clearPendingImage = useCallback(() => {
    if (pendingImage) {
      URL.revokeObjectURL(pendingImage.preview);
    }
    setPendingImage(null);
  }, [pendingImage]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if ((!trimmed && !pendingImage) || disabled) return;
    onSend(trimmed || "[图片]", pendingImage?.file);
    setInput("");
    clearPendingImage();
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [input, disabled, onSend, pendingImage, clearPendingImage]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const textarea = e.target;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith("image/")) {
      return;
    }

    const preview = URL.createObjectURL(file);
    setPendingImage({ file, preview });

    // Reset input so same file can be re-selected
    e.target.value = "";
  }, []);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) {
          if (pendingImage) {
            URL.revokeObjectURL(pendingImage.preview);
          }
          const preview = URL.createObjectURL(file);
          setPendingImage({ file, preview });
        }
        return;
      }
    }
  }, [pendingImage]);

  return (
    <div className="border-t bg-background px-4 pb-4 pt-3">
      <div className="mx-auto max-w-3xl">
        {/* Image preview */}
        {pendingImage && (
          <div className="mb-2 inline-flex items-start gap-2 rounded-lg border bg-muted/30 p-2">
            <div className="relative">
              <img
                src={pendingImage.preview}
                alt="待发送图片"
                className="h-20 w-20 rounded-md object-cover"
              />
              <button
                onClick={clearPendingImage}
                className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <ImageIcon className="h-3 w-3" />
              {pendingImage.file.name.length > 20
                ? pendingImage.file.name.slice(0, 20) + "..."
                : pendingImage.file.name}
            </div>
          </div>
        )}

        <div className="flex items-end gap-2 rounded-xl border bg-muted/30 p-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleFileSelect}
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0"
            disabled={disabled}
            title="上传图片"
            aria-label="上传图片"
            onClick={() => fileInputRef.current?.click()}
          >
            <Paperclip className="h-4 w-4" />
          </Button>
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
            className="min-h-[40px] max-h-[200px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            rows={1}
            disabled={disabled}
          />
          <Button
            size="icon"
            className="h-9 w-9 shrink-0"
            onClick={handleSend}
            disabled={disabled || (!input.trim() && !pendingImage)}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
