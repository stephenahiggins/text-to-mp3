#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Older macOS CocoaPods launchers can point at a Ruby without the pod gem.
# Prefer the existing working installation; never change system Ruby or gems.
if ! pod --version >/dev/null 2>&1; then
  for ruby in /opt/homebrew/opt/ruby/bin/ruby /usr/local/opt/ruby/bin/ruby; do
    [[ -x "$ruby" ]] || continue
    gem_bin="$("$ruby" -rrubygems -e 'puts Gem.bindir')"
    if [[ -x "$gem_bin/pod" ]] && "$gem_bin/pod" --version >/dev/null 2>&1; then
      export PATH="$gem_bin:$(dirname "$ruby"):$PATH"
      break
    fi
  done
fi
if ! pod --version >/dev/null 2>&1; then
  echo 'CocoaPods is unavailable. Install CocoaPods for your active Ruby, then retry npm run ios.' >&2
  exit 1
fi

# Expo only prebuilds automatically when ios/ is missing. Refresh the native
# sources copied by the share extension plugin before reusing an Xcode project.
if [[ -d ios ]]; then
  npx expo prebuild --platform ios --no-clean --no-install
fi

exec npx expo run:ios "$@"
