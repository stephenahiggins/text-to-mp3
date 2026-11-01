import numpy as np
from backend.kokoro_compat import CompatibleKokoro
from kokoro_onnx.config import MAX_PHONEME_LENGTH


def test_model_schema_keeps_fractional_speed():
    class Input:
        def __init__(self,name,type):self.name=name;self.type=type
    class Session:
        def get_inputs(self):return [Input('input_ids','tensor(int64)'),Input('style','tensor(float)'),Input('speed','tensor(float)')]
        def run(self, _, values):
            assert values['speed'].dtype == np.float32
            assert values['speed'][0] == 1.25
            assert values['input_ids'].dtype == np.int64
            return [np.zeros(100)]
    class Tokenizer:
        def tokenize(self, text):return [1,2,3]
    engine=object.__new__(CompatibleKokoro)
    engine.sess=Session()
    engine.tokenizer=Tokenizer()
    result,rate=engine._create_audio('test',np.zeros((510,256)),1.25)
    assert rate==24000 and len(result)==100


def test_phonemes_never_truncated():
    engine=object.__new__(CompatibleKokoro)
    text='a'*2000 + ' hello world. ' + 'b '*1000
    parts=engine._split_phonemes(text)
    assert all(len(p)<MAX_PHONEME_LENGTH for p in parts)
    assert ''.join(parts).replace(' ','')==text.replace(' ','')
