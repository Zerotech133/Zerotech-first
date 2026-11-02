import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Card,
  Drawer,
  Empty,
  Progress,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../services/api';
import { useMarket } from '../contexts/MarketContext';
import { useNavigate } from 'react-router-dom';
import SymbolLink from '../components/SymbolLink';
import { strategyLabel } from '../i18n/strategyName';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

/** /candidates/pools 单项 */
interface PoolItem {
  pool_id: string;
  strategy_version_id: string;
  strategy_name: string;
  pool_date: string | null;
  pool_type: string;
  candidate_count: number;
  declared_candidate_count?: number;
  updated_at?: string;
  status: string;
}

/** /candidates/pools/{pool_id}/assets 单项 */
interface CandidateAsset {
  candidate_id: string;
  ts_code: string;
  candidate_score: number | null;
  candidate_reason: string | null;
  status: string;
  ref_price: number | null;
}

type Row = CandidateAsset & { _pool: PoolItem };

/** label 为 i18n key（pages 命名空间），渲染处 t(label) */
const STATUS_META: Record<string, { label: string; color: string }> = {
  ACTIVE: { label: 'candidates.statusActive', color: 'green' },
  EXPIRED: { label: 'candidates.statusExpired', color: 'default' },
  PROMOTED: { label: 'candidates.statusPromoted', color: 'blue' },
};

function fmtScore(v: number | null): string {
  return v != null ? v.toFixed(4) : '—';
}

