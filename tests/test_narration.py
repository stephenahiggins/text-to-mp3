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
