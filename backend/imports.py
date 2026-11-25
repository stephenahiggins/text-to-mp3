from io import BytesIO
from zipfile import ZipFile


def document_text(content, extension):
    if extension == '.pdf':
        from backend.pdf import pdf_article
        text = pdf_article(content)['original']
    else:
        from docx import Document
        with ZipFile(BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist())>50_000_000:
                raise ValueError('Expanded Document Exceeds 50 MB')
        text='\n\n'.join(p.text for p in Document(BytesIO(content)).paragraphs if p.text.strip())
    if not text.strip(): raise ValueError('No Readable Text Found. Paste Narration Text For Scans Or Images.')
    return text
