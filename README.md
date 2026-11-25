# Reading Stacks

A local article-to-MP3 application for Apple Silicon macOS. Paste a public Substack/article URL, paste text, or import HTML, Markdown or TXT. Review and edit the narration, select a British narrator, then generate a tagged MP3 for your iPhone.

## Stacks

Click New Stack and rename its title inline, then add public URLs, pasted text, or HTML, Markdown, TXT, text-based PDF and Word (.docx) files. Multiple file imports are supported. Scans and images need narration text supplied through Paste Text. Review and edit documents, reorder them, or remove them. Additions and ordering are saved immediately; document edits use Save Changes. Saved Stacks survive browser and backend restarts. Drag Stack cards to reorder them, or use Alt + Arrow keys while a card is focused. Cards show creation dates and audio status: green for current audio, amber for changes since generation, and black when no audio exists. Completed audio shows its last generation time.

Choose Whole Article or Briefing Only for each document. Estimated listening time includes introductions, pauses and the selected speed; unprepared briefings use a 50-word allowance. Briefing Only always requires a summary, even with Include Briefings switched off. Choose a voice and speed, then Create Audio to produce one MP3 in document order, beginning with the Stack title, then each document’s title and date (when available), with pauses between documents. The job captures an immutable snapshot, so you can keep adding to the Stack during generation. A successful matching version marks the Stack Audiofied. Subsequent edits mark the audio as needing an update; Regenerate Audio replaces the previous recording after successful generation while reusing cached chunks. Only the latest recording is retained; existing older recordings are removed on startup. Existing conversions migrate automatically to single-document Stacks.

## Start

From the repository root, `make help` lists the supported commands.

```sh
make setup          # First run: Mac dependencies, model weights and web build
make setup-mobile   # First run: Expo dependencies and the native build patch
make start          # Build and run the local web app in the background
make simulator      # Start local app + Expo; build/install/open the iOS simulator app
make status         # Check which services are running
make stop           # Stop the local app and Expo started by Make
```

Open http://127.0.0.1:8765 for the Mac app. `make simulator` accepts
`SIMULATOR="iPhone 17 Pro"` to select a simulator. The first native build can take
several minutes. Xcode and an installed iOS simulator runtime are required.
`make expo` starts only Metro; `make stop-expo` stops only Metro. For an already
installed simulator build, `make simulator-open` runs an interactive Expo session;
stop that foreground session with Ctrl-C rather than `make stop`.

To install on a connected iPhone, use `make device` (or `make device DEVICE="name or UDID"`).
This creates a Release build with bundled JavaScript, so playback does not depend
on Metro. Xcode signing with your Apple Account, device trust and Developer Mode
must be configured. This command installs locally; it does not publish to TestFlight
or the App Store. See [mobile setup and transfer instructions](mobile/README.md).

Managed service logs and PID records live in `.data/services/`. `make stop` only
stops processes launched by these commands and leaves the Simulator app open.
If a port is already occupied by an older `./scripts/start.sh` or Expo session,
stop it with Ctrl-C in its original terminal first. Only run one backend against
a data directory. The original `./scripts/start.sh` remains available for a
foreground, offline-capable launch; stop it with Ctrl-C.

Prerequisites: macOS Apple Silicon, Python 3.11+ (tested with 3.12), Node.js 20.19+ or 22.12+, and FFmpeg. Install missing tools with Homebrew:

```sh
brew install python@3.12 node ffmpeg uv
```

Setup uses an existing `uv`, or installs a project-local copy in `.tools`. Python packages live in `.venv`; exact dependencies are recorded in `uv.lock`, and frontend dependencies in `frontend/package-lock.json`. No paid services or API keys are needed for local narration. Optional AI briefings use your OpenAI API account.

## Your files

- MP3s: `~/Music/Articles/`, 128 kbps, 24 kHz, mono, with FFmpeg loudness normalisation (-18 LUFS target, -1.5 dB true peak target).
- Each new MP3 has a matching JSON file preserving original extracted text, prepared narration and source metadata. Both are also retained in SQLite.
- Title, author, publication, date and source URL are embedded as ID3 title, artist, album, date and comment when supplied by the source.
- History, original and prepared text, audio cache and logs: `.data/` in this project.
- Model weights: `models/kokoro-v1.0.onnx` and `models/voices-v1.0.bin` (roughly 338 MB combined).

