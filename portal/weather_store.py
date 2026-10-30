"""weather_store.py — 环境日程域·天气源（wttr.in 免费无 key）。

职责：refresh() 拉默认城市天气存 JSON 账本；load() 给桥端点。
城市：环境变量 COUNCIL_WEATHER_CITIES（逗号分隔，默认 重庆,北京）。
边界：wttr.in 免费 JSON 接口、无凭据；失败保留上次数据（如实标 stale）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "data", "weather_latest.json")
CITIES = [
    c.strip()
    for c in os.environ.get("COUNCIL_WEATHER_CITIES", "重庆,北京").split(",")
    if c.strip()
]
UA = "Mozilla/5.0 (compatible; ZeroOS-Weather/0.1)"


def refresh() -> dict:
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cities": {}, "stale": False}
    for city in CITIES:
        try:
            url = "https://wttr.in/%s?format=j1" % urllib.request.quote(city)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with safe_urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
            cur = (d.get("current_condition") or [{}])[0]
            out["cities"][city] = {
                "desc": ",".join(
                    x.get("value", "") for x in (cur.get("weatherDesc") or [])[:1]
                ),
                "temp_c": cur.get("temp_C"),
                "feels": cur.get("FeelsLikeC"),
                "humidity": cur.get("humidity"),
                "max_min": "/".join(
                    filter(
                        None,
                        [
                            ((d.get("weather") or [{}])[0].get("maxtempC") or ""),
                            ((d.get("weather") or [{}])[0].get("mintempC") or ""),
                        ],
                    )
                ),
            }
        except Exception as e:
            out["cities"][city] = {"err": str(e)[:80]}
    if all("err" in v for v in out["cities"].values()) and os.path.isfile(STORE):
        prev = json.load(open(STORE, encoding="utf-8"))
        prev["stale"] = True
        return prev
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def load() -> dict:
    if not os.path.isfile(STORE):
        return {"at": "", "cities": {}, "stale": None}
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"at": "", "cities": {}, "stale": None}


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False, indent=1)[:600])
