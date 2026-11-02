# Article Stacks

A local article-to-MP3 application for Apple Silicon macOS. Paste a public Substack/article URL, paste text, or import HTML, Markdown or TXT. Review and edit the narration, select a British narrator, then generate a tagged MP3 for your iPhone.

## Stacks

Click New Stack and rename its title inline, then add public URLs, pasted text, or HTML, Markdown, TXT, text-based PDF and Word (.docx) files. Multiple file imports are supported. Scans and images need narration text supplied through Paste Text. Review and edit documents, reorder them, or remove them. Additions and ordering are saved immediately; document edits use Save Changes. Saved Stacks survive browser and backend restarts. Drag Stack cards to reorder them, or use Alt + Arrow keys while a card is focused. Cards show creation dates and audio status: green for current audio, amber for changes since generation, and black when no audio exists. Completed audio shows its last generation time.

Choose a voice and speed, then Create Audio to produce one MP3 in document order, beginning with the Stack title, then each document’s title and date (when available), with pauses between documents. The job captures an immutable snapshot, so you can keep adding to the Stack during generation. A successful matching version marks the Stack Audiofied. Subsequent edits mark the audio as needing an update; Regenerate Audio retains older versions while reusing cached chunks. Existing conversions migrate automatically to single-document Stacks.

## Start

From this directory:

```sh
./scripts/setup.sh   # first run: install dependencies, download weights, build UI
./scripts/start.sh   # subsequent runs, including offline
```

Open http://127.0.0.1:8765. The launcher opens your default browser automatically. Stop the server with Control-C. Only run one backend process against a data directory; the queue deliberately uses a single worker and a single model instance.

Prerequisites: macOS Apple Silicon, Python 3.11+ (tested with 3.12), Node.js 20.19+ or 22.12+, and FFmpeg. Install missing tools with Homebrew:

```sh
brew install python@3.12 node ffmpeg uv
```

Setup uses an existing `uv`, or installs a project-local copy in `.tools`. Python packages live in `.venv`; exact dependencies are recorded in `uv.lock`, and frontend dependencies in `frontend/package-lock.json`. No paid services or API keys are needed.

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

After setup, **pasted text and local files work completely offline**. Starting the application, phonemisation and synthesis require no downloads. Public URL ingestion needs internet access. The built frontend uses system fonts and no remote assets. Do not use `uv run` at launch if you want to avoid dependency resolution; the launcher directly invokes `.venv/bin/python`.

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

Stretch features (Share Sheet, folder watching, M4B chapters and automatic iPhone transfer) are not implemented.

Document dates can be reviewed or entered through Edit. Full dates are announced in British spoken order (for example, “the second of November, 2026”). Missing dates are omitted; upload dates are never substituted. Regenerate existing audio to include the new introductions.
