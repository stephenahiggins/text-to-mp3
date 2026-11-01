"""Adapt Kokoro 0.4.9 to the actual ONNX input schema.

The upstream release assumes integer speed when an export uses input_ids.
The official v1.1 asset uses input_ids with FLOAT speed. Inspect the graph
rather than inferring dtypes from the name, preserving fractional speeds.
Also bound every phoneme batch so unusually long sentences aren't truncated.
"""
import re
import numpy as np
from kokoro_onnx import Kokoro
from kokoro_onnx.config import MAX_PHONEME_LENGTH, SAMPLE_RATE


class CompatibleKokoro(Kokoro):
    def _split_phonemes(self, phonemes):
        parts = []
        remaining = phonemes.strip()
        limit = MAX_PHONEME_LENGTH - 2
        while len(remaining) > limit:
            window = remaining[:limit]
            boundaries = [m.end() for m in re.finditer(r'[.!?;,]\s|\s', window)]
            end = boundaries[-1] if boundaries else limit
            parts.append(remaining[:end].strip())
            remaining = remaining[end:].strip()
        if remaining:
            parts.append(remaining)
        return parts

    def _create_audio(self, phonemes, voice, speed):
        tokens = self.tokenizer.tokenize(phonemes)
        if not tokens:
            raise ValueError('This text produced no pronounceable words')
        schema = {i.name: i.type for i in self.sess.get_inputs()}
        token_name = 'input_ids' if 'input_ids' in schema else 'tokens'
        dtypes = {'tensor(float)': np.float32, 'tensor(double)': np.float64, 'tensor(int64)': np.int64, 'tensor(int32)': np.int32}
        inputs = {
            token_name: np.array([[0, *tokens, 0]], dtype=dtypes[schema[token_name]]),
            'style': np.asarray(voice[min(len(tokens),len(voice)-1)], dtype=dtypes[schema['style']]),
            'speed': np.array([speed], dtype=dtypes[schema['speed']]),
        }
        return self.sess.run(None, inputs)[0].ravel(), SAMPLE_RATE
