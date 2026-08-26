#!/bin/bash
# 「納品チェック.app」ドロップレットをビルドする（Mac用）
# リポジトリ内 mac/ に格納（配布用）し、デスクトップにもコピー（普段使い用）する
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"
APP="$REPO/mac/納品チェック.app"
DESKTOP="$HOME/Desktop/納品チェック.app"

mkdir -p "$REPO/mac"
rm -rf "$APP"
osacompile -o "$APP" "$DIR/droplet.applescript"
rm -rf "$DESKTOP"
cp -R "$APP" "$DESKTOP"
echo "作成しました: $APP"
echo "コピーしました: $DESKTOP"
