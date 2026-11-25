# Reading Stacks mobile

React Native / Expo application for iOS and Android. Recordings are generated on
the Mac, transferred over Wi-Fi or imported as files, and played offline.

## Run

The root Makefile provides the usual workflow:

```sh
make setup-mobile
make simulator                         # Builds/installs and opens simulator; starts Mac app + Metro
make simulator SIMULATOR="iPhone 17 Pro"
make device                            # Connected iPhone, standalone Release build; signing required
make device-dev                        # Connected iPhone, development build with Fast Refresh
make status
make stop                              # Stops Make-managed Mac app and Metro
```

Run these from the repository root. Use `make expo` for Metro alone, or
`make simulator-open` for an interactive Expo session with an already-installed
simulator build (Ctrl-C to stop that session). `make stop` does not shut down the
Simulator or stop services launched from other terminals. Logs are in
`.data/services/`. For physical installation, connect/unlock/trust the iPhone,
configure Xcode signing, and enable Developer Mode. Optional `DEVICE="name or UDID"`
selects the phone. Release installs do not need Metro after installation.

### Automatic updates while developing on your iPhone

Connect and unlock your iPhone, then run `make device-dev` from the repository
root (optionally with `DEVICE="name or UDID"`). This starts Metro and installs a
Debug development build. Keep your Mac and phone on the same Wi-Fi and allow
local-network access. If the development launcher appears, select this project's
Metro server. Saved changes to React components, wording, and styles refresh
automatically while the app is connected; some changes reload the entire app.

For later sessions, run `make expo` and open the development app; a native rebuild
isn't needed for JavaScript/TypeScript edits. If edits don't appear, reconnect to
the Metro server and check that Fast Refresh is enabled in the developer menu
(shake the phone). `make stop-expo` stops live updates. Native dependencies,
permissions, and Swift share-extension changes still require `make device-dev`
again. Use `make device` when you want a standalone Release install instead;
that install doesn't watch source changes. Both builds use the same app ID, so
installing one replaces the other.

Use Node 22.13+ (Node 24 recommended), npm, and the platform's native toolchain.
Dependencies and the lockfile are included:

```sh
cd mobile
npm ci
npm run ios       # Xcode, iOS simulator and CocoaPods required
npm run android   # Android SDK, JDK and emulator/device required
```

These commands generate the native projects and install a development build.
For an existing development build, use `npm start`. Use development builds rather
than Expo Go to verify background audio, native permissions, and deep links.
Native directories are generated from app.json and intentionally ignored.
The iOS launcher checks CocoaPods and falls back to an existing Homebrew Ruby
gem executable if the default `pod` launcher is broken. If neither installation
works, install CocoaPods for your active Ruby before retrying.

