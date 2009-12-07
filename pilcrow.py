#!/usr/bin/env python
"""
Static site generator.

Copyright (c) 2009 Liam Cooke
Licensed under the terms of the MIT license.

"""
import codecs
import os
import re
import shutil
import sys
from collections import defaultdict
from commands import getstatusoutput
from datetime import datetime
from itertools import izip
from optparse import OptionParser
from os import path

import dateutil.parser
import yaml
from mako.exceptions import MakoException
from mako.lookup import TemplateLookup
from markdown import markdown

CONTENT_EXTS = ('.text', '.markdown', '.mkdn', '.md')
CONFIG_FILE = 'site.yml'
DEPLOY_DIR = 'deploy'
CONTENT_DIR = 'content'
FILES_DIR = 'files'
TEMPLATES_DIR = 'templates'
REQUIRED_FILES = (CONFIG_FILE, CONTENT_DIR, TEMPLATES_DIR)

FILES_EXCLUDE = re.compile(r'(^\.|~$)')
FILES_INCLUDE = re.compile(r'^\.htaccess$')
FILES_RENAME = {'.less': '.css'}
FILES_ACTION = {
    '.less': lambda s, d: run_or_die('lessc %s %s' % (s, d)),
}

CONTEXT = {
    'clean_urls': False,
}

alphanum = lambda s: re.sub('[^A-Za-z0-9]', '', s)
filemtime = lambda f: datetime.fromtimestamp(os.fstat(f.fileno()).st_mtime)
identity = lambda o: o
is_str = lambda o: isinstance(o, basestring)

def die(*msg):
    msg = ' '.join(str(m) for m in msg) + '\n'
    yellow = '\033[93m%s\033[00m'
    sys.stderr.write(re.sub('^(.*?:)', yellow % r'\1', msg))
    sys.exit(1)

def run_or_die(cmd):
    status, output = getstatusoutput(cmd)
    if status > 0: die(output)

norm_time = lambda s: s and dateutil.parser.parse(str(s), fuzzy=True) or None
def norm_tags(obj):
    tags = is_str(obj) and obj.split(',' in obj and ',' or None) or obj
    return tuple(filter(bool, (alphanum(tag) for tag in tags)))

def join_url(*parts, **kwargs):
    ext = (kwargs.get('ext', 1) and not CONTEXT['clean_urls']) and '.html' or ''
    return re.sub('//+', '/', '/'.join(str(s) for s in parts if s)) + ext

def mkdir(d):
    try: os.mkdir(d)
    except OSError: pass  # ignore errors if directory exists

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
        id = self.id
        return join_url(CONTEXT['root'], id != 'index' and id)

class ContentPage(Page):
    NORM = {
        'date': norm_time, 'posted': norm_time, 'started': norm_time,
        'tags': norm_tags, 'category': norm_tags,
        'summary': markdown,
    }
    SUMMARY = re.compile('(<summary>)(.*?)(</summary>)', re.DOTALL)

    def __init__(self, fp):
        id = path.splitext(path.basename(fp.name))[0]
        Page.__init__(self, id, modified=filemtime(fp))
        data = fp.read().split('\n\n', 1)
        head = yaml.load(data.pop(0))
        body = data and data.pop() or ''

        for key, val in head.items():
            self[key] = self.NORM.get(key, identity)(val)
        if self.date:
            self.update({
                'id': join_url(self.date.year, id, ext=False),
                'template': self.template or 'entry',
                'posted': self.get('posted', None),
                'month_name': self.date.strftime('%B'),
                'prevpost': None,
                'nextpost': None,
            })

        def _summary(m):
            summary = m.group(2).strip()
            self['summary'] = markdown(summary)
            return summary
        self['content'] = markdown(self.SUMMARY.sub(_summary, body).strip())

class ArchivePage(Page):

    def __init__(self, entries, year, month=0):
        id = join_url(year, month and '%02d' % month, ext=False)
        Page.__init__(self, id, {
            'entries': entries,
            'year': year,
            'month': month,
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

    def __getitem__(self, id):
        return self.pages[id]

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
                with open(path.join(DEPLOY_DIR, page.id) + '.html', 'w') as f:
                    f.write(html.encode('utf-8'))
            except NameError:
                die('template error: undefined variable in', template.filename)

CONTEXT.update({
    'join_url': join_url,
})

def build(site_path, clean=False):
    try: os.chdir(site_path)
    except OSError: die('invalid path:', site_path)

    base_path = path.realpath(os.curdir)
    deploy_path = path.realpath(DEPLOY_DIR)

    if any(f for f in REQUIRED_FILES if not path.exists(f)):
        die('required files/folders: %s' % ', '.join(REQUIRED_FILES))

    global CONTEXT
    with open(CONFIG_FILE) as f:
        CONTEXT.update(yaml.load(f))

    if clean:
        shutil.rmtree(deploy_path, ignore_errors=True)
        mkdir(deploy_path)

    os.chdir(FILES_DIR)
    for root, _, files in os.walk(os.curdir):
        mkdir(path.normpath(path.join(deploy_path, root)))
        for fname in files:
            if FILES_EXCLUDE.match(fname) and not FILES_INCLUDE.match(fname):
                continue
            src, dest = path.join(root, fname), path.join(deploy_path, root, fname)
            ext = path.splitext(fname)[1]
            if ext in FILES_RENAME:
                dest = path.splitext(dest)[0] + FILES_RENAME[ext]
            if path.isfile(dest) and path.getmtime(src) <= path.getmtime(dest):
                continue
            FILES_ACTION.get(ext, shutil.copy2)(src, dest)
            print '%s => %s' % (path.relpath(src, base_path), path.relpath(dest, base_path))
    os.chdir(base_path)

    pages, years = PageManager(), defaultdict(list)
    for root, _, files in os.walk(CONTENT_DIR):
        for file in filter(lambda f: path.splitext(f)[1] in CONTENT_EXTS, files):
            with codecs.open(path.join(root, file), 'r', encoding='utf-8') as fp:
                page = ContentPage(fp)
                pages.add(page)
                if page.date:
                    years[page.date.year].append(page)

    for year, posts in sorted(years.items()):
        mkdir(path.join(DEPLOY_DIR, str(year)))
        posts = sorted(posts)
        pages.add(ArchivePage(posts, year))
        for prevpost, post, nextpost in neighbours(posts):
            post['prevpost'], post['nextpost'] = prevpost, nextpost

    def select(limit=None, dated=True, chrono=False):
        results = pages.all()
        if not chrono: results.reverse()
        if dated: results = [page for page in results if page.date]
        return tuple(results)[:limit]

    CONTEXT.update({
        'get': lambda id: pages[str(id)],
        'pages': select,
        'domain': CONTEXT['domain'].rstrip('/'),
        'root': '/' + CONTEXT.get('root', '').lstrip('/'),
        'head_title': CONTEXT.get('site_title', ''),
        'years': sorted(years.keys()),
        'default_template': CONTEXT.get('default_template', 'page'),
    })
    try: pages.render()
    except MakoException as e: die('template error:', e)

if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option('-x', '--clean', action='store_true', default=False)
    options, args = parser.parse_args()
    build(args and args[0] or '.', clean=options.clean)
