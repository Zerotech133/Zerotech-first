# -*- coding: utf-8 -*-
import json
import sys

sys.path.insert(0, "portal")
import api_news_intel as ni

raw = json.dumps({"bias": "bull", "conf": 0.9, "impact_cn": "高"},
                 ensure_ascii=False)
print("raw:", raw)
print("parse:", ni._parse_jev(raw))
print("bad:", ni._parse_jev("not-json"))
print("empty:", ni._parse_jev(""))
