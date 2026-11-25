# Add to stack — saved shares and Wi-Fi delivery

Safari and Files can share one web URL or PDF to **Add to stack**. **Add to stack**
first commits the share on the iPhone. A URL is a bookmark; its contents are fetched
by the Mac later. A shared PDF is copied to the phone's persistent App Group container.
Saving works on cellular, offline, and before pairing. The share sheet shows the
article, a destination dropdown (existing stacks or **Create new stack**), a new-stack
name field when needed, **Add to stack**, and Cancel, then a saved-article confirmation. Open
Reading Stacks to go directly to the latest saved article. New-stack creation and
article insertion happen together on the Mac and are safe to retry. Restart the Mac
backend after installing this update to enable new-stack requests.
Text-only PDFs are supported; OCR is not included.

iOS does not provide a supported way for a Share Extension to launch its containing
app automatically. The saved item is remembered across app/phone restarts and shown
when the user next opens or returns to Reading Stacks. Once shown in the app, it does
not interrupt subsequent app opens.

## Install and pair

1. Install dependencies (`uv sync` and `cd mobile && npm ci`).
2. Run `make device` (or `make device-dev`). The iOS build refreshes the share
   extension and queue module through the Expo config plugin. Changes to either
   require rebuilding the iPhone app.
3. Both **ReadingStacks** and **AddToStack** must be signed by the same team, with
   App Group `group.com.articlestacks.mobile.sharing` enabled in their provisioning
   profiles. The plugin configures entitlements and Xcode capabilities; the versioned
   Expo CLI patch lets device builds refresh existing signing profiles automatically. If the bundle identifier changes,
   the plugin also changes the group identifier in both source copies.
4. Start the Mac backend and open Reading Stacks on both devices on the same Wi-Fi.
   Allow Local Network access on the phone. It discovers your Mac automatically;
   if several Macs are running Reading Stacks, choose yours in Saved shares.
5. The phone displays a six-digit code. Approve the matching connection request
   in the Mac web app once. The phone saves the connection and sends pending shares
   automatically. Keep the Mac awake while sending.

Bonjour advertises `_readingstacks._tcp` whenever the backend runs, including before
pairing. Only the service identity and address are advertised. Stack access and uploads
still require a saved credential. Pairing requests expire after five minutes, require
approval through the Mac's loopback-only UI, and never grant access just because a
phone is on the same network. Decline unrecognized requests. Disconnect revokes the
saved credential; reconnecting requires approval again.

Pairing survives backend restarts. The phone stores credentials in the App Group
Keychain and uses the discovered machine identity to refresh the address when it
changes. Previously pasted connection links continue to work. Manual connection
remains available under an advanced disclosure for networks that block Bonjour.
`ARTICLE_SHARE_PORT` defaults to 8767; `ARTICLE_DISCOVERY=0` disables advertisement.
Guest Wi-Fi isolation, VPN routing, denied Local Network access, or a sleeping Mac
can prevent discovery. Local transport remains bearer-authenticated HTTP on trusted
Wi-Fi. No cloud service is involved.

Native discovery requires installing a rebuilt iPhone app with `make device`.

## Queue behavior

- Sync on app foreground, Wi-Fi availability while active, every 15 seconds while
  active, and **Send now**. Choose the destination inside the app after capture.
  Requests prohibit cellular access. Background/closed-app syncing is not promised.
- **Waiting for Mac**, **Processing on Mac / awaiting confirmation**, **Added to stack**,
  and **Needs attention** distinguish local storage from successful delivery.
- Cached stacks work offline in the app. Choose later, retry a failed item,
  or change a known failed item's destination. An uncertain/in-flight item cannot be
  redirected or deleted until its receipt is reconciled, avoiding duplicate delivery.
- The Mac stores a receipt and payload before acknowledging acceptance, then ingests
  on a separate worker. Receipts survive restarts; interrupted work is recovered.
  Appending to the stack and marking success use one SQLite transaction.
- Stable request IDs reject different payloads and make retries safe. Exact URL
  duplicates and identical PDF bytes are deduplicated within the destination stack.
- PDF copies remain on the phone until completion or explicit deletion. Completed
  history can be cleared. Failed Mac payloads are released; retry resends the phone copy.
- Limits: 20 MB per PDF, 200 MB of queued phone PDFs, 500 local entries, 300 MB pending
  Mac payloads. Full storage reports an error and does not discard unsent shares.
- Saving adds source text to a stack. Generate audio separately as usual.

## PDF extraction

`backend/pdf.py` is used by uploaded PDFs, URL ingestion and the sharing inbox.
PDF detection uses response headers and signature, including extensionless arXiv URLs.
`pypdf` reads metadata and validates document structure; `pdfminer.six` extracts
text with word spacing across font changes and layout-based reading order.
Extraction runs in a child process with a 60-second deadline; limits include
500 pages and 2 million text characters. Encrypted and image-only documents return
readable errors. Mixed image/text PDFs extract their text layer only. Scientific
notation, tables, hyphenated line breaks and complex layouts may still need editing.

## Verification

Run `make check`. With a booted iOS simulator, run
`npm --prefix mobile run test:share-queue` for native storage tests (durable PDF
copy, size rejection, exclusive syncing and concurrent writers across processes). Focused integration tests are in `tests/test_pdf_queue.py` and
`tests/test_share.py`: PDF upload/link extraction, malformed/scanned/encrypted PDFs,
queued upload, duplicate receipt, changed-payload rejection, error retry, restart
recovery, persistent pairing and revocation. Native source changes also require an
Xcode app/extension build and physical-device checks.

Physical acceptance:

1. Share a webpage and Safari PDF on 5G; verify both appear in Saved shares.
2. Force-close/reopen the app and reboot the phone: both remain queued.
3. Return to home Wi-Fi, start the Mac and open Reading Stacks: each arrives once.
4. Interrupt upload/response delivery and restart the Mac; retry without duplicates.
5. Share before pairing or with no stacks; choose a destination later.
6. Delete a destination on Mac; resolve the failed item by choosing another stack.
7. Revoke pairing, reconnect, and verify pending items remain intact.
8. Test scanned, encrypted and oversized PDFs; confirm clear errors.

Verified on 18 November 2026: 45 backend tests, mobile TypeScript and parser tests,
web production build, native storage/activation/concurrency tests, and the full iOS
Release simulator build. The app launched and created its App Group queue through
the React Native bridge. The supplied arXiv paper extracted in about three seconds,
including title/author metadata and readable word spacing. Later on 18 November,
the signed Release build succeeded with refreshed App Group provisioning profiles,
was installed on the physical iPhone 16 Pro, and launched successfully. End-to-end
Safari capture and Wi-Fi delivery on the physical phone still require acceptance checks.

## iCloud alternative

Saved shares can also use a user-selected iCloud Drive folder instead of the LAN
connection. See [iCloud setup and protocol](ICLOUD_SYNC.md). Folder selection and
sync happen in the containing app; the extension continues saving locally first.
