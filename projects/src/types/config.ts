/** 项目价格/活动配置项 */
export interface PriceConfig {
  id?: string;
  sys_platform?: string;
  uuid?: string;
  bstudio_create_time?: string;
  /** 大品类/项目（必填） */
  project_name: string;
  /** 日常单次价（必填） */
  daily_price: number;
  /** 新客体验价（必填） */
  new_price: number;
  /** 老客单次价（必填） */
  old_price: number;
  /** 老客推荐卡项 */
  old_card?: string;
  /** 活动价 */
  promo_price?: number;
  /** 活动适用人群 */
  promo_target?: string;
  /** 活动开始时间 */
  promo_start?: string;
  /** 活动结束时间 */
  promo_end?: string;
  /** 可赠送福利 */
  gift_item?: string;
  /** 福利触发场景 */
  gift_scene?: string;
  /** 状态，启用/停用 */
  status?: boolean;
  /** 报价备注，内部使用 */
  price_note?: string;
}

/** 工作流请求参数 */
export interface ConfigWorkflowRequest {
  action: "query" | "insert" | "update" | "delete";
  data?: Partial<PriceConfig>;
}

/** 工作流返回的 output 数组项 */
export type ConfigWorkflowOutput = Record<string, unknown>;