Use **Show in Finder**, **Download**, or the library's audio player. AirDrop the MP3 to your iPhone and open/import it in an audio player that supports local files, or sync it through Finder/Music. Transfer is manual.

Override directories before starting if desired:

```sh
ARTICLE_OUTPUT_DIR="$HOME/Music/Articles" ARTICLE_DATA_DIR="$PWD/.data" ./scripts/start.sh
```

## Local model and offline use

[Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx) runs the Kokoro 82M v1 model using native ARM64 ONNX Runtime CPU inference. CPU is intentional: it works reliably on Apple Silicon without depending on partial CoreML/Metal operator support. Inference uses four threads, one worker, and one lazily loaded model. There is no cloud TTS fallback. All eight British voices were verified against the downloaded bundle: George (default), Lewis, Daniel, Fable, Emma, Isabella, Alice and Lily.

The installed package is pinned to 0.4.9. Its released code assumes integer speed for `input_ids` exports, while the current official model requires floating-point speed. `backend/kokoro_compat.py` inspects actual graph dtypes to preserve fractional speeds; regression tests cover this. It also bounds phoneme batches so very long sentences cannot be silently truncated. Article chunks preserve complete sentences; the model's own context window may divide an unusually long sentence internally.

Model assets come from the [upstream model-files-v1.1 release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1). Run `.venv/bin/python scripts/download_models.py` to fetch missing files independently. The downloader uses macOS curl with TLS verification and atomic replacement. It records local SHA256 fingerprints in `models/checksums.json`; these are local fingerprints, not an upstream signature. Cache keys fingerprint both model and voice files. Model weights are Apache 2.0; the wrapper is MIT. See upstream for licenses.

After setup, **pasted text and local files work completely offline with Include Briefings switched off**. Starting the application, phonemisation and synthesis require no downloads. Public URL ingestion needs internet access. The built frontend uses system fonts and no remote assets. Do not use `uv run` at launch if you want to avoid dependency resolution; the launcher directly invokes `.venv/bin/python`.

## Reliability and limits

A background thread performs sequential synthesis, leaving the FastAPI event loop responsive. SQLite persists queue state; queued jobs and jobs interrupted during narration/encoding resume on restart. Completed chunks are validated WAV files and reused by content, voice, speed, pause policy and model fingerprint. Each failed chunk is retried up to three times. The UI reports actual completed chunks, then a separate encoding phase. It never advances on a timer.

Cancel takes effect after the current synthesis chunk; encoding already in progress finishes before its cancelled output is removed. Resume uses completed cached chunks. Cache files older than 30 days and partial cache/output writes are cleaned at startup; model replacement or cache expiry requires regeneration. Source text is retained even when audio cache is cleaned. Logs include model load and per-chunk synthesis timings. Text is not guaranteed bit-for-bit deterministic across runtime/hardware versions.

Trafilatura extracts public page content with metadata, removes boilerplate and deduplicates extraction. Paragraphs and headings remain in reading order. Preparation removes common markup/footnote references and expands a small set of abbreviations. Dates and numbers retain their original forms for the English phonemiser; unusual expressions may need preview editing. Subscription/login pages, scripts-only pages or extraction failures should be handled by pasting/importing text you are authorised to read. The app does not authenticate or bypass access controls. Review the extracted text before generating: automatic extraction cannot guarantee perfect results for every publisher.

URL downloads have timeouts, redirect checks and a 10 MB cap; imported files and text are capped at 2 MB (well above 20,000 words). The server binds to loopback, validates Host/Origin, and rejects private-network URL targets. It is a personal local application, not a multiuser or public server. Do not expose its port to the internet. Source URL and path information remain in local history and MP3 metadata.

## Development and verification

```sh
.tools/bin/uv sync --frozen  # or uv sync --frozen
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
# In a second terminal:
cd frontend && npm run dev
# Development UI: http://127.0.0.1:5173 (proxies /api to the backend)
```

```sh
.venv/bin/python -m pytest -q
(cd frontend && npm run build)
# With backend running, creates a real George narration using original sample prose:
.venv/bin/python scripts/smoke.py
```

