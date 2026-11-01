import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import logging
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / 'models'


def cache_key(text, voice, speed, model_id, pause):
    return hashlib.sha256(json.dumps([text, voice, speed, model_id, pause, 'audio-v1'], ensure_ascii=False).encode()).hexdigest()


def valid_audio(path):
    try:
        info = sf.info(path)
        return info.frames > 0 and info.samplerate == 24000
    except (RuntimeError, OSError):
        return False


class Engine:
    def __init__(self):
        self.model = None
        self.model_id = None

    def load(self):
        if self.model is None:
            from backend.kokoro_compat import CompatibleKokoro
            import onnxruntime as ort
            model = MODELS / 'kokoro-v1.0.onnx'
            voices = MODELS / 'voices-v1.0.bin'
            if not model.exists() or not voices.exists():
                raise RuntimeError('Model files missing. Run ./scripts/setup.sh while online first.')
            start = time.monotonic()
            # Fingerprint both files once, so replacing weights invalidates the cache.
            digest = hashlib.sha256()
            for path in (model, voices):
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        digest.update(block)
            self.model_id = digest.hexdigest()
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            ort.disable_telemetry_events()
            session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
            self.model = CompatibleKokoro.from_session(session, str(voices))
            logging.info('Model load: %.2fs', time.monotonic() - start)

    def render(self, chunk, voice, speed, cache):
        self.load()
        target = cache / (cache_key(chunk['text'], voice, speed, self.model_id, chunk['pause']) + '.wav')
        if valid_audio(target):
            return target
        start = time.monotonic()
        for attempt in range(3):
            try:
                samples, rate = self.model.create(chunk['text'], voice=voice, speed=speed, lang='en-gb')
                if rate != 24000 or not len(samples) or not np.isfinite(samples).all():
                    raise RuntimeError('Model returned invalid audio')
                samples = np.clip(samples, -1, 1)
                fade = min(120, len(samples)//2)
                samples[:fade] *= np.linspace(0, 1, fade)
                samples[-fade:] *= np.linspace(1, 0, fade)
                samples = np.concatenate([samples, np.zeros(int(rate*chunk['pause']))])
                temp = target.with_suffix('.part.wav')
                sf.write(temp, samples, rate, subtype='PCM_16')
                temp.replace(target)
                logging.info('Synthesis: %.2fs for %.2fs audio', time.monotonic()-start, len(samples)/rate)
                return target
            except Exception:
                if attempt == 2:
                    raise
                logging.exception('Chunk failed; retrying')
                time.sleep(attempt + 1)


def encode(paths, output, article):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='article-voice-') as tmp:
        listing = Path(tmp) / 'concat.txt'
        listing.write_text(''.join("file '" + str(Path(p).resolve()).replace("'", "'\\''") + "'\n" for p in paths))
        partial = output.with_suffix('.part.mp3')
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-af', 'loudnorm=I=-18:TP=-1.5:LRA=11', '-ar', '24000', '-ac', '1', '-codec:a', 'libmp3lame', '-b:a', '128k', '-id3v2_version', '3']
        for key, value in dict(title=article['title'], artist=article.get('author',''), album=article.get('publication',''), comment=article.get('url',''), date=article.get('date','')).items():
            command += ['-metadata', f'{key}={value}']
        try:
            subprocess.run(command + [str(partial)], check=True, capture_output=True)
            os.replace(partial, output)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('FFmpeg: ' + exc.stderr.decode(errors='replace')[-1500:]) from exc
        finally:
            partial.unlink(missing_ok=True)
