"""Spoken introductions without altering the saved source documents."""
from datetime import date, datetime
import re
from backend.text import prepare, chunks

ORDINALS = ('first second third fourth fifth sixth seventh eighth ninth tenth '
            'eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth '
            'eighteenth nineteenth twentieth twenty-first twenty-second twenty-third '
            'twenty-fourth twenty-fifth twenty-sixth twenty-seventh twenty-eighth '
            'twenty-ninth thirtieth thirty-first').split()
MONTHS = ('January February March April May June July August September October November December').split()


def spoken_date(value):
    value = str(value or '').strip()
    if not value:
        return ''
    parsed = None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00')).date()
        except ValueError:
            # Explicit formats only: never fill missing components with today's date.
            for fmt in ('%d %B %Y', '%d %b %Y', '%B %d, %Y', '%b %d, %Y', '%d/%m/%Y'):
                try:
                    parsed = datetime.strptime(value, fmt).date()
                    break
                except ValueError:
                    continue
    if parsed:
        return f'The {ORDINALS[parsed.day-1]} of {MONTHS[parsed.month-1]}, {parsed.year}'
    # Preserve partial dates and unusual source labels rather than inventing a date.
    return prepare(value)


def sentence(value):
    value = prepare(value).strip()
    return value if not value or re.search(r'[.!?][\"”\'’]?$', value) else value + '.'


def stack_parts(article):
    intro = chunks(sentence(article['title']))
    for part in intro:
        part.update(document=0, document_title=article['title'])
    if intro:
        intro[-1]['pause'] = 0.8
    parts = intro
    for index, document in enumerate(article['documents']):
        heading = sentence(document['title'])
        date_text = spoken_date(document.get('date'))
        if date_text:
            heading += '\n\n' + sentence(date_text)
        briefing_only = document.get('narration_mode') == 'briefing'
        if article.get('include_briefings') or briefing_only:
            kind=document.get('content_type','document')
            heading=sentence(f"Next is {document['title']}, {'an' if kind.lower().startswith(('a','e','i','o','u')) else 'a'} {kind}" + (f" by {document['author']}" if document.get('author') else ''))
            if document.get('publication'): heading += '\n\n' + sentence('From ' + document['publication'])
            if date_text: heading += '\n\n' + sentence('Published on the ' + date_text.removeprefix('The '))
            if document.get('added_at'):
                added=datetime.fromtimestamp(document['added_at']).date().isoformat()
                heading += '\n\n' + sentence('You added it to this stack on the ' + spoken_date(added).removeprefix('The '))
            heading += '\n\n' + sentence(document.get('briefing_summary',''))
            if not briefing_only: heading += '\n\nNow, the full document.'
        section = chunks(heading if briefing_only else heading + '\n\n' + document['prepared'])
        for part in section:
            part.update(document=index+1, document_title=document['title'])
        if section:
            section[-1]['pause'] = 0.8
        parts.extend(section)
    return parts


def estimate_duration(article, speed=1):
    """Estimate listening time without synthesis or paid summary requests."""
    from copy import deepcopy
    snapshot = deepcopy(article)
    pending = 0
    for document in snapshot['documents']:
        if snapshot.get('include_briefings') or document.get('narration_mode') == 'briefing':
            summary = document.get('briefing_override') or document.get('briefing_summary')
            if not summary:
                pending += 1
                summary = ' '.join(['summary'] * 50)
            document['briefing_summary'] = summary
    durations = [0.0] * (len(snapshot['documents']) + 1)
    for part in stack_parts(snapshot):
        durations[part['document']] += len(part['text'].split()) * 60 / (155 * speed) + part['pause']
    return dict(seconds=sum(durations), pending_briefings=pending,
                items=[dict(id=d.get('id'), seconds=durations[i+1]) for i,d in enumerate(snapshot['documents'])])