Tests cover HTML/Markdown extraction and metadata, preparation, sentence/paragraph boundaries, 20,000+ word chunking without loss, filename safety, cache reuse/invalidation/corruption, model input dtype compatibility, bounded phonemes, cancellation/resume, restart recovery, imports, local-origin restrictions and real FFmpeg MP3 metadata/encoding. Unit tests use a fake synthesiser; `scripts/smoke.py` explicitly exercises the real local model.

Initial benchmark on this Mac: model loading and fingerprints 0.79 seconds, two original-prose chunks synthesised in 3.64 seconds for about 16.48 seconds of encoded audio (~4.5× real time). This is a short smoke benchmark, not a measured full-article throughput guarantee. Public Substack URL extraction was also verified (3,015 words with title, author, publication and date). A 20,000+ word article was verified for chunk integrity; full-length audio rendering was not benchmarked.

The React Native companion in [`mobile/`](mobile/README.md) supports iOS and Android.
Click **Send to Phone** on completed audio and scan its QR code inside the mobile
app to download over local Wi-Fi. **Export Stack** creates a portable `.stack`
file with audio, source text and chapter metadata for the app's file importer.
New recordings include article timestamps; regenerate older recordings to add
them. The phone stores recordings for offline playback and remembers progress.
The editor remains loopback-only; a separate scoped transfer service starts on
port 8766 when needed. See the mobile README for setup, permissions and limits.

Share Sheet extensions, folder watching, M4B chapters and cloud delivery are not implemented.

Document dates can be reviewed or entered through Edit. Full dates are announced in British spoken order (for example, “the second of November, 2026”). Missing dates are omitted; upload dates are never substituted. Regenerate existing audio to include the new introductions.

## Optional Spoken Briefings

Before each document, Reading Stacks can announce its title, type, author, publication, publication date and date added (where known), followed by a short AI summary and the full document. The stack title is spoken first. Older documents with no recorded date added omit that date.

1. Copy `.env.example` to `.env` in the project folder (keep any existing settings).
2. Set `OPENAI_API_KEY` to your own OpenAI API key. Optionally change `OPENAI_SUMMARY_MODEL` (default: `gpt-6-luna`).
3. Restart the local server: `python3 scripts/services.py stop local`, then `make start`.
4. Select **Include Briefings**, then **Create Audio** or **Regenerate Audio**. With a configured key, briefings are enabled by default.

The key stays on the Mac, is ignored by Git, and is never sent to the browser or phone. With briefings enabled, document text is sent to OpenAI and API usage is billed to your account. Requests use `store: false`; voice generation still runs locally.

Expand a document's **Briefing** to generate or review it. Use **Edit** to adjust its summary, author, publication or document type. Editing the document text clears its old summary; unchanged documents reuse cached summaries. Very long documents are summarised in sections before producing the final short briefing.

Progress shows **Preparing Briefing** before narration. If OpenAI fails, retry or choose **Continue Without Briefings**. Audio uses a frozen snapshot, so editing the stack during creation affects the next recording. Phone exports include the spoken summary and chapter timing. Existing recordings are unchanged until regenerated.

## Automatic iPhone connection

Run the Mac app and open Reading Stacks on your iPhone on the same Wi-Fi. The phone finds this Mac automatically with Bonjour. Approve the matching code in the Mac web app once; subsequent connections and saved-share delivery are automatic while the phone app is active. No pasted link is needed. Install the updated native app with `make device` and allow Local Network access. The inbox listens on port 8767; the editor stays loopback-only. See [iPhone sharing](mobile/SHARE_EXTENSION.md).

## Personal iCloud sync and builds for other users

Use **iPhone Sharing → Send from anywhere with iCloud Drive** to select an empty
Reading Stacks folder in your own iCloud Drive. Select the same folder in the iPhone
app's **Mac connection → iCloud Drive** setup. Shares wait for your Mac to be online;
completed audio returns to the phone's library while its app is open. Automatic
local audio generation can be turned off in the Mac setup panel.

Other developers can copy `mobile/.env.example` to `mobile/.env.local` and supply
their own signing team and app identifier. Each person's selected iCloud folder and
Mac data stay separate. See [iCloud setup, limits and distribution](mobile/ICLOUD_SYNC.md).