`npm ci` applies the versioned `patch-package` fix for `expo-modules-jsi@58.0.21`.
It addresses the Swift pointer-capture compilation failure on Xcode 26.2
([Expo issue #52754](https://github.com/expo/expo/issues/52754)) while preserving
the synchronous JavaScript-thread execution and pointer lifetime. Review/remove
the patch when upgrading Expo to a release with the upstream fix.
`npm ci` also applies the `@expo/cli@58.1.18` signing fix: device builds allow
Xcode to refresh provisioning profiles even when a development team is already
configured. This is required after adding the shared queue's App Groups capability.
Both targets declare that capability through the config plugin. Review/remove the
patch when upgrading to an Expo CLI version that handles existing-team profile updates.

Set your own bundle/package identifiers before store distribution. Store signing,
TestFlight / Play distribution and cloud hosting are not configured.

## Send from the web app

1. Start the Mac app normally with `./scripts/start.sh` from the repository root.
2. Generate a recording and click **Send to Phone** beside it.
3. Connect the phone to the same Wi-Fi network, open the mobile app, and scan
   the code (or paste the transfer link and tap **Download stack**).
4. Keep the Mac awake until the mobile app says **Saved for offline listening**.
   The web app displays **Downloaded on Phone** after receiving the phone's receipt.

The Mac starts a separate transfer listener on port 8766 when Send to Phone is
used. Its random capability links expose only the selected immutable recording
and snapshot, expire after one hour, and can be revoked in the web UI. The editor
continues listening only on loopback. Transfers stop when the backend stops.
The phone does not need connectivity after a download is saved.

Local transfers use HTTP, so use a trusted Wi-Fi network. Anyone with the link
can download that recording until it expires or is revoked. The application has
cleartext/local-network permission configured for this transport. Cloud delivery
would need a separate authenticated HTTPS inbox; no cloud provider is required
or included in this version. No automatic delivery while the app is closed is
promised: scan/open the link in the app to initiate a download.

If the generated address is wrong (VPN, multiple interfaces), start the backend
with `ARTICLE_TRANSFER_HOST=192.168.x.x` set to your Mac's LAN address.
`ARTICLE_TRANSFER_PORT` overrides 8766. Allow the Python process through the Mac
firewall when asked. Guest Wi-Fi isolation can prevent transfers.

Downloads use native file transfer and verify SHA-256 and size before committing
the recording to SQLite. Failed downloads can be retried using the same link;
retries currently restart the file. Duplicates keep existing playback progress.
New generations are separate recordings; an old version is never silently replaced.

## File import and player

**Export Stack** downloads a `.stack` ZIP containing `manifest.json` and `audio.mp3`.
Move it using Files, AirDrop, Drive, USB, or another file transfer, then choose
**Import file** inside the app. The app also accepts standalone MP3s, reading basic
ID3v2.3/v2.4 title tags and falling back to the filename. The OS decodes the MP3;
the app doesn't transcribe audio to recover text. Audio file associations are not configured; select audio files with the in-app picker.
The Add to Stack share extension accepts article URLs and PDFs for delivery to your Mac.

The player supports offline playback, saved position, speed, skip controls,
lock-screen/headphone controls, article seeking, and source text. Recordings
offer a labeled Playback Speed selector (0.75×, 1×, 1.25×, 1.5×, 1.75×, 2×).
Your selected speed is remembered across recordings and app launches. Recordings
generated before this feature have no chapter markers; regenerate them to add
markers. New markers use cumulative chunk WAV durations, including headings and
pauses. MP3 encoder padding can introduce a small offset; the encoding test
checks total duration within 150 ms. Chapters are carried in the manifest, not
embedded ID3 CHAP frames.

Imports are limited to 150 MB of audio (about 2 h 40 min at 128 kbps). ZIP imports
and checksum validation currently buffer audio in memory, so very large files
should be split into smaller stacks. Archives only extract the two expected
entries and validate declared sizes and chapter bounds.

## Verification

```sh
npm run typecheck
npm test
npx expo export --platform ios --platform android
```

Device acceptance checks before release: transfer an actual generated stack to
both platforms, enable airplane mode, play with the screen locked, use Bluetooth
controls, interrupt playback with a call, reopen to resume, seek each chapter,
interrupt/retry a transfer, reject a corrupted export, import an older recording,
and download a regenerated version without losing the old version's position.

On 3 November 2026, the iOS Debug build succeeded on Xcode 26.2 and ran on the
iPhone 17 Pro simulator (iOS 26.2). A real Mac recording was downloaded through
the transfer deep link, verified and saved in the library, played to completion,
and reopened at its saved position. The source-text reader was also checked.
TypeScript and all three parser tests passed. Physical-device background audio,
Bluetooth/call interruptions, camera scanning, and Android native execution
still require device acceptance checks.

The custom URL scheme `articlestacks://receive?url=<encoded-transfer-link>` opens
the receiver. QR codes contain plain transfer links for scanning inside the app.

## Add article URLs from iOS

The native **Add to Stack** Share Extension saves URLs and PDFs on your iPhone, including offline. Open the app on home Wi-Fi: it finds your Mac automatically. Approve the matching code in the Mac app once; later deliveries reconnect automatically. The numbered **shared articles · send to Mac** button opens saved shares (shown when saved shares exist). See [iPhone sharing setup and testing](SHARE_EXTENSION.md) for installation, pairing, and limitations.
