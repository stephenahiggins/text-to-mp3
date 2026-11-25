#!/usr/bin/env bash
# Requires a booted iOS simulator; tests the production Swift storage implementation.
set -euo pipefail
cd "$(dirname "$0")/.."
work="$(mktemp -d /tmp/reading-queue-tests.XXXXXX)"
trap 'rm -rf "$work"' EXIT
arch="$(uname -m)"
if [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then arch=arm64; fi
xcrun --sdk iphonesimulator swiftc -D QUEUE_STORAGE_TESTS \
  -sdk "$(xcrun --sdk iphonesimulator --show-sdk-path)" \
  -target "${arch}-apple-ios16.4-simulator" \
  share-extension/ShareQueue.swift share-extension/CloudStore.swift tests/QueueStorageTests.swift -o "$work/test"
xcrun simctl spawn booted "$work/test" "$work/store" "$PWD/share-extension/Info.plist" init
pids=()
for i in 1 2 3 4; do
  xcrun simctl spawn booted "$work/test" "$work/store" append &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid"; done
xcrun simctl spawn booted "$work/test" "$work/store" verify
