# -*- coding: utf-8 -*-
"""Office 文档读写（零依赖优先）：
· .docx 读：zipfile + word/document.xml 解析段落/文本（stdlib）；
· .docx 写：生成最小合法 OOXML 包（stdlib，Word/WPS 可开）；
· .md → .docx：标题/段落/代码块/列表粗转；
· .xlsx 读/写：openpyxl（若安装；未装给出明确错误）。
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _paragraph(text: str, style: str = "") -> str:
    ppr = ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else ""
    return '<w:p>%s<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (
        ppr,
        _esc(text),
    )


def _table(rows: list) -> str:
    cells = []
    for row in rows or []:
        tcs = "".join(
            '<w:tc><w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p></w:tc>'
            % _esc(c)
            for c in row
        )
        cells.append("<w:tr>%s</w:tr>" % tcs)
    return (
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
        "<w:tblGrid/>%s</w:tbl>" % "".join(cells)
    )


def write_docx(
    path,
    title: str,
    paragraphs: list,
    headings: list | None = None,
    tables: list | None = None,
) -> dict:
    """headings: [{"level":1,"text":...}]；paragraphs: [str]；tables: [[[cell,...],...]]。"""
    body = []
    if title:
        body.append(_paragraph(title, "Title"))
    for h in headings or []:
        body.append(_paragraph(h.get("text", ""), "Heading%d" % int(h.get("level", 1))))
    for p in paragraphs or []:
        body.append(_paragraph(p))
    for rows in tables or []:
        body.append(_table(rows))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>%s<w:sectPr/></w:body></w:document>" % "".join(body)
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", document)
    return {
        "ok": True,
        "path": str(p),
        "bytes": p.stat().st_size,
        "paragraphs": len(paragraphs or []) + len(headings or []) + (1 if title else 0),
        "tables": len(tables or []),
        "table_rows": sum(len(r) for r in tables or []),
    }


def read_docx(path, max_paragraphs: int = 1000) -> dict:
    p = Path(path)
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    try:
        root = ET.fromstring(xml)  # nosec B314 - ElementTree 不解析外部实体（无网络/DTD 展开）
    except ET.ParseError as e:
        return {"ok": False, "error": "xml_parse: %s" % str(e)[:120]}
    paras = []
    for pnode in root.iter(W + "p"):
        texts = [t.text or "" for t in pnode.iter(W + "t")]
        txt = "".join(texts).strip()
        if txt:
            paras.append(txt)
        if len(paras) >= max_paragraphs:
            break
    tables = []
    for tbl in root.iter(W + "tbl"):
        rows = []
        for tr in tbl.iter(W + "tr"):
            cells = []
            for tc in tr.iter(W + "tc"):
                cell_text = "".join(t.text or "" for t in tc.iter(W + "t")).strip()
                cells.append(cell_text)
            if cells:
                rows.append(cells)
        tables.append(rows)
    return {
        "ok": True,
        "path": str(p),
        "paragraphs": len(paras),
        "text": paras,
        "tables": len(tables),
        "table_rows": [len(r) for r in tables],
        "table_data": tables,
    }


def md_to_docx(md_path, out_path) -> dict:
    lines = Path(md_path).read_text(encoding="utf-8", errors="replace").splitlines()
    title = ""
    headings, paragraphs = [], []
    for ln in lines:
        s = ln.rstrip()
        if not s:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            if level == 1 and not title:
                title = m.group(2).strip()
            else:
                headings.append({"level": min(level, 3), "text": m.group(2).strip()})
            continue
        if s.startswith("```"):
            continue
        paragraphs.append(
            s.lstrip("-* ").strip() if s.lstrip().startswith(("-", "*")) else s
        )
    return write_docx(out_path, title, paragraphs, headings)


def read_xlsx(path, sheet: str = "", max_rows: int = 200) -> dict:
    try:
        from openpyxl import load_workbook  # noqa: WPS433
    except ImportError:
        return {"ok": False, "error": "openpyxl_not_installed"}
    try:
        wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    except OSError as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            break
        rows.append(["" if v is None else str(v) for v in row])
    return {
        "ok": True,
        "path": str(path),
        "sheet": ws.title,
        "rows": rows,
        "row_count": len(rows),
    }


def write_xlsx(path, rows: list, sheet: str = "Sheet1") -> dict:
    try:
        from openpyxl import Workbook  # noqa: WPS433
    except ImportError:
        return {"ok": False, "error": "openpyxl_not_installed"}
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows or []:
        ws.append(list(row))
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output))
    return {
        "ok": True,
        "path": str(output),
        "bytes": output.stat().st_size,
        "rows": len(rows or []),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Office 文档读写（docx/xlsx）")
    ap.add_argument("--read-docx", default="")
    ap.add_argument("--write-docx", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--md2docx", nargs=2, metavar=("MD", "DOCX"))
    ap.add_argument("--read-xlsx", default="")
    ap.add_argument("--write-xlsx", default="")
    ap.add_argument("--rows-json", default="[]")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.read_docx:
        rep = read_docx(args.read_docx)
    elif args.write_docx:
        rep = write_docx(
            args.write_docx, args.title, ["由 zhaoyun office_doc 生成的示例文档。"]
        )
    elif args.md2docx:
        rep = md_to_docx(args.md2docx[0], args.md2docx[1])
    elif args.read_xlsx:
        rep = read_xlsx(args.read_xlsx)
    elif args.write_xlsx:
        rep = write_xlsx(args.write_xlsx, json.loads(args.rows_json))
    else:
        rep = {"ok": False, "error": "no_action"}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
