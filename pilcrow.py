#!/usr/bin/env python
"""
Static site generator.

"""
import codecs
import os
import re
import shutil
import sys
import yaml
from collections import defaultdict
from datetime import datetime
from itertools import izip

import dateutil.parser
from mako.exceptions import MakoException
from mako.lookup import TemplateLookup
from markdown import markdown


CONTENT_EXTS = ('.text', '.markdown', '.mkdn', '.md')
CONFIG_FILE = 'site.yml'
DEPLOY_DIR = 'deploy'
CONTENT_DIR = 'content'
STATIC_DIR = 'static'
TEMPLATES_DIR = 'templates'
REQUIRED_FILES = (CONFIG_FILE, CONTENT_DIR, TEMPLATES_DIR)

CONTEXT = {}


def die(*msg):
    msg = ' '.join(str(m) for m in msg) + '\n'
    yellow = '\033[93m%s\033[00m'
    sys.stderr.write(re.sub('^(.*?:)', yellow % r'\1', msg))
    sys.exit(1)

alphanum = lambda s: re.sub('[^A-Za-z0-9]', '', s)
identity = lambda o: o
is_str = lambda o: isinstance(o, basestring)
mtime = lambda f: datetime.fromtimestamp(os.fstat(f.fileno()).st_mtime)
pluralize = lambda n, s: '%d %s%s' % (n, s, n != 1 and 's' or '')

norm_time = lambda s: s and dateutil.parser.parse(str(s), fuzzy=True) or None
def norm_tags(obj):
    tags = is_str(obj) and obj.split(',' in obj and ',' or None) or obj
    return tuple(filter(bool, (alphanum(tag) for tag in tags)))

def join_url(*parts):
    return '/'.join(str(s) for s in parts if s)

def neighbours(iterable):
    "1..4 -> (None,1,2), (1,2,3), (2,3,4), (3,4,None)"
    L = list(iterable)
    a = [None] + L[:-1]
    b = L[1:] + [None]
    return izip(a, L, b)


class Page(dict):
    _sortkey = lambda self: '%s %s' % (self.date or '0000-00-00', self.id)
    __cmp__ = lambda self, obj: cmp(self._sortkey(), obj._sortkey())

    def __init__(self, id, items={}, **kwargs):
        dict.__init__(self, {
            'date': None,
            'title': '',
            'template': '',
        })
        self['id'] = str(id)
        self.update(items)
        self.update(kwargs)

    def __getattr__(self, name):
        return self[name]

    @property
    def url(self):
        return CONTEXT['root'] + self.id


class ContentPage(Page):
    NORM = {
        'date': norm_time, 'posted': norm_time, 'started': norm_time,
        'tags': norm_tags, 'category': norm_tags,
    }
    SUMMARY = re.compile('(<summary>)(.*?)(</summary>)', re.DOTALL)

    def __init__(self, fp):
        id = os.path.splitext(os.path.basename(fp.name))[0]
        Page.__init__(self, id, modified=mtime(fp))
        data = fp.read().split('\n\n', 1)
        head = yaml.load(data.pop(0))
        body = data and data.pop() or ''

        for key, val in head.items():
            self[key] = self.NORM.get(key, identity)(val)
        if self.date:
            self.update({
                'id': join_url(self.date.year, id),
                'template': self.template or 'entry',
                'month_name': self.date.strftime('%B'),
            })
            if 'posted' not in self:
                self['posted'] = self.date

        # extract a summary from the <summary> pseudo-tag, if any
        def _summary(m):
            summary = m.group(2).strip()
            self['summary'] = markdown(summary)
            return summary
        self['content'] = markdown(self.SUMMARY.sub(_summary, body).strip())


class ArchivePage(Page):

    def __init__(self, entries, year, month=0):
        id = join_url(year, month and '%02d' % month)
        Page.__init__(self, id, {
            'entries': entries,
            'template': 'archive_%s' % (month and 'month' or 'year'),
            'title': month and datetime(year, month, 1).strftime('%B %Y') or year,
        })


class PageManager:

    def __init__(self):
        self.pages = {}
        self.lookup = TemplateLookup(directories=[TEMPLATES_DIR], input_encoding='utf-8')

    def add(self, page):
        if page.id in self.pages:
            die('duplicate page id: %s' % page.id)
        self.pages[page.id] = page

    @property
    def all(self):
        return self.pages.values()

    def render(self):
        for page in sorted(self.pages.values()):
            t = page.template or CONTEXT['default_template']
            template = self.lookup.get_template('%s.html' % t)
            print '%14s : /%s' % (t, page.id)

            context = dict(CONTEXT.items() + page.items())
            if context['title']:
                context['head_title'] = context['title_format'] % context
            try:
                html = template.render_unicode(**context).strip()
                with open(os.path.join(DEPLOY_DIR, page.id) + '.html', 'w') as f:
                    f.write(html.encode('utf-8'))
            except NameError as err:
                die('template error: undefined variable in', template.filename)


def build(path):
    try: os.chdir(path)
    except OSError: die('invalid path:', path)

    if any(f for f in REQUIRED_FILES if not os.path.exists(f)):
        die('required files/folders: %s' % ', '.join(REQUIRED_FILES))

    # read config
    global CONTEXT
    with open(CONFIG_FILE) as f:
        CONTEXT = yaml.load(f)
    CONTEXT.update({
        'domain': CONTEXT['domain'].rstrip('/'),
        'root': '/' + CONTEXT.get('root', '').lstrip('/'),
        'head_title': CONTEXT.get('site_title', ''),
        'default_template': CONTEXT.get('default_template', 'page'),
    })

    # clean /deploy and copy static files into it
    shutil.rmtree(DEPLOY_DIR, ignore_errors=True)
    if os.path.exists(STATIC_DIR):
        shutil.copytree(STATIC_DIR, DEPLOY_DIR)
    else:
        os.mkdir(DEPLOY_DIR)

    # parse content files
    pages, years = PageManager(), defaultdict(list)
    for root, _, files in os.walk(CONTENT_DIR):
        for file in filter(lambda f: os.path.splitext(f)[1] in CONTENT_EXTS, files):
            with codecs.open(os.path.join(root, file), 'r', encoding='utf-8') as fp:
                page = ContentPage(fp)
                pages.add(page)
                if page.date:
                    years[page.date.year].append(page)

    # prepare archives
    for year, posts in sorted(years.items()):
        os.mkdir(os.path.join(DEPLOY_DIR, str(year)))
        posts = sorted(posts)
        pages.add(ArchivePage(posts, year))
        for prevpost, post, nextpost in neighbours(posts):
            post['prev'], post['next'] = prevpost, nextpost

    def select(limit=None, dated=True, chrono=False):
        results = pages.all
        if chrono: results.reverse()
        if dated: results = [page for page in results if page.date]
        return tuple(results)[:limit]
    CONTEXT['pages'] = select

    try:
        pages.render()
    except MakoException as err:
        die('template error:', err)

if __name__ == '__main__':
    build(sys.argv[1:] and sys.argv[1] or '.')
