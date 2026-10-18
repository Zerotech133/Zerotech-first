#!/usr/bin/env bash
# 轻量 i18n 硬编码检查：检测「已迁移」文件里残留的 JSX 中文字面量（排除注释）。
# 迁移一个文件就把它加入 MIGRATED，CI/pre-commit 跑此脚本守住不回退。
# 注：项目暂无 ESLint 基础；完整方案(eslint-plugin-i18next no-literal-string)列为后续。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../frontend" && pwd)"

# 已完成 i18n 迁移的文件（随铺开逐步追加）。语言名常量文件(LanguageMenu)含'中文'属特例，不列入。
MIGRATED="src/pages/LoginPage.tsx"

fail=0
for f in $MIGRATED; do
  # 去掉 // 行注释后再查中文；命中即视为未走 t() 的硬编码
  hits=$(sed -E 's#//.*$##' "$f" | grep -nE '[一-鿿]' || true)
  if [ -n "$hits" ]; then
    echo "[i18n] $f 残留中文字面量(应走 t())："
    echo "$hits"
    fail=1
  fi
done

if [ $fail -eq 0 ]; then
  echo "i18n hardcode check OK: $MIGRATED"
fi
exit $fail
