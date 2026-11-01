# -*- coding: utf-8 -*-
"""scan_secrets 自验证：生成随机假密钥夹具 → 运行扫描器 → 程序化断言。
终端只输出 PASS/FAIL 汇总，不输出任何密钥材料（含脱敏片段）。"""
import os
import random
import string
import subprocess
import sys

TD = os.path.join(os.path.sep.join([os.environ.get("TEMP", "/tmp")]), "scan_selftest_2026")  # nosec B108 - 测试夹具/docker参数/字符串常量（已核实）
os.makedirs(os.path.join(TD, "tests"), exist_ok=True)
os.makedirs(os.path.join(TD, "docs"), exist_ok=True)
rng = random.Random(20261019)


def w(rel, content):
    with open(os.path.join(TD, rel), "w", encoding="utf-8") as f:
        f.write(content)


alpha = string.ascii_letters + string.digits
tok32 = "".join(rng.choices(alpha, k=32))
tok32b = "".join(rng.choices(alpha, k=32))
pwd = "Px" + "".join(rng.choices(alpha + "!@#", k=12)) + "9z"
pwd2 = "Qw" + "".join(rng.choices(alpha + "!@#", k=10)) + "7k"
lit = "Tf" + "".join(rng.choices(string.ascii_uppercase, k=6)) + "20" + "26"
fake_tok = "aa" + "b" * 28 + "1234"

w("tests/test_stub.py", "TUSHARE_TOKEN = '%s'  # fake fixture\n" % fake_tok)
w("config.py", "api_key = '%s'\npassword: \"%s\"\n" % (tok32, pwd))
w("docs/notes.md",
  "服务器 192.168.1.50 的密码为%s\n联系电话 13800001111\ncommit %s\n"
  % (lit, "a1f3c5d7e9b204861d3c5e7f9a1b3c5d7e9f0a1b"))
w(".env.local", "TUSHARE_TOKEN=%s\n" % tok32)
w("leaked.pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIBfake\n-----END RSA PRIVATE KEY-----\n")
# 同行混合密钥红线回归：一行同时含高熵串与真实形态密码，两个都必须被打码
w("mixed_line.py", "dburl = %s  # PWD: %s\n" % (tok32b, pwd2))
# compose 默认值回归：占位符引用行内的默认真实口令也必须被提取并打码
w("compose_like.yml", "conn: %s postgres://u:${POSTGRES_PASSWORD:-%s}@db\n" % (tok32b, pwd2))
w("clean.txt", "hello world, nothing to see here. just plain text lines.\n")
w("placeholder_cfg.py", "api_key = os.environ['API_KEY']\ntoken = ${TOKEN}\n")
# 第6轮调优回归（2026-10-19）：三种新误报形态必须不命中
w("interp_cfg.yml",
  "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in quantdinger/.env}\n"
  "url: postgresql://${U:-q}:${P:?x}@db:5432/d\n"
  "conn2: postgresql://${U:-q}:${POSTGRES_PASSWORD:-%s}@db\n" % pwd2)
w("call_site.py",
  "PWD = _load_pwd()\n"
  "token = _load_token()\n"
  "password = _password(\"加密私钥口令：\")\n")
# 第7轮调优回归（2026-10-19）：PascalCase 长标识符不命中（含真实误报形态：
# 类名内含 "Authorization" 子串会误触 CRED_CONTEXT 放宽分支，必须一并覆盖）
w("pascal_case.py",
  "class PortfolioAuthorizationRequest(BaseModel):\n"
  "    ) -> PortfolioAuthorizationRequest:\n"
  "        raise PortfolioAuthorizationError(\"request 必须是 PortfolioAuthorizationRequest\")\n"
  "    \"PortfolioAuthorizationRequest\", \"PortfolioAuthorizationError\",\n")
# 第7轮阳性对照：随机 32 位字母数字 token 仍必须命中（防漏报回退）
tok32c = "".join(rng.choices(alpha, k=32))
w("pascal_positive.py", "TUSHARE_TOKEN = '%s'\n" % tok32c)
# 2026-11-01 SCANFIX：JSON 字符串 id 值（策略 id）不是凭据，不得误报 HIGH_ENTROPY
w("json_id.json", '{"id": "S03_H18_forecast_revision_watch"}\n')

scanner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_secrets.py")
proc = subprocess.run([sys.executable, scanner, TD], capture_output=True, timeout=120)
out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")

results = []


def check(name, cond):
    results.append((name, bool(cond)))


