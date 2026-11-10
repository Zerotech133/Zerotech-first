# -*- coding: utf-8 -*-
"""互动易公司页 XHR 抓包：找按公司拉问答的 API。

Playwright 打开搜索页 → 等渲染 → 点第一个公司卡片 → 录制全部
XHR/Fetch 响应，打印 question/qa 相关端点与样本。
"""
import json
import sys
from playwright.sync_api import sync_playwright

hits = []

def on_response(resp):
    url = resp.url
    if any(k in url.lower() for k in ('qa', 'question', 'search', 'list', 'company')):
        try:
            body = resp.text()
            ct = (resp.headers or {}).get('content-type', '')
            if 'json' in ct and len(body) > 100:
                hits.append({'url': url[:160], 'status': resp.status,
                             'body_head': body[:200]})
        except Exception:
            pass

with sync_playwright() as p:
    b = p.chromium.launch(headless=False, args=['--window-position=3000,3000'])
    pg = b.new_page()
    pg.on('response', on_response)
    pg.goto('https://irm.cninfo.com.cn/newircs/index/search?keyword=%E4%B8%AD%E9%99%85%E6%97%AD%E5%88%9B',
            wait_until='domcontentloaded', timeout=45000)
    pg.wait_for_timeout(6000)
    # 点公司标签（若出现）
    try:
        tab = pg.query_selector('text=中际旭创')
        if tab:
            tab.click()
            pg.wait_for_timeout(6000)
    except Exception as e:
        print('click fail:', str(e)[:60])
    # 停在页面看 XHR
    print('captured:', len(hits))
    seen = set()
    for h in hits:
        key = h['url'].split('?')[0]
        if key in seen:
            continue
        seen.add(key)
        print('---', h['status'], h['url'])
        print('   ', h['body_head'][:160].replace(chr(10), ' '))
    b.close()
