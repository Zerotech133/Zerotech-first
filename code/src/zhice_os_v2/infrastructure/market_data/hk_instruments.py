"""HK 全量正股枚举（HKEX 官方证券列表，权威、免 key、不依赖东财）。

HKEX 每日发布 List of Securities（xlsx），含全部上市证券及类别/每手股数/ISIN。
取 Category == 'Equity' 即全量正股（~2800）。作 instrument 同步的权威主源，
东财枚举为兜底（见 scheduler_jobs.run_sync_instruments_job）。
"""
from __future__ import annotations

import io

import pandas as pd
import requests

_URL = ("https://www.hkex.com.hk/eng/services/trading/securities/"
        "securitieslists/ListOfSecurities.xlsx")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def fetch_hkex_equities(timeout: int = 40) -> list[dict]:
    """下载 HKEX 官方列表 → 全量正股 [{'code'(5位),'name','lot_size'}]。

    失败抛异常（调用方兜底东财）。表头在第 3 行（skiprows=2）。
    """
    r = requests.get(_URL, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), skiprows=2, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    eq = df[df["Category"].astype(str).str.strip() == "Equity"]
    out: list[dict] = []
    for _, row in eq.iterrows():
        code = str(row.get("Stock Code") or "").strip()
        if not code.isdigit():
            continue
        name = str(row.get("Name of Securities") or "").strip()
        lot_raw = str(row.get("Board Lot") or "").replace(",", "").strip()
        lot = int(lot_raw) if lot_raw.isdigit() else None
        out.append({"code": f"{int(code):05d}", "name": name, "lot_size": lot})
    return out
