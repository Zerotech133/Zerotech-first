/**
 * 策略中台（qengine_sync）只读 API client。
 *
 * 只调用后端 /api/v1/strategy-center GET；不写库、不触发交易/批准。
 */
import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { StrategyCenterData, StrategyCenterDetail } from './strategyCenterData';

let snapshotCache: StrategyCenterData | null = null;
let snapshotPending: Promise<StrategyCenterData> | null = null;

export async function fetchStrategyCenterSnapshot(
  force = false,
): Promise<StrategyCenterData> {
  if (!force && snapshotCache) return snapshotCache;
  if (snapshotPending) return snapshotPending;
  snapshotPending = api<StrategyCenterData>('/strategy-center/snapshot')
    .then((data) => {
      snapshotCache = data;
      return data;
    })
    .finally(() => {
      snapshotPending = null;
    });
  return snapshotPending;
}

export async function fetchStrategyCenterDetail(
  sid: string,
  signal?: AbortSignal,
): Promise<StrategyCenterDetail> {
  return api<StrategyCenterDetail>(
    `/strategy-center/strategies/${encodeURIComponent(sid)}`,
    { signal },
  );
}

export function useStrategyCenterSnapshot() {
  const [data, setData] = useState<StrategyCenterData | null>(snapshotCache);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!snapshotCache);

  const load = (force = false) => {
    setLoading(true);
    setError('');
    fetchStrategyCenterSnapshot(force)
      .then((next) => setData(next))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (snapshotCache) {
      setData(snapshotCache);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    fetchStrategyCenterSnapshot()
      .then((next) => {
        if (alive) setData(next);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { data, error, loading, reload: () => load(true) };
}
