"""Render document highlights, original links, and contextual agenda trees."""
from __future__ import annotations

import colorsys
from html import escape
import re
from urllib.parse import urlsplit

HIGHLIGHTS = {
    'yellow': '#FEF3C7', 'green': '#DCF3E3', 'cyan': '#DDF3F5',
    'magenta': '#F5E1F1', 'blue': '#E1EAFE', 'red': '#FBE3E2',
    'darkYellow': '#F2E8CB', 'darkGreen': '#DFEBE2', 'darkCyan': '#DFECEE',
    'darkMagenta': '#EDE0ED', 'darkBlue': '#E2E6F1', 'darkRed': '#F0DFDF',
    'lightGray': '#F0F1F3', 'darkGray': '#E5E7EB', 'black': '#E5E7EB', 'white': '#F8FAFC',
}


def safe_link(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        return value if url.scheme.lower() in ('https', 'http', 'ftp') and url.netloc else None
    except ValueError:
        return None


def gentle_color(value, background=False):
    if not re.fullmatch(r'[0-9a-fA-F]{6}', value or ''):
        return None
    hue, light, saturation = colorsys.rgb_to_hls(*(int(value[i:i+2], 16)/255 for i in (0,2,4)))
    if background:
        light, saturation = .93, min(saturation, .65)
    elif saturation < .12:
        return None  # Use readable default ink, including originally white text.
    else:
        light, saturation = .30, min(saturation, .65)
    return '#' + ''.join(f'{round(c*255):02X}' for c in colorsys.hls_to_rgb(hue, light, saturation))


def rich_text(topic, key):
    paragraphs = topic.get('rich_text', {}).get(key)
    if not paragraphs:
        return escape(topic.get(key, '')).replace('\n', '<br>')
    output = []
    for paragraph in paragraphs:
        # Word frequently splits a single highlight/link across multiple runs.
        # Coalesce identical presentation so highlights read as continuous text.
        groups = []
        for run in paragraph['runs']:
            style = {k: run.get(k) for k in ('highlight','shading','color','bold','italic','underline','strike','href')}
            if groups and groups[-1][0] == style:
                groups[-1][1] += run['text']
            else:
                groups.append([style, run['text']])
        parts = []
        for style, text in groups:
            if not text:
                continue
            body = escape(text).replace('\n', '<br>')
            if style['bold']:
                body = f'<strong>{body}</strong>'
            if style['italic']:
                body = f'<em>{body}</em>'
            if style['strike']:
                body = f'<s>{body}</s>'
            href = safe_link(style['href'])
            if href:
                body = f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{body}</a>'
            elif style['underline']:
                body = f'<span class="source-underline">{body}</span>'
            ink = gentle_color(style['color'])
            background = HIGHLIGHTS.get(style['highlight']) or gentle_color(style['shading'], background=True)
            if ink:
                body = f'<span style="color:{ink}">{body}</span>'
            if background:
                body = f'<mark class="source-highlight" style="background-color:{background}">{body}</mark>'
            parts.append(body)
        output.append(''.join(parts))
    return '<br>'.join(output)


def agenda_tree(items):
    roots = {}
    for item in items:
        levels = item.get('hierarchy') or [{'agenda_item': item['agenda_item'], 'description': item['description']}]
        branch = roots
        for level in levels:
            number = str(level['agenda_item'])
            node = branch.setdefault(number, {'description': level.get('description'), 'children': {}, 'selected': False})
            node['selected'] |= number == str(item['agenda_item'])
            branch = node['children']
        # A parent-description fallback must not erase the originally requested AI.
        if str(levels[-1]['agenda_item']) != str(item['agenda_item']):
            branch.setdefault(str(item['agenda_item']), {'description': 'Description unavailable', 'children': {}, 'selected': True})

    def render(nodes):
        result = '<ul>'
        for number, node in nodes.items():
            selected = ' class="agenda-selected"' if node['selected'] else ''
            result += (f'<li{selected}><span class="agenda-node"><span class="agenda-number">{escape(number)}</span> '
                       f'{escape(node["description"] or "Description unavailable")}</span>')
            if node['children']:
                result += render(node['children'])
            result += '</li>'
        return result + '</ul>'
    return f'<div class="topic-agenda-tree" aria-label="Agenda hierarchy">{render(roots)}</div>' if roots else ''


def render_topic_references(topics, preamble=()):
    if not topics:
        return ''
    output = ['<details class="topic-references" open><summary>Topics</summary>']
    output.extend(f'<p>{escape(text).replace(chr(10), "<br>")}</p>' for text in preamble if text.strip())
    output.extend(['<div class="topic-scroll"><table><thead><tr><th>Topic</th><th>Documents / notes</th>',
                   '<th>Agenda item</th></tr></thead><tbody>'])
    for topic in topics:
        source = (f'<div class="topic-agenda-source">'
                  f'<span class="topic-agenda-label">{rich_text(topic, "agenda_item")}</span></div>'
                  if topic.get('agenda_item', '').strip() else '')
        tree = agenda_tree(topic.get('agenda_descriptions', []))
        description = (f'<div class="topic-agenda-description"><div class="topic-field-label">Description</div>{tree}</div>'
                       if tree else '')
        output.append(f'<tr><td>{rich_text(topic, "title")}</td><td>{rich_text(topic, "documents")}</td>'
                      f'<td>{source}{description}</td></tr>')
    output.append('</tbody></table></div></details>')
    return ''.join(output)
