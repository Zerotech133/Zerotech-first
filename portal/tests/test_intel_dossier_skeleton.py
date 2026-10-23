"""K-INTEL-20261022-FE: offline skeleton contracts, without loading live sources."""
from pathlib import Path
import re
import shutil
import subprocess


PAGE = Path(__file__).resolve().parents[1] / "intel_dossier_skeleton.html"
NODE = shutil.which("node")


def _page():
    return PAGE.read_text(encoding="utf-8")


def test_frontend_admission_and_five_views():
    html = _page()
    assert '<link rel="stylesheet" href="/static/tokens.css">' in html
    assert '<script src="/static/nav.js" defer></script>' in html
    assert html.count("<h2") == 6  # E2 第六屏后: overview/case/graph/sources/review/drill
    for view in ("overview", "case", "graph", "sources", "review", "drill"):
        assert f'<section id="{view}"' in html
        assert f'<option value="{view}">' in html
    assert 'id="crumb-now"' in html
    assert "location.hash" not in html


def test_offline_boundary_has_no_network_or_claimed_facts():
    html = _page()
    # E2 后受控演进: 允许对同源只读端点 /api/intel-graph 的 fetch; 仍禁外网/写操作/存储
    import json as _json
    for m in re.finditer(r"fetch\(([^)]*)\)", html):
        arg = m.group(1)
        assert "'/api/intel-graph/dossier?code='" in arg or "\"/api/intel-graph/dossier?code=\"" in arg, \
            f"fetch 目标越界(仅允许同源 dossier 端点): {arg[:80]}"
    for forbidden in ("XMLHttpRequest", "WebSocket(", "sendBeacon(", "http://", "https://",
                      "localStorage", "sessionStorage", "<form", "type=\"submit\""):
        assert forbidden not in html
    assert "历史 as_of 默认关闭" in html
    assert "没有批准、推送、写图或交易按钮" in html
    assert "真实证据未接入" in html
    assert "Tushare 等" in html


def test_dossier_untrusted_fields_are_text_only():
    html = _page()
    match = re.search(r"fetch\('/api/intel-graph/dossier\?code='.*?\}\);\s*</script>", html, re.S)
    assert match, "dossier rendering block missing"
    renderer = match.group(0)
    assert "out.innerHTML" not in renderer
    for field in ("e.rel_type", "e.pair", "e.evidence_key", "e.question"):
        assert field in renderer
    assert "title.textContent" in renderer
    assert "key.textContent" in renderer
    assert "question.textContent" in renderer
    assert "out.replaceChildren()" in renderer


def test_historical_eligibility_fails_closed():
    html = _page()
    match = re.search(r"// eligibility:start\s*(.*?)\s*// eligibility:end", html, re.S)
    assert match, "eligibility function missing"
    js = match.group(1)
    cases = r'''
const base = {
  rights: 'verified', reviewed: true, clockAuthorityVerified: true,
  sourceKey: 'synthetic/key', version: 'v1',
  occurred: '2026-10-21T08:00:00+08:00',
  published: '2026-10-21T09:00:00+08:00',
  firstSeen: '2026-10-21T09:05:00+08:00',
  available: '2026-10-21T09:06:00+08:00',
  validFrom: '2026-10-21T09:00:00+08:00'
};
const at = '2026-10-21T10:00:00+08:00';
const checks = [
  [base, at, true],
  [{...base, rights: 'synthetic-only'}, at, false],
  [{...base, clockAuthorityVerified: false}, at, false],
  [{...base, reviewed: false}, at, false],
  [{...base, published: null}, at, false],
  [{...base, firstSeen: '2026-10-21T11:00:00+08:00'}, at, false],
  [{...base, available: '2026-10-21T11:00:00+08:00'}, at, false],
  [{...base, occurred: '2026-10-21T11:00:00+08:00'}, at, false],
  [{...base, validFrom: '2026-10-21T11:00:00+08:00'}, at, false],
  [{...base, validTo: '2026-10-21T09:30:00+08:00'}, at, false],
  [{...base, sourceKey: ''}, at, false],
  [base, '2026-10-21T10:00:00', false]
];
for (const [row, moment, expected] of checks) {
  if (historicalEligibility(row, moment) !== expected) process.exit(1);
}
'''
    if NODE is None:
        raise AssertionError("Node.js is required to run behavioral contract tests")
    proc = subprocess.run([NODE, "-e", js + cases], capture_output=True,
                          text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
