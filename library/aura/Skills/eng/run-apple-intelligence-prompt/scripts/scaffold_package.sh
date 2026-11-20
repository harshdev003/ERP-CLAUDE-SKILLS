#!/usr/bin/env bash
# scaffold_package.sh — create the ApplePromptTester Swift package if absent.
#
# Usage: scaffold_package.sh <package-dir>
#
# Idempotent: existing files are left in place. Run safely on a partial scaffold.

set -euo pipefail

PKG_DIR="${1:-}"
if [ -z "$PKG_DIR" ]; then
  echo "usage: scaffold_package.sh <package-dir>" >&2
  exit 2
fi

ASSETS="$(cd "$(dirname "$0")/.." && pwd)/assets"

mkdir -p "$PKG_DIR/Sources/ApplePromptTester" "$PKG_DIR/prompts" "$PKG_DIR/results" "$PKG_DIR/fixtures"

if [ ! -f "$PKG_DIR/Package.swift" ]; then
  cp "$ASSETS/Package.swift.template" "$PKG_DIR/Package.swift"
  echo "wrote $PKG_DIR/Package.swift"
fi

if [ ! -f "$PKG_DIR/Sources/ApplePromptTester/main.swift" ]; then
  cp "$ASSETS/main.swift.template" "$PKG_DIR/Sources/ApplePromptTester/main.swift"
  echo "wrote $PKG_DIR/Sources/ApplePromptTester/main.swift"
fi

if [ ! -f "$PKG_DIR/.gitignore" ]; then
  printf "results/\n.build/\n" > "$PKG_DIR/.gitignore"
  echo "wrote $PKG_DIR/.gitignore"
fi

if [ ! -d "$PKG_DIR/.git" ]; then
  (cd "$PKG_DIR" && git init -q)
  echo "git init $PKG_DIR"
fi

echo "scaffold complete: $PKG_DIR"
