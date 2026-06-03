export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  /** 消息内容类型：text 或 image */
  contentType?: "text" | "image";
  /** 用户上传的图片 URL（对象存储签名链接） */
  imageUrl?: string;
  /** 回答耗时（毫秒），仅 assistant 消息有 */
  duration?: number;
  /** 工作流返回的附加信息 */
  meta?: {
    intent?: string;
    scene?: string;
    subflow?: string;
    requestId?: string;
    traceUrl?: string;
    toolResultKeys?: string[];
    toolCalls?: unknown[];
    profileUpdate?: unknown;
    eventUpdates?: unknown[];
    imageInfo?: unknown;
    memoryLoaded?: boolean;
    raw?: Record<string, unknown>;
  };
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

export interface WorkflowOutputItem {
  content: string;
  order: number;
  type: string;
}

export interface WorkflowResponse {
  intent?: string;
  meta?: Record<string, unknown>;
  output?: WorkflowOutputItem[];
  request_id?: string;
  scene?: string;
  subflow?: string;
  trace_url?: string;
}