check("退出码=1(有未豁免命中)", proc.returncode == 1)
check("config.py 命中 TOKEN_ASSIGN", "config.py:1:TOKEN_ASSIGN" in out)
check("config.py 命中 HIGH_ENTROPY", "config.py:1:HIGH_ENTROPY" in out)
check("config.py 命中 PASSWORD_LITERAL", "config.py:2:PASSWORD_LITERAL" in out)
check("notes.md 命中 PASSWORD_LITERAL", "docs/notes.md:1:PASSWORD_LITERAL" in out)
check("notes.md 命中 INTRANET_IP_WITH_CREDS", "docs/notes.md:1:INTRANET_IP_WITH_CREDS" in out)
check("notes.md 命中 PHONE_CN", "docs/notes.md:2:PHONE_CN" in out)
check("notes.md 的40hex(commit sha)未误报", "notes.md:3" not in out)
check("leaked.pem 命中 PRIVATE_KEY", "leaked.pem:1:PRIVATE_KEY" in out)
check(".env.local 进入路径豁免清单", ".env.local" in out and "EXEMPT-PATH" in out)
check("tests桩命中全部标记豁免", "tests/test_stub.py" in out and "EXEMPT-TEST" in out)
check("clean.txt 无命中", "clean.txt" not in out)
check("占位符文件无命中", "placeholder_cfg.py" not in out)
# 第6轮调优回归断言
check("第6轮:插值报错串行(:?行)不命中", "interp_cfg.yml:1" not in out and "interp_cfg.yml:2" not in out)
check("第6轮:函数调用文件无命中", "call_site.py" not in out)
check("第6轮:compose默认真实口令仍被提取", "interp_cfg.yml:3" in out and (pwd2[:4] + "****") in out)
check("同行混合线:HIGH_ENTROPY 命中", "mixed_line.py:1:HIGH_ENTROPY" in out)
check("同行混合线:PASSWORD_LITERAL 也命中", "mixed_line.py:1:PASSWORD_LITERAL" in out)
check("同行混合线:密码预览已打码", (pwd2[:4] + "****") in out)
# 诊断（只输出布尔）：
he_line = [l for l in out.splitlines() if "mixed_line.py:1:HIGH_ENTROPY" in l]
pl_line = [l for l in out.splitlines() if "mixed_line.py:1:PASSWORD_LITERAL" in l]
check("诊断:HE预览含pwd2掩码", bool(he_line) and (pwd2[:4] + "****") in he_line[0])
check("诊断:PL预览存在", bool(pl_line))
check("诊断:HE预览存在tok掩码", bool(he_line) and (tok32b[:4] + "****") in he_line[0])
check("compose默认口令被提取命中", "compose_like.yml" in out and (pwd2[:4] + "****") in out)
for name, secret in [("tok32b", tok32b), ("pwd2", pwd2)]:
    check("完整%s未回显(混合行)" % name, secret not in out)
# 红线：完整假密钥绝不出现在任何输出
for name, secret in [("tok32", tok32), ("pwd", pwd), ("lit", lit), ("fake_tok", fake_tok)]:
    check("完整%s未回显" % name, secret not in out)
# 第7轮调优回归断言
check("第7轮:PascalCase类名文件无命中", "pascal_case.py" not in out)
check("第7轮:随机32位token仍命中HIGH_ENTROPY(阳性对照)",
      "pascal_positive.py:1:HIGH_ENTROPY" in out)
check("第7轮:阳性对照token按前4后2脱敏", tok32c[:4] + "****" + tok32c[-2:] in out)
check("第7轮:完整阳性token未回显", tok32c not in out)
check("JSON id 值不误报 HIGH_ENTROPY", "json_id.json" not in out)
# 脱敏形态：前4后2存在
check("tok32 已按前4后2脱敏", tok32[:4] + "****" + tok32[-2:] in out)
check("pwd 已脱敏", pwd[:4] + "****" in out)
# 输出中不应有超长未打码串（粗检：连续40位字母数字不应出现）
check("输出无40位连续未打码串", not any(
    seg in out for seg in [tok32, fake_tok]))

fails = [n for n, ok in results if not ok]
for n, ok in results:
    print(("PASS " if ok else "FAIL ") + n)
print("\n汇总: %d/%d 通过" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)


# ── 夹具口径说明（2026-10-31 WO-SCANST-SELFTEST-20261031）─────────────────
# ① config.py 的 password 必须加引号：.py 中未加引号的纯字母数字值会被扫描器
#    SCANFIX④ 判为标识符引用（防 password=password 类误报的既定规则）而豁免；
#    随机 pwd 可能恰好纯字母数字，旧夹具 `password: <pwd>` 因此偶发漏检
#    （selftest 34/36 两项失败的根因；本修复只改夹具，不削弱扫描规则）。
# ② 本文件 fixtures 区行号被 tools/scan_secrets_allowlist.tsv 以 path:line:rule
#    精确引用（当前第 32 行 PHONE_CN / INTRANET_IP_WITH_CREDS）。在 fixture 区
#    之前增删行会让 allowlist 失配、merge 明文凭据门报“新增命中”——改动须同步
#    更新 allowlist 行号（或在 fixture 区之后添加说明）。
