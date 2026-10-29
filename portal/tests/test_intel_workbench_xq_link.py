"""Offline regression for evidence-to-Xueqiu ticker links; no live source calls."""

from pathlib import Path
import re
import shutil
import subprocess

import pytest


PAGE = Path(__file__).resolve().parents[1] / "intel_workbench.html"


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("000993.SZ", "000993"),
        ("600337.SH", "600337"),
        ("605100.SH", "605100"),
        ("830001.BJ", "830001"),
    ],
)
def test_xq_link_uses_bare_six_digit_code(symbol, expected, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node runtime unavailable for DOM-free JS regression")
    html = PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html)
    # 页头/页尾注入的无关 <script> 不算；取包含主逻辑的那一段
    main = [s for s in scripts if "const groups=" in s or "function evidenceDetail(" in s]
    assert len(main) == 1, "main evidence script must be unique"
    js = main[0]
    # Extract the actual production function, rather than reimplementing normalization.
    match = re.search(
        r"function evidenceDetail\(\)\{.*?(?=\nfunction evidenceResults\()", js, re.S
    )
    assert match, "production evidenceDetail function must be found"
    harness = r"""
let evidenceItems=[{code:CODE,title:'fixture',evidence_id:'fixture',source_ref:'fixture'}];
let selectedEvidence=0;
const box={innerHTML:''};
globalThis.document={querySelector:()=>box,querySelectorAll:()=>[]};
const safe=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
FUNCTION
evidenceDetail();
const xq=box.innerHTML.match(/href="(\/api\/xq\/intel\/ticker\?[^\"]+)"/);
const kg=box.innerHTML.match(/href="(\/kg\/stock\/[^\"]+)"/);
if(!xq||!kg)process.exit(2);
const url=new URL(xq[1],'http://localhost');
if(url.searchParams.get('code')!==EXPECTED||!kg[1].includes(CODE))process.exit(3);
"""
    script = (
        harness.replace("FUNCTION", match.group(0))
        .replace("CODE", repr(symbol))
        .replace("EXPECTED", repr(expected))
    )
    script_file = tmp_path / "link.cjs"
    script_file.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(script_file)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_invalid_code_does_not_create_cross_source_links(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node runtime unavailable for DOM-free JS regression")
    html = PAGE.read_text(encoding="utf-8")
    js = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html)[1]
    match = re.search(
        r"function evidenceDetail\(\)\{.*?(?=\nfunction evidenceResults\()", js, re.S
    )
    assert match
    script = (
        """let evidenceItems=[{code:'000993.SZ<script>',title:'fixture'}],selectedEvidence=0;
const box={innerHTML:''};globalThis.document={querySelector:()=>box,querySelectorAll:()=>[]};
const safe=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));
"""
        + match.group(0)
        + "\nevidenceDetail();if(box.innerHTML.includes('/api/xq/intel/ticker?'))process.exit(2);\n"
    )
    script_file = tmp_path / "invalid.cjs"
    script_file.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(script_file)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
