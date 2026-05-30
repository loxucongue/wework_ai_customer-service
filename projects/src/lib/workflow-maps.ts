/** 意图 intent → 中文 */
export const INTENT_MAP: Record<string, string> = {
  greeting: "打招呼",
  project_inquiry: "项目咨询",
  price_inquiry: "价格咨询",
  bargain: "砍价",
  store_inquiry: "门店咨询",
  campaign_inquiry: "活动咨询",
  image_inquiry: "看图咨询",
  competitor_compare: "竞品对比",
  trust_issue: "信任问题",
  appointment_intent: "预约意向",
  appointment_confirm: "确认预约",
  appointment_change: "改约",
  appointment_cancel: "取消预约",
  pre_visit_question: "到店前咨询",
  after_sales: "售后",
  emotion_chat: "情感闲聊",
  silence_return: "沉默回归",
  human_request: "转人工",
  complaint_refund: "投诉退款",
};

/** 子流程 subflow → 中文 */
export const SUBFLOW_MAP: Record<string, string> = {
  SF1_new_customer_reply: "新客首响",
  SF2_profile_collect: "画像收集",
  SF3_project_consult: "项目咨询",
  SF4_face_consult: "面诊咨询",
  SF5_competitor_response: "竞品应对",
  SF6_store_match: "门店匹配",
  SF7_price_consult: "价格咨询",
  SF8_campaign_push: "活动推送",
  SF9_appointment: "邀约到店",
  SF10_trust_build: "信任建立",
  SF11_emotion_companion: "情感陪伴",
  SF12_after_sales: "售后服务",
  DIRECT_REPLY: "直接回复",
  HUMAN_HANDOFF: "转人工",
};

/** 场景 scene (生命周期) → 中文 */
export const SCENE_MAP: Record<string, string> = {
  S0: "新客首次来话",
  S1: "新客首次来话",
  S2: "画像收集中",
  S3: "深度咨询",
  S4: "邀约协商",
  S5: "待到店来话",
  S6: "到店未成交",
  S7: "老客来话",
  S8: "沉默客回归",
  // 兼容带下划线的格式，如 S1_icebreaking
};

/**
 * 解析 scene 字段，支持 "S1"、"S1_icebreaking" 等格式
 */
export function translateScene(raw: string): string {
  // 先尝试精确匹配
  if (SCENE_MAP[raw]) return SCENE_MAP[raw];
  // 尝试取前缀匹配，如 "S1_icebreaking" → "S1"
  const prefix = raw.split("_")[0];
  if (prefix && SCENE_MAP[prefix]) return SCENE_MAP[prefix];
  return raw;
}

export function translateIntent(raw: string): string {
  return INTENT_MAP[raw] ?? raw;
}

export function translateSubflow(raw: string): string {
  return SUBFLOW_MAP[raw] ?? raw;
}
