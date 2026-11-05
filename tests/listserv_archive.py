"""A mailing list archive for tests: feed, weekly indexes, message pages and attachments."""

from __future__ import annotations

from datetime import datetime
from email.utils import format_datetime
from html import escape
from urllib.parse import quote
import zlib

import httpx

from shared import listserv

LIST = '3GPP_TSG_RAN_WG2'


def when(text: str) -> datetime:
    return datetime.fromisoformat(text)


def attachment_url(mail_id: str, name: str) -> str:
    return (f'{listserv.ARCHIVE}/scripts/wa.exe?A3=ind2611a&L={LIST}&E=base64&P={zlib.crc32(mail_id.encode())}'
            f'&B=--x&T=application%2Fvnd.openxmlformats-officedocument.wordprocessingml.document;'
            f'%20name=%22{quote(name)}%22&N={quote(name)}&attachment=q&XSS=3')


class Archive:
    """Mails by id: ``{'title', 'date' (ISO), 'author', 'files': {name: bytes}}``.

    ``feed`` lists the ids the RSS feed shows (default: all), ``down`` the
    URLs that fail, ``seen`` every URL asked for.
    """

    def __init__(self, mails: dict, feed: list[str] | None = None):
        self.mails = mails
        self.feed = feed
        self.down: set[str] = set()
        self.seen: list[str] = []

    def opened(self) -> list[str]:
        return [url.split(';', 1)[1] for url in self.seen if 'A2=' in url]

    def _rss(self) -> str:
        ids = self.feed if self.feed is not None else list(self.mails)
        ids = sorted(ids, key=lambda i: when(self.mails[i]['date']), reverse=True)
        items = ''.join(
            f'<item><title>{escape(self.mails[i]["title"])}</title>'
            f'<link>{listserv.message_url(i, LIST)}</link><author>{escape(self.mails[i].get("author", "x"))}</author>'
            f'<pubDate>{format_datetime(when(self.mails[i]["date"]))}</pubDate>'
            f'<description>text</description></item>'
            for i in ids)
        return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'

    def _index(self, week_id: str) -> str:
        rows = ''.join(
            f'<tr><td><span><a href="/scripts/wa.exe?A2={LIST};{i}&S=">{escape(m["title"])}</a></span></td>'
            f'<td><div>{escape(m.get("author", "x"))}</div></td>'
            f'<td><div>{format_datetime(when(m["date"]))}</div></td></tr>'
            for i, m in self.mails.items() if listserv.week(when(m['date']).date()) == week_id)
        return f'<html><body><table>{rows}</table></body></html>'

    def _page(self, mail_id: str) -> str:
        links = ''.join(f'<a href="{escape(attachment_url(mail_id, name)[len(listserv.ARCHIVE):])}">{escape(name)}</a>'
                        for name in self.mails[mail_id].get('files', {}))
        body = f'/scripts/wa.exe?A3=ind2611a&L={LIST}&E=quoted-printable&P=1&B=--x&T=text%2Fplain;%20charset=UTF-8&header=1'
        return f'<html><body><a href="{escape(body)}">text/plain</a>{links}</body></html>'

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.seen.append(url)
        if url in self.down:
            raise httpx.ConnectError('down', request=request)
        if url == listserv.feed_url(LIST):
            return httpx.Response(200, text=self._rss())
        if 'A1=ind' in url:
            return httpx.Response(200, text=self._index(url.split('A1=ind', 1)[1].split('&', 1)[0]))
        if 'A2=' in url:
            mail_id = url.split(';', 1)[1]
            return httpx.Response(200, text=self._page(mail_id)) if mail_id in self.mails else httpx.Response(404)
        name = listserv.attachment_name(url)
        for mail_id, mail in self.mails.items():
            if name in mail.get('files', {}) and url == attachment_url(mail_id, name):
                return httpx.Response(200, content=mail['files'][name])
        return httpx.Response(404)

    def client(self, also=None) -> httpx.Client:
        """A client for the archive; ``also`` answers every other host (a 3GPP mock server's handler)."""
        def route(request):
            if request.url.host == 'list.etsi.org':
                return self.handler(request)
            return also(request) if also else httpx.Response(404)
        return httpx.Client(transport=httpx.MockTransport(route))
