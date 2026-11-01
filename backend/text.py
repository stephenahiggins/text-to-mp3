import re
import unicodedata
from bs4 import BeautifulSoup
import markdown
import pysbd
import trafilatura


def extract(source: str, kind='text', title='Untitled article', url='') -> dict:
    if kind == 'html':
        result = trafilatura.bare_extraction(source, url=url or None, include_comments=False, include_tables=False, favor_precision=True, with_metadata=True, deduplicate=True)
        if not result or not result.text:
            raise ValueError('No readable article found. Paste or import text you are authorised to access.')
        return dict(title=result.title or title, author=result.author or '', date=result.date or '', publication=result.sitename or '', url=url, original='\n\n'.join(line.strip() for line in result.text.splitlines() if line.strip()))
    if kind == 'md':
        soup = BeautifulSoup(markdown.markdown(source), 'html.parser')
        heading = soup.find('h1')
        title = heading.get_text() if heading else title
        source = '\n\n'.join(n.get_text(' ', strip=True) for n in soup.find_all(['p','h1','h2','h3','h4','li','blockquote']) if not n.find(['p','li']))
    return dict(title=title, author='', date='', publication='', url=url, original=source.strip())


def prepare(text):
    if re.search(r'</?(?:p|div|h[1-6]|br|script|style)\b', text, re.I):
        soup = BeautifulSoup(text, 'html.parser')
        for node in soup(['script', 'style']):
            node.decompose()
        for node in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'br']):
            node.insert_after('\n\n')
        text = soup.get_text()
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[\^?\d+\]|(?<=\w)[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', text)
    text = re.sub(r'(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|>\s*)', '', text)
    text = re.sub(r'[*_`]', '', text)
    text = re.sub(r'\be\.g\.', 'for example', text)
    text = re.sub(r'\bi\.e\.', 'that is', text)
    text = re.sub(r'\betc\.', 'etcetera', text)
    return '\n\n'.join(re.sub(r'\s+', ' ', p).strip() for p in re.split(r'\n\s*\n', text) if p.strip())


def chunks(text, limit=450):
    segmenter = pysbd.Segmenter(language='en', clean=False)
    result = []
    for paragraph in text.split('\n\n'):
        current = ''
        for sentence in segmenter.segment(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if current and len(current) + len(sentence) + 1 > limit:
                result.append(dict(text=current, pause=0.12))
                current = ''
            current = f'{current} {sentence}'.strip()
        if current:
            result.append(dict(text=current, pause=0.4))
    return result


def filename(title):
    name = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    name = re.sub(r'[^\w -]', '', name).strip(' .-_')[:100].strip()
    return name or 'article'
