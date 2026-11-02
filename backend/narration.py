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
        section = chunks(heading + '\n\n' + document['prepared'])
        for part in section:
            part.update(document=index+1, document_title=document['title'])
        if section:
            section[-1]['pause'] = 0.8
        parts.extend(section)
    return parts
