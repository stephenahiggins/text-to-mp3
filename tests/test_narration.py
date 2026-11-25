from copy import deepcopy
import pytest
from backend.app import article_parts
from backend.narration import spoken_date


@pytest.mark.parametrize('value,expected', [
    ('2026-11-02','The second of November, 2026'),
    ('2024-02-29T12:30:00Z','The twenty-ninth of February, 2024'),
    ('1 January 2025','The first of January, 2025'),
    ('November 21, 2026','The twenty-first of November, 2026'),
    ('02/11/2026','The second of November, 2026'),
    ('2026','2026'), ('2026-02-30','2026-02-30'), ('',''), (None,''),
])
def test_spoken_dates(value,expected):
    assert spoken_date(value)==expected


def test_intro_order_and_missing_date():
    article={'title':'Weekend Reading','documents':[
        {'title':'Morning!','date':'2026-11-02','prepared':'The robin sings.'},
        {'title':'Evening','date':'','prepared':'The stars appear.'},
    ]}
    original=deepcopy(article)
    parts=article_parts(article)
    assert [p['text'] for p in parts]==['Weekend Reading.','Morning!','The second of November, 2026.','The robin sings.','Evening.','The stars appear.']
    assert [p['document'] for p in parts]==[0,1,1,1,2,2]
    assert parts[0]['pause']==parts[3]['pause']==parts[-1]['pause']==0.8
    assert article==original


def test_briefing_only_and_duration_estimate():
    from backend.narration import stack_parts, estimate_duration
    article = {'title':'Reading', 'documents':[
        {'id':'one', 'title':'River', 'prepared':'UNSPOKEN ' * 1000,
         'narration_mode':'briefing', 'briefing_summary':'A river overview.'},
        {'id':'two', 'title':'Garden', 'prepared':'Flowers grow here.'},
    ]}
    text = ' '.join(p['text'] for p in stack_parts(article))
    assert 'UNSPOKEN' not in text
    assert 'A river overview.' in text and 'Flowers grow here.' in text
    assert 'Now, the full document.' not in text
    estimate = estimate_duration(article)
    assert estimate['pending_briefings'] == 0
    faster = estimate_duration(article, 2)
    assert estimate['seconds'] / 2 < faster['seconds'] < estimate['seconds']
    whole = deepcopy(article)
    whole['documents'][0]['narration_mode'] = 'whole'
    assert estimate_duration(whole)['seconds'] > estimate['seconds'] * 10
    del article['documents'][0]['briefing_summary']
    pending = estimate_duration(article)
    assert pending['pending_briefings'] == 1
    assert pending['seconds'] > estimate['seconds']
    assert 'briefing_summary' not in article['documents'][0]
