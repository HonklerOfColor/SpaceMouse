#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

python3 fusion/test_motion.py

mkdir -p bin
swiftc -O -framework IOKit -framework CoreFoundation -framework Foundation \
  -framework CoreGraphics -framework CoreText \
  -o bin/sp1hid sp1hid/main.swift sp1hid/lcd.swift
codesign -s - --force bin/sp1hid

ADDIN="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/SpacePilot"
mkdir -p "$ADDIN"
cp fusion/SpacePilot.py fusion/SpacePilot.manifest fusion/index.html fusion/motion.py "$ADDIN/"
cp bin/sp1hid "$ADDIN/sp1hid"
chmod +x "$ADDIN/sp1hid"
echo "installiert: $ADDIN"
