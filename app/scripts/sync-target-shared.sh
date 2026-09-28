#!/usr/bin/env bash
# 分享和小组件两个扩展各带一份 MousseShared.swift（apple-targets 的 _shared 目录会连主 app 一起编，不用它）。
# 以 targets/share 那份为准，拷到 targets/widgets；--check 只比较，不一样就退出 1。
set -euo pipefail
cd "$(dirname "$0")/../targets"
if [[ "${1:-}" == "--check" ]]; then
  cmp -s share/MousseShared.swift widgets/MousseShared.swift || { echo "targets/*/MousseShared.swift 不一样：跑 scripts/sync-target-shared.sh"; exit 1; }
  exit 0
fi
cp share/MousseShared.swift widgets/MousseShared.swift
echo "synced"