function fmtPrice(v: number | null): string {
  return v != null ? v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 }) : '—';
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function CandidatesPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const navigate = useNavigate();

  const statusTag = (s: string) => {
    const m = STATUS_META[s];
    return <Tag color={m?.color ?? 'default'}>{m ? t(m.label) : s}</Tag>;
  };

  const [pools, setPools] = useState<PoolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeStrategy, setActiveStrategy] = useState('__ALL__');
  const [assetsByPool, setAssetsByPool] = useState<Record<string, CandidateAsset[]>>({});
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [selected, setSelected] = useState<Row | null>(null);
  const [tick, setTick] = useState(0);

  // 切市场重置选中策略与详情
  useEffect(() => {
    setActiveStrategy('__ALL__');
    setSelected(null);
  }, [market]);

  /* 候选池批次列表（最新一个交易日） */
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<PoolItem[]>(`/candidates/pools?market=${market}`)
      .then((list) => {
        if (!cancelled) setPools(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        if (!cancelled) setPools([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [market, tick]);

  const latestDate = pools.length ? pools[0].pool_date : null;

  /* 当前选中策略下的池 */
  const visiblePools = useMemo(
    () => pools.filter((p) => activeStrategy === '__ALL__' || p.strategy_version_id === activeStrategy),
    [pools, activeStrategy],
  );

  /* 各池候选明细（懒加载当前选中策略涉及的池） */
  const neededIds = useMemo(() => visiblePools.map((p) => p.pool_id).sort().join(','), [visiblePools]);
  useEffect(() => {
    if (!visiblePools.length) {
      setAssetsByPool({});
      return;
    }
    let cancelled = false;
    setAssetsLoading(true);
    Promise.all(
      visiblePools.map((p) =>
        api<CandidateAsset[]>(`/candidates/pools/${p.pool_id}/assets`).catch(() => [] as CandidateAsset[]),
      ),
    ).then((results) => {
      if (cancelled) return;
      const map: Record<string, CandidateAsset[]> = {};
      visiblePools.forEach((p, i) => {
        map[p.pool_id] = results[i] ?? [];
      });
      setAssetsByPool(map);
      setAssetsLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [neededIds, tick]);

  /* 合并后的候选列表（全部策略 = 各池合并，按评分降序） */
  const assets = useMemo(() => {
    const merged: Row[] = [];
    visiblePools.forEach((p) => {
      (assetsByPool[p.pool_id] ?? []).forEach((a) => merged.push({ ...a, _pool: p }));
    });
    merged.sort((a, b) => (b.candidate_score ?? -1) - (a.candidate_score ?? -1));
    return merged;
  }, [visiblePools, assetsByPool]);

  const totalCount = assets.length;

  /* 策略 Tab：「全部策略」+ 各策略（策略名+候选数） */
  const strategyTabs = useMemo(() => {
    const items = [{ key: '__ALL__', label: t('candidates.allStrategiesTab', { n: totalCount }) }];
    pools.forEach((p) => {
      const n = (assetsByPool[p.pool_id] ?? []).length;
      items.push({ key: p.strategy_version_id, label: t('candidates.strategyTab', { name: strategyLabel(p.strategy_name), n }) });
    });
    return items;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pools, assetsByPool, totalCount]);

  const columns: ColumnsType<Row> = [
    {
      title: t('candidates.colRank'),
      key: 'rank',
      width: 70,
      render: (_v, _row, idx) => <span style={{ fontFamily: 'monospace' }}>{idx + 1}</span>,
    },
    {
      title: t('candidates.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string, row) => <SymbolLink code={v} onClick={() => setSelected(row)} />,
    },
    {
      title: t('candidates.colScore'),
      dataIndex: 'candidate_score',
      key: 'candidate_score',
      width: 170,
      sorter: (a, b) => (a.candidate_score ?? -1) - (b.candidate_score ?? -1),
      render: (v: number | null) => (
        <Space size={8}>
          <Progress
            percent={v != null ? Math.min(100, Math.round(v * 100)) : 0}
            size="small"
            showInfo={false}
            style={{ width: 80, marginBottom: 0 }}
          />
          <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{fmtScore(v)}</span>
        </Space>
      ),
    },
    {
      title: '策略来源',
      key: 'strategy_name',
      width: 150,
      render: (_v, row) => <Text ellipsis style={{ maxWidth: 140 }}>{strategyLabel(row._pool.strategy_name)}</Text>,
    },
    {
      title: '更新时间',
      key: 'updated_at',
      width: 145,
      render: (_v, row) => <Text type="secondary" style={{ fontSize: 11 }}>{(row._pool.updated_at || '').slice(0, 16).replace('T', ' ')}</Text>,
    },
    {
      title: t('candidates.colStatus'),
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v: string) => statusTag(v),
    },
    {
      title: t('candidates.colRefPrice'),
      dataIndex: 'ref_price',
      key: 'ref_price',
      width: 100,
      align: 'right',
      render: (v: number | null) => (
        <span style={{ fontFamily: 'monospace' }}>{fmtPrice(v)}</span>
      ),
    },
    {
      title: t('candidates.colAction'),
      key: 'action',
      width: 80,
      render: (_v, row) => (
        <a onClick={(e) => { e.stopPropagation(); setSelected(row); }}>{t('candidates.detail')}</a>
      ),
    },
  ];

  const detail = selected;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>{t('candidates.title')}</Title>
        <Button icon={<ReloadOutlined />} size="small" onClick={() => setTick((n) => n + 1)} loading={loading}>
          {t('candidates.refresh')}
        </Button>
        <div style={{ flex: 1 }} />
        <Text type="secondary">
          {latestDate ? t('candidates.poolDate', { date: latestDate }) : ''}
          {latestDate && pools.length ? ' · ' : ''}
          {pools.length ? t('candidates.poolSummary', { strategies: visiblePools.length, count: totalCount }) : ''}
          {pools.length ? ` · 更新 ${String(pools[0].updated_at || '').slice(0, 16).replace('T', ' ')}` : ''}
        </Text>
      </div>

      {loading ? (
        <div style={{ padding: 64, textAlign: 'center' }}><Spin /></div>
      ) : pools.length === 0 ? (
        <Card size="small">
          <Empty description={t('candidates.emptyPool')} style={{ padding: 32 }} />
        </Card>
      ) : (
        <Card size="small">
          <Tabs
            activeKey={activeStrategy}
            onChange={setActiveStrategy}
            items={strategyTabs}
            style={{ marginBottom: 8 }}
          />
          <Table<Row>
            rowKey={(r) => `${r._pool.pool_id}:${r.candidate_id}`}
            columns={columns}
            dataSource={assets}
            loading={assetsLoading}
            size="small"
            pagination={{ pageSize: 50, showSizeChanger: false, showTotal: (total) => t('candidates.total', { count: total }) }}
            onRow={(row) => ({ onClick: () => setSelected(row), style: { cursor: 'pointer' } })}
          />
        </Card>
      )}

      <Drawer
        open={detail !== null}
        onClose={() => setSelected(null)}
        styles={{ wrapper: { width: 480 } }}
        title={detail ? t('candidates.drawerTitle', { code: detail.ts_code }) : ''}
      >
        {detail && (
          <div>
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 16 }}>{t('candidates.basicInfo')}</div>
            <Button size="small" type="primary" style={{ marginBottom: 12 }} onClick={() => navigate(`/${market.toLowerCase()}/stock/${encodeURIComponent(detail.ts_code)}`)}>打开个股全景</Button>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 24 }}>
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>{t('candidates.symbolCode')}</Text>
                <SymbolLink code={detail.ts_code} />
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>{t('candidates.colScore')}</Text>
                <span style={{ fontFamily: 'monospace', fontWeight: 600, color: '#1677ff' }}>
                  {fmtScore(detail.candidate_score)}
                </span>
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>{t('candidates.colStatus')}</Text>
                {statusTag(detail.status)}
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>{t('candidates.colRefPrice')}</Text>
                <span style={{ fontFamily: 'monospace' }}>{fmtPrice(detail.ref_price)}</span>
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>{t('candidates.ownerStrategy')}</Text>
                <span>{strategyLabel(detail._pool.strategy_name)}</span>
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>{t('candidates.poolDateLabel')}</Text>
                <span style={{ fontFamily: 'monospace' }}>{detail._pool.pool_date ?? '—'}</span>
              </div>
            </div>

            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 8 }}>{t('candidates.enterReason')}</div>
            <div
              style={{
                fontFamily: 'monospace',
                fontSize: 12,
                lineHeight: 1.8,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                background: '#f6f7f9',
                borderRadius: 6,
                padding: '10px 12px',
                color: '#3b4252',
              }}
            >
              {detail.candidate_reason ?? '—'}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
