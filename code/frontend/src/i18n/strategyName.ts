// 策略名(库存中文，如"美股趋势动量v1") → 当前语言显示名。
// 数据类文案方案A：后端仍返回库存中文 strategy_name，前端按 domain.strategyName 映射翻译；
// 无映射(如新增策略未登记译名)则回退原名(中文)。语言切换由消费组件的 useTranslation 触发重渲染生效。
import i18n from './index';

export function strategyLabel(name?: string | null): string {
  if (!name) return '';
  // key 段用完整策略名；现有 9 个策略名均不含 '.'/':'(i18next 分隔符)，新增策略名须遵守。
  return i18n.t(`strategyName.${name}`, { ns: 'domain', defaultValue: name });
}
