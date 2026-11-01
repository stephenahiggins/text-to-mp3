import json
import shutil
import subprocess
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from backend.text import chunks, extract, filename, prepare
from backend.audio import Engine, cache_key, encode, valid_audio


def test_extraction():
    html = '<html><head><title>A garden after rain</title><meta name="author" content="Test Writer"></head><body><nav>Subscribe Account</nav><article><h1>A garden after rain</h1>' + ''.join(f'<p>On morning {i}, the gardener opened the gate and watched the birds gather near the old apple tree. The rain had left silver drops along every leaf.</p>' for i in range(5)) + '</article><footer>Cookie settings</footer></body></html>'
    article = extract(html, 'html')
    assert 'morning 0' in article['original']
    assert article['original'].index('morning 0') < article['original'].index('morning 4')
    assert 'Cookie settings' not in article['original']
    assert article['author'] == 'Test Writer'


def test_markdown():
    article = extract('# Garden\n\nA **green** leaf.\n\n## Evening\n\nThe gate closes.', 'md')
    assert article['title'] == 'Garden'
    assert 'Evening' in article['original']
    assert '**' not in article['original']


def test_preparation():
    assert prepare('## Hello\n\nRead [this](https://example.com)[1], e.g. today.') == 'Hello\n\nRead this, for example today.'


def test_long_chunking():
    text = '\n\n'.join(['Dr. Smith walks home. The road is quiet. ' * 40]*100)
    parts = chunks(text)
    assert len(text.split()) > 20000
    assert ' '.join(p['text'] for p in parts).split() == text.split()
    assert all(p['text'].endswith('.') for p in parts)
    assert sum(p['pause']==0.4 for p in parts) == 100
    assert not any(p['text'].endswith('Dr.') for p in parts)


def test_unsplittable_sentence():
    sentence = 'A ' * 1000 + 'finish.'
    assert len(chunks(sentence)) == 1


@pytest.mark.parametrize('title', ['../../Hello: world?!','', '你好', '/\\:*?"<>|', 'x'*300])
def test_filename(title):
    result = filename(title)
    assert result and len(result)<=100 and '/' not in result and '..' not in result


def test_cache(tmp_path):
    class Fake:
        calls=0
        def create(self, text, **kwargs):
            self.calls+=1
            return np.zeros(2400, dtype=np.float32), 24000
    engine=Engine()
    engine.model=Fake()
    engine.model_id='test'
    part={'text':'Hello.', 'pause':0.4}
    path=engine.render(part,'bm_george',1,tmp_path)
    assert valid_audio(path)
    assert engine.render(part,'bm_george',1,tmp_path)==path
    assert engine.model.calls==1
    engine.render(part,'bm_george',1.1,tmp_path)
    assert engine.model.calls==2
    path.write_bytes(b'broken')
    engine.render(part,'bm_george',1,tmp_path)
    assert engine.model.calls==3
    assert cache_key('x','v',1,'a',0)!=cache_key('x','v',1,'b',0)


def test_mp3(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('Install ffmpeg')
    wav=tmp_path/'chunk.wav'
    sf.write(wav, .15*np.sin(np.arange(24000)*2*np.pi*220/24000),24000)
    output=tmp_path/'output.mp3'
    encode([wav,wav],output,{'title':'Original sample','author':'Test Author','publication':'Local Letters','url':'https://example.com/story'})
    info=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-show_format','-show_streams','-of','json',str(output)]))
    assert info['streams'][0]['codec_name']=='mp3'
    assert info['streams'][0]['sample_rate']=='24000'
    assert int(info['streams'][0]['bit_rate'])>=128000
    assert 2 <= float(info['format']['duration']) < 2.2
    assert info['format']['tags']['title']=='Original sample'
    assert info['format']['tags']['artist']=='Test Author'
    assert info['format']['tags']['comment']=='https://example.com/story'
