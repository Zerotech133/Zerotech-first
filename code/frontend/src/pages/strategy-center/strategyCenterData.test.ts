import { describe, expect, it } from 'vitest';
import {
  diverseLatestPicks,
  strategyStatus,
  type StrategyCenterData,
  type StrategyCenterPick,
  type StrategyCenterStrategy,
} from './strategyCenterData';

const pick = (sid: string, ts_code: string): StrategyCenterPick => ({
  signal_date: '20261105',
  sid,
  ts_code,
  name: ts_code,
  industry: '',
  rank_in: 1,
  gap_pct: 1,
  stop_px: 1,
  ref_px: 1,
  score: 1,
  vetoed: 0,
  veto_why: '',
});

const strategy = (overrides: Partial<StrategyCenterStrategy>) =>
  ({
    sid: 'LX01',
    name: 'LX01',
    in_pool: true,
    enabled: 1,
    ...overrides,
  }) as StrategyCenterStrategy;

describe('strategyCenterData', () => {
  it('round-robins latest picks across strategies', () => {
    const data = {
      latest_picks: [pick('LX01', 'A'), pick('LX01', 'B'), pick('LX02', 'C')],
    } as unknown as StrategyCenterData;
    expect(diverseLatestPicks(data, 3).map((p) => p.ts_code)).toEqual(['A', 'C', 'B']);
  });

  it('does not infinite-loop when picks are exhausted', () => {
    const data = {
      latest_picks: [pick('LX01', 'A')],
    } as unknown as StrategyCenterData;
    expect(diverseLatestPicks(data, 8)).toHaveLength(1);
  });

  it('maps pool and enabled state to labels', () => {
    expect(strategyStatus(strategy({}))).toEqual({ label: '在池', tone: 'ok' });
    expect(strategyStatus(strategy({ enabled: 0 }))).toEqual({
      label: '已停用',
      tone: 'danger',
    });
    expect(strategyStatus(strategy({ in_pool: false }))).toEqual({
      label: '池外',
      tone: 'muted',
    });
  });
});
