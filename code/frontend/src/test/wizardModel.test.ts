/**
 * Phase D 终审 Important #2 + Minor #5：向导默认版本真数据形态（version_no="vN"）。
 *
 * 后端真形态 version_no 为 "v1"/"v2"（带前缀，见 my_strategies._serialize /
 * StrategyService.create_version），此前 pickDefaultVersion 用 Number(v.version_no)
 * 直接转数值 → NaN，reduce 恒返第一项=最旧版；且 StrategyStatus 无 PUBLISHED
 * （DRAFT/PENDING_REVIEW/STAGING/ACTIVE/DISABLED/REJECTED），PUBLISHED 分支为死代码。
 * 正确口径：剥 v/V 前缀数值比较 + 优先可运行（STAGING/ACTIVE，对齐后端
 * _RUNNABLE_VERSION_STATUSES），无可运行回退最高版本号。
 */
import { describe, expect, it } from 'vitest';

import {
  formatVersionNo,
  pickDefaultVersion,
  versionNoNum,
  type WizardVersion,
} from '../components/strategy/wizardModel';

function v(version_no: string, status: string, id?: string): WizardVersion {
  return {
    strategy_version_id: id ?? `sv-${version_no}`,
    version_no,
    status,
  };
}

describe('versionNoNum：version_no → 数值（剥 v/V 前缀）', () => {
  it('"v1"/"V2"/"10" → 1/2/10（"v10">"v9" 按数值）', () => {
    expect(versionNoNum('v1')).toBe(1);
    expect(versionNoNum('V2')).toBe(2);
    expect(versionNoNum('10')).toBe(10);
    expect(versionNoNum('v10')).toBeGreaterThan(versionNoNum('v9'));
  });

  it('空/不可解析 → NaN', () => {
    expect(versionNoNum(undefined)).toBeNaN();
    expect(versionNoNum('')).toBeNaN();
    expect(versionNoNum('draft')).toBeNaN();
  });
});

describe('pickDefaultVersion（真形态 "vN"）', () => {
  it('乱序 ["v1","v3","v2"] → 默认选 v3（数值最高，非首项/字典序）', () => {
    const versions = [v('v1', 'STAGING'), v('v3', 'STAGING'), v('v2', 'STAGING')];
    expect(pickDefaultVersion(versions)).toBe('sv-v3');
  });

  it('优先可运行版本（STAGING/ACTIVE）：[v2 DRAFT, v1 ACTIVE] → v1', () => {
    const versions = [v('v2', 'DRAFT'), v('v1', 'ACTIVE')];
    expect(pickDefaultVersion(versions)).toBe('sv-v1');
  });

  it('无可运行版本回退最高版本号：[v1 DRAFT, v3 DRAFT, v2 DRAFT] → v3', () => {
    const versions = [v('v1', 'DRAFT'), v('v3', 'DRAFT'), v('v2', 'DRAFT')];
    expect(pickDefaultVersion(versions)).toBe('sv-v3');
  });

  it('裸数字形态兼容（旧 fixture/异常序列化）：["1","2"] → 2', () => {
    const versions = [v('1', 'STAGING'), v('2', 'STAGING')];
    expect(pickDefaultVersion(versions)).toBe('sv-2');
  });

  it('空列表 → null', () => {
    expect(pickDefaultVersion([])).toBeNull();
  });
});

describe('formatVersionNo：渲染层规范化（Minor #5，防 "vv1"）', () => {
  it('已含 v/V 前缀原样显示；裸数字补 v', () => {
    expect(formatVersionNo('v2')).toBe('v2');
    expect(formatVersionNo('V2')).toBe('V2');
    expect(formatVersionNo('2')).toBe('v2');
  });
});
