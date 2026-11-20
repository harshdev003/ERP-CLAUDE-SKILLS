#!/usr/bin/env bash
# scaffold_ios_sources.sh — write the ApplePromptTesterIOS Swift sources.
#
# Usage: scaffold_ios_sources.sh <project-dir>
#
# Idempotent: existing files are left in place. Does not create the
# `.xcodeproj` — that requires a one-time manual step in Xcode (see
# references/setup-xcode-project.md).

set -euo pipefail

PROJ_DIR="${1:-}"
if [ -z "$PROJ_DIR" ]; then
  echo "usage: scaffold_ios_sources.sh <project-dir>" >&2
  exit 2
fi

ASSETS="$(cd "$(dirname "$0")/.." && pwd)/assets"
SRC_DIR="$PROJ_DIR/ApplePromptTesterIOS"

mkdir -p "$SRC_DIR"

if [ ! -f "$SRC_DIR/ApplePromptTesterIOSApp.swift" ]; then
  cp "$ASSETS/ApplePromptTesterIOSApp.swift.template" "$SRC_DIR/ApplePromptTesterIOSApp.swift"
  echo "wrote $SRC_DIR/ApplePromptTesterIOSApp.swift"
fi

if [ ! -f "$SRC_DIR/ContentView.swift" ]; then
  cp "$ASSETS/ContentView.swift.template" "$SRC_DIR/ContentView.swift"
  echo "wrote $SRC_DIR/ContentView.swift"
fi

if [ ! -f "$PROJ_DIR/.gitignore" ]; then
  cat > "$PROJ_DIR/.gitignore" <<'EOF'
build/
DerivedData/
*.xcuserstate
xcuserdata/
EOF
  echo "wrote $PROJ_DIR/.gitignore"
fi

echo "scaffold complete: $PROJ_DIR"
echo
if [ ! -d "$PROJ_DIR/ApplePromptTesterIOS.xcodeproj" ]; then
  cat <<'EOF'
NEXT: ApplePromptTesterIOS.xcodeproj does not exist yet.
Open Xcode 26 once to create it (signing team, deployment target iOS 26,
add the two .swift files above to the project). Full walkthrough:
  references/setup-xcode-project.md
EOF
fi
