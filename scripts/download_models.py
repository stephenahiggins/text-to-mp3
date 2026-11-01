"""Download official Kokoro ONNX release assets, atomically and resumably by file."""
from pathlib import Path
import subprocess
import hashlib
import json
ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'

def main():
    directory = ROOT / 'models'
    directory.mkdir(exist_ok=True)
    hashes = {}
    for name in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'):
        target = directory / name
        if not target.exists():
            partial = target.with_suffix('.download')
            print(f'Downloading {name}…', flush=True)
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--output', str(partial), BASE + name], check=True)
            partial.replace(target)
        digest = hashlib.sha256()
        with target.open('rb') as source:
            for block in iter(lambda: source.read(1024*1024), b''):
                digest.update(block)
        hashes[name] = digest.hexdigest()
    (directory / 'checksums.json').write_text(json.dumps(hashes, indent=2))
    print('Model files ready. Local SHA256 fingerprints saved to models/checksums.json.')

if __name__ == '__main__':
    main()
