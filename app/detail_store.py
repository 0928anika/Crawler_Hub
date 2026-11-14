"""Lossless local HTML archive and explicit Excel overflow handling."""
from pathlib import Path
import uuid
import os
from bs4 import BeautifulSoup, Comment

EXCEL_TEXT_LIMIT = 32767
OVERFLOW_NOTICE = ''  # Oversized content is a registration error, never a download placeholder.


DETAIL_WIDTH = 860


def registration_layout(markup):
    """Apply our own responsive layout after removing untrusted source styles."""
    soup = BeautifulSoup(str(markup or ''), 'html.parser')
    if not soup.find('img'):
        return str(soup).strip()
    # Unwrap outer layout containers to keep repeated exports idempotent.
    while True:
        roots = [node for node in soup.contents if str(node).strip()]
        if len(roots) != 1 or getattr(roots[0], 'name', None) != 'div':
            break
        roots[0].unwrap()
    for image in soup.find_all('img'):
        image.attrs.pop('height', None)
        image['width'] = str(DETAIL_WIDTH)
        image['style'] = ('display:block;width:100%%;max-width:%spx;height:auto;margin:0 auto'
                          % DETAIL_WIDTH)
    for table in soup.find_all('table'):
        table['border'] = '1'
        table['cellpadding'] = '8'
        table['cellspacing'] = '0'
        table['style'] = 'width:100%;border-collapse:collapse;margin:16px auto;font-size:14px;line-height:1.6;color:#333'
        for cell in table.find_all(['th','td']):
            cell['style'] = 'border:1px solid #ddd;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere'
            if cell.name == 'th':cell['style'] += ';background:#f6f6f6;font-weight:bold'
    for paragraph in soup.find_all('p'):
        paragraph['style'] = 'font-size:14px;line-height:1.8;color:#444;margin:14px auto;text-align:left'
    for heading in soup.find_all(['h1','h2','h3']):
        heading['style'] = 'font-size:20px;line-height:1.5;margin:28px 0 14px'
    wrapper = soup.new_tag('div')
    wrapper['align'] = 'center'
    wrapper['style'] = ('width:100%%;max-width:%spx;margin:0 auto;text-align:center'
                        % DETAIL_WIDTH)
    for node in list(soup.contents):
        wrapper.append(node.extract())
    soup.append(wrapper)
    return str(soup).strip()


def excel_detail(markup):
    """Produce compact registration HTML without cutting product content.

    Empty string + overflow=True means unusable for sheet registration, not a
    request to download a separate document. Raw journal data stays untouched.
    """
    import re
    from urllib.parse import urlsplit
    from bs4 import NavigableString
    soup = BeautifulSoup(str(markup or ''), 'html.parser')
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    remove={'script','style','iframe','object','embed','form','input','button','svg','link','meta','head','noscript'}
    allowed={'div','p','br','span','b','strong','em','i','ul','ol','li','dl','dt','dd','table','caption','thead','tbody','tfoot','tr','th','td','h1','h2','h3','h4','h5','h6','img','pre','code','hr'}
    seen_images=set()
    for tag in list(soup.find_all(True)):
        if tag.name is None or tag.parent is None:continue
        if tag.name in remove:
            tag.decompose();continue
        if tag.name not in allowed:
            tag.unwrap();continue
        attrs={}
        if tag.name=='img':
            src=str(tag.get('src','')).strip()
            if not (urlsplit(src).scheme in ('http','https') or src.startswith('/sample/')) or src in seen_images:
                tag.decompose();continue
            seen_images.add(src);attrs={'src':src}
        if tag.name in ('td','th'):
            attrs={k:str(tag[k]) for k in ('colspan','rowspan') if str(tag.get(k,'')).isdigit() and 1<int(tag[k])<=100}
        tag.attrs=attrs
    for text in list(soup.find_all(string=True)):
        if text.parent is None or text.find_parent(['pre','code']):continue
        compact=re.sub(r'\s+',' ',str(text))
        if not compact.strip() and not (text.previous_sibling and text.next_sibling):text.extract()
        else:text.replace_with(NavigableString(compact))
    # Remove layout-only wrappers, preserving block boundaries and table cells.
    for tag in reversed(list(soup.find_all(['div','span']))):
        if tag.parent is None:continue
        if tag.name=='span':tag.unwrap();continue
        meaningful=[c for c in tag.contents if str(c).strip()]
        if len(meaningful)==1 and getattr(meaningful[0],'name',None) in ('div','p','table','ul','ol','img','pre'):
            tag.unwrap()
    seen_blocks=set()
    for tag in list(soup.find_all(['table','p'])):
        if tag.parent is None:continue
        content=str(tag)
        if content in seen_blocks:tag.decompose()
        else:seen_blocks.add(content)
    compact=registration_layout(str(soup))
    return (compact,False) if len(compact)<=EXCEL_TEXT_LIMIT else ('',True)


def archive_detail(markup, directory):
    folder = Path(directory) / 'details'
    folder.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex + '.html'
    file = folder / filename
    # Bytes avoid platform newline conversion and preserve the exact string.
    with file.open('xb') as handle:
        handle.write(str(markup).encode('utf-8'))
        handle.flush();os.fsync(handle.fileno())
    value, overflow = excel_detail(markup)
    return dict(path='details/' + filename, chars=len(markup), overflow=overflow,
                excel_chars=len(value))


def detail_file(directory, relative):
    root = Path(directory).resolve()
    path = (root / relative).resolve()
    if path.parent != root / 'details' or path.suffix != '.html':
        raise ValueError('허용되지 않은 상세 HTML 경로입니다.')
    return path
