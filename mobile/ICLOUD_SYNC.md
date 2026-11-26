# Personal iCloud Drive sync

Reading Stacks can send URLs and PDFs to your Mac through your own iCloud Drive,
then return completed `.stack` recordings to the iPhone's offline library. The Mac
can be asleep or disconnected when you submit a share. It processes requests once
it is awake, online, and running Reading Stacks. Open the iPhone app to send saved
shares and collect receipts and completed audio; continuous syncing while the app
is closed is not promised. Apple controls upload/download timing and cellular-data
policy, so saving a request locally is not confirmation that the Mac received it.

## Set up once

1. Enable iCloud Drive on both devices, signed in to the same Apple Account.
2. Start the Mac app and open **Share by iCloud**.
3. Click **Choose iCloud folder**. In the native picker, create/select a new, empty
   folder called **Reading Stacks** inside iCloud Drive. macOS may ask permission
   for the terminal/Python process to access iCloud Drive. Allow it for this folder.
4. On the iPhone, open **Mac connection** (or the numbered shared-articles button),
   then **iCloud Drive → Choose iCloud folder**. Select the same folder using Files.
   If it has not appeared yet, let iCloud finish syncing and try again.
5. Save an article or PDF with **Add to stack**, choose a stack, and open the app.
   **Send now** also initiates a sync. No pasted link, Wi-Fi pairing, or VPN is needed
   when iCloud mode is selected.

The Mac's **Generate audio automatically for incoming stacks** option is enabled
on initial setup. It uses George at speed 1, without optional AI briefings. Turn it
off to review/edit incoming documents and generate audio manually. Existing failed
or cancelled audio jobs are not automatically retried; resolve them in the Mac
editor. The usual reading-mode rules still apply: documents explicitly set to
Briefing Only require a briefing.

Completed recordings from the Mac library are published to the chosen folder.
When the phone app is active, it downloads new recordings, validates the archive,
size and SHA-256, and adds them to **Library**. Existing playback progress is kept.
Removing a recording from the phone does not automatically import it again.

## Privacy and storage

Each person selects a folder in their own iCloud account. Do not share this folder
with other people: its contents include submitted URLs, PDFs, source text and audio,
and files added to its inbox are treated as authorized import requests. No Apple
password, API key, LAN pairing token, SQLite database or absolute Mac output path
is written into the synced folder. Optional AI briefings still use the Mac user's
own OpenAI key and the existing briefing settings.

A folder has one designated processing Mac. Another checkout/Mac cannot claim an
existing folder silently; choose a separate empty folder for another processor.
Multiple phones on the same account can use that processor with distinct request
IDs. Phone folder access is remembered using a security-scoped bookmark. If access
expires, select the same folder again; pending requests keep their IDs.

Request files are removed after the phone receives successful confirmation. Failed
requests retain their source for explicit retry. Receipts and completed audio are
retained in iCloud; they use your iCloud storage allowance. Disconnecting sync on
either device does not delete cloud files, local shares, or offline audio. Wait for
pending shares to finish before switching to a different folder or Wi-Fi mode.
Old exported recordings can be removed manually from Files when no longer needed;
current recordings may be republished by the Mac. The phone retains locally imported
audio independently of iCloud.

## Protocol and reliability

The selected folder contains:

- `reading-stacks.json`: schema version, workspace ID and designated Mac ID.
- `requests/<UUID>.json`: bounded URL/PDF payload, workspace ID and retry attempt.
- `receipts/<UUID>.json`: matching attempt, processing/complete/error status.
- `catalog.json`: available stack names/IDs and completed recording filenames.
- `recordings/<job-id>.stack`: immutable verified audio/source export.

The existing durable Mac inbox handles both LAN and cloud requests, deduplicating
by request ID and payload fingerprint. Source insertion and success receipts commit
together. Polling an error never automatically resubmits it. Explicit retries
increment the attempt; the phone ignores stale receipts from earlier attempts.
Submitted items cannot be redirected until the Mac reports a known failure.

Native file coordination serializes file-provider reads/writes, requests downloads,
and atomically publishes files. The Mac compiles `scripts/cloud-files.swift` into
its ignored `.data/tools/` directory on first use; Xcode command-line tools are
required. Partial temporary files are not inbox requests. Per-file limits remain
20 MB for PDFs and 150 MB for portable recordings. Validation is shared with the
LAN service, including public-URL restrictions and bounded inbox storage.

## Building and sharing the app

This sync feature uses a user-selected folder through Apple's document picker;
it does not require a developer-owned CloudKit database or a shared service account.
Each user grants access to their own folder. The existing Share Extension still
requires App Groups and correctly provisioned signing profiles for both targets.

For a fresh checkout:

1. Follow the root README's Mac setup and `make setup-mobile`.
2. Copy `mobile/.env.example` to `mobile/.env.local`.
3. Set `APPLE_TEAM_ID` to your own signing team and `APP_BUNDLE_IDENTIFIER` to a
   unique identifier, for example `com.yourname.readingstacks`. Set
   `ANDROID_PACKAGE` only if it should differ. These local settings are ignored
   by Git. No personal signing team is hard-coded in the tracked app config.
4. Sign in to Xcode with your developer account and run `make device`. The Expo
   config plugin derives the extension ID and shared App Group from your app ID
   and refreshes the copied native sources on each build. Enable App Groups in
   provisioning for both targets. An account supporting these capabilities is
   required; do not assume a free Personal Team supports the full app.
5. Follow the folder setup above with your own Mac and iCloud account.

Changing app IDs creates a different iOS app and App Group; it does not migrate an
existing installation's library, queue or pairing. Keep existing identifiers when
updating an installed app. Distributed TestFlight/App Store users use the publisher's
signed app and do not need Xcode or their own signing configuration. Publishing and
Apple review are separate steps; this repository does not submit a release.

## Verification

`make check` covers the cloud inbox, restart/retry behavior, changed-payload and
wrong-workspace rejection, export idempotency, signing config, and existing app
features. `npm --prefix mobile run test:share-queue` (with a booted simulator) tests
coordinated native writes and stale receipts using the production exchange code.

Physical acceptance requires two devices signed into iCloud: choose the folder on
both, submit on cellular while the Mac is asleep, wake it and verify exactly one
import, then reopen the phone app and play the returned audio offline. Also check
iCloud storage exhaustion, revoked folder access, delayed receipts and reconnecting
after restarting both apps. Local filesystem tests cannot prove Apple's cloud
upload/download timing or account configuration.
