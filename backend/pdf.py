"""Bounded, selectable-text PDF extraction shared by uploads and URL ingestion."""
from io import BytesIO
from pypdf import PdfReader

MAX_BYTES = 20_000_000


def _extract(content, title='', url=''):
    if len(content) > MAX_BYTES:
        raise ValueError('PDF exceeds the 20 MB limit')
    if b'%PDF-' not in content[:1024]:
        raise ValueError('This file is not a PDF')
    reader = PdfReader(BytesIO(content))
    if reader.is_encrypted:
        raise ValueError('Encrypted PDFs are not supported')
    if len(reader.pages) > 500:
        raise ValueError('PDF exceeds the 500 page limit')
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer
    pages, total = [], 0
    for page in reader.pages:
        stream = page.get_contents()
        if stream is not None and len(stream.get_data()) > 20_000_000:
            raise ValueError('PDF page is too complex to extract')
    for layout in extract_pages(BytesIO(content)):
        text = '\n'.join(box.get_text() for box in layout if isinstance(box, LTTextContainer))
        total += len(text)
        if total > 2_000_000:
            raise ValueError('PDF exceeds the 2 million character limit')
        pages.append(text.strip())
    if not any(pages):
        raise ValueError('No selectable text found. Scanned PDFs need OCR; paste their text instead.')
    # Preserve page boundaries. Do not silently strip equations, references or columns.
    metadata = reader.metadata
    return dict(title=(title if title and title != 'Untitled article' else
                       str(metadata.title or '') if metadata else '')[:500] or 'PDF document',
                author=str(metadata.author or '') if metadata else '', url=url,
                original='\n\n'.join(pages), content_type='PDF')


def pdf_article(content, title='', url=''):
    # PDF parsers may consume excessive CPU on small but complex documents.
    # A child process gives every import a hard deadline without stranding threads.
    import json
    import subprocess
    import sys
    from pathlib import Path
    if len(content) > MAX_BYTES:
        raise ValueError('PDF exceeds the 20 MB limit')
    try:
        result = subprocess.run([sys.executable, '-m', 'backend.pdf'], input=content,
                                capture_output=True, timeout=60, cwd=Path(__file__).resolve().parent.parent)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('PDF extraction exceeded 60 seconds. Try a smaller document.') from exc
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace')[-1000:] or 'Could not read this PDF')
    article = json.loads(result.stdout)
    article['url'] = url
    if title and title != 'Untitled article' and article['title'] == 'PDF document':
        article['title'] = title[:500]
    return article


if __name__ == '__main__':
    import json
    import sys
    try:
        print(json.dumps(_extract(sys.stdin.buffer.read(MAX_BYTES + 1))))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
