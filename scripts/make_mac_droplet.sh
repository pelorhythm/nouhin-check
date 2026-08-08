#!/bin/bash
# デスクトップに「納品チェック.app」ドロップレットを作る（Mac用・再ビルドもこれ一発）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$HOME/Desktop/納品チェック.app"
rm -rf "$OUT"
osacompile -o "$OUT" "$DIR/droplet.applescript"
echo "作成しました: $OUT"
