"""
Static site generator.

Copyright (c) 2009 Liam Cooke
Licensed under the terms of the MIT license.

"""
import re
import urlparse
from collections import defaultdict
from datetime import datetime
from os import path

import dateutil.parser
import PyRSS2Gen as rss2
import yaml
from BeautifulSoup import BeautifulSoup
from markdown import markdown

from pilcrow import util


class Page(dict):
    sortkey_origin = lambda self: (util.timestamp(self.date), self.id)
    sortkey_posted = lambda self: (util.timestamp(self.posted or self.date), self.id)

    def __init__(self, site, id, attrs={}, **kwargs):
        dict.__init__(self, {
            'content': '',
            'date': None,
            'posted': None,
            'id': str(id),
            'title': '',
            'template': '',
        })
        self._site = site
        self.update(attrs, **kwargs)

    def __getattr__(self, name):
        return self[name]

    @property
    def url(self):
        id = self.id
        return self._site.join_url(self._site['root'], id != 'index' and id)

    @property
    def full_url(self):
        return self._site['domain'] + self.url


class Content(Page):
    NORM = {
        'date': util.norm_time, 'posted': util.norm_time,
        'tags': util.norm_tags,
        'summary': lambda s: ''.join(BeautifulSoup(markdown(s)).findAll(text=True)),
    }
    SUMMARY = re.compile('(<summary>)(.*?)(</summary>)', re.DOTALL)

    backposted = lambda self: self.posted and self.posted.date() > self.date.date()

    def __init__(self, site, fp):
        id = path.splitext(path.basename(fp.name))[0]
        Page.__init__(self, site, id, modified=util.filemtime(fp), tags=set(), summary='')
        data = fp.read().split('\n\n', 1)
        head = yaml.load(data.pop(0))
        body = data and data.pop() or ''

        for key, val in head.items():
            key = util.norm_key(key)
            self[key] = self.NORM.get(key, util.identity)(val)
        if self.date:
            self.update({
                'id': self._site.join_url(self.date.year, id, ext=False),
                'template': self.template or 'entry',
                'month_name': self.date.strftime('%B'),
                'prevpost': None,
                'nextpost': None,
                'tags_by_count': lambda: sorted(self.tags.values(), key=Tag.sortkey_count),
                'tags_by_name': lambda: sorted(self.tags.values(), key=Tag.sortkey_tag),
            })
        if 'tags' in self._site:
            self['tags'] -= set((tag for tag in self.tags if tag not in self._site['tags']))

        def _summary(m):
            summary = m.group(2).strip()
            self['summary'] = self.NORM['summary'](summary)
            return summary
        self['content'] = markdown(self.SUMMARY.sub(_summary, body).strip())

    def feed_item(self):
        url, title = self.full_url, self.title or 'Untitled'
        if self.backposted():
            title += ' [%s]' % self.date.strftime('%Y-%m-%d')
        tags = [rss2.Category(tag, self._site['home']) for tag in self.tags]

        content = BeautifulSoup(self.content)
        for link in content.findAll('a'):
            link['href'] = urlparse.urljoin(self.full_url, link['href'])

        return rss2.RSSItem(title=title, link=url, guid=rss2.Guid(url),
            description=str(content), pubDate=self.posted or self.date,
            categories=tags, enclosure=self.get('enclosure', None))


class Archive(Page):
    def __init__(self, site, id, entries, year, month, attrs={}):
        id = site.join_url(year, month and '%02d' % month, ext=False)
        Page.__init__(self, site, id, {
            'entries': entries,
            'year': year,
            'month': month,
            'template': 'archive_%s' % (month and 'month' or 'year'),
            'title': month and datetime(year, month, 1).strftime('%B %Y') or year,
        }, **attrs)

class Month(Archive):
    def __init__(self, site, entries, year, month):
        if not (1 <= month <= 12):
            raise ValueError, 'month must be in the range 1-12'
        id = site.join_url(year, '%02d' % month, ext=False)
        Archive.__init__(self, site, id, entries, year, month, {
            'title': datetime(year, month, 1).strftime('%B %Y'),
        })

class Year(Archive):
    def __init__(self, site, entries, year):
        Archive.__init__(self, site, year, entries, year, 0, {
            'title': str(year),
        })


class Tag(Page):
    sortkey_count = lambda self: (-len(self.tagged), self.name)
    sortkey_tag = lambda self: self.name

    def __init__(self, site, tag):
        Page.__init__(self, site, tag, template='tag', tagged={})
        self.name, self['tag'] = tag, tag
        self['title'] = self._site.get('tags', {}).get(tag, tag)

    def add(self, page):
        self['tagged'][page.id] = page
