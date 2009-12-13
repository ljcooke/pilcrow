#!/usr/bin/env python
"""
Static site generator.

Copyright (c) 2009 Liam Cooke
Licensed under the terms of the MIT license.

"""
import codecs, commands, optparse, os, re, shutil, sys, time
from collections import defaultdict
from datetime import datetime
from itertools import izip
from os import path

import dateutil.parser
import PyRSS2Gen as rss2
import yaml
from BeautifulSoup import BeautifulSoup
from mako.exceptions import MakoException
from mako.lookup import TemplateLookup
from markdown import markdown

CONFIG_FILE = 'site.yml'
FILES_ACTIONS = {
    '.less': lambda s, d: run_or_die('lessc %s %s' % (s, d)),
}

site = yaml.load(r"""
    domain: http://localhost/
    root: /
    clean_urls: no
    content_extensions: [text, markdown, mkdn, md]
    dirs:
        content: content
        files: files
        templates: templates
        deploy: deploy
    feed: feed.rss
    files_exclude: "(^\\.|~$)"
    files_include: "^\\.htaccess$"
    files_rename:
        .less: .css
    lang: en
""")


alphanum = lambda s: re.sub('[^A-Za-z0-9]', '', s)
filemtime = lambda f: datetime.fromtimestamp(os.fstat(f.fileno()).st_mtime)
identity = lambda o: o
is_str = lambda o: isinstance(o, basestring)
timestamp = lambda dt: dt and int(time.mktime(dt.timetuple())) or 0

def die(*msg):
    sys.stderr.write(' '.join(str(m) for m in msg) + '\n')
    sys.exit(1)

def run_or_die(cmd):
    status, output = commands.getstatusoutput(cmd)
    if status > 0: die(output)

norm_key = lambda s: re.sub('[- ]+', '_', s.lower())
norm_time = lambda s: s and dateutil.parser.parse(str(s), fuzzy=True) or None
def norm_tags(obj):
    tags = is_str(obj) and obj.split(',' in obj and ',' or None) or obj
    return set(filter(bool, (alphanum(tag) for tag in tags)))

def join_url(*parts, **kwargs):
    ext = (kwargs.get('ext', 1) and not site['clean_urls']) and '.html' or ''
    return re.sub('//+', '/', '/'.join(str(s) for s in parts if s)) + ext
site['join_url'] = join_url

def mkdir(d):
    try: os.mkdir(d)
    except OSError: pass

def neighbours(iterable):
    "1..4 -> (None,1,2), (1,2,3), (2,3,4), (3,4,None)"
    L = list(iterable)
    a = [None] + L[:-1]
    b = L[1:] + [None]
    return izip(a, L, b)


class Page(dict):
    sortkey_origin = lambda self: (timestamp(self.date), self.id)
    sortkey_posted = lambda self: (timestamp(self.posted or self.date), self.id)

    def __init__(self, id, attrs={}, **kwargs):
        dict.__init__(self, {
            'content': '',
            'date': None,
            'posted': None,
            'id': str(id),
            'title': '',
            'template': '',
        })
        self.update(attrs)
        self.update(kwargs)

    def __getattr__(self, name):
        return self[name]

    @property
    def url(self):
        id = self.id
        return join_url(site['root'], id != 'index' and id)

    @property
    def full_url(self):
        return site['domain'] + self.url


class ContentPage(Page):
    NORM = {
        'date': norm_time, 'posted': norm_time,
        'tags': norm_tags,
        'summary': lambda s: ''.join(BeautifulSoup(markdown(s)).findAll(text=True)),
    }
    SUMMARY = re.compile('(<summary>)(.*?)(</summary>)', re.DOTALL)
    backposted = lambda self: self.posted and self.posted.date() > self.date.date()

    def __init__(self, fp):
        id = path.splitext(path.basename(fp.name))[0]
        Page.__init__(self, id, modified=filemtime(fp), tags=set(), summary='')
        data = fp.read().split('\n\n', 1)
        head = yaml.load(data.pop(0))
        body = data and data.pop() or ''

        for key, val in head.items():
            key = norm_key(key)
            self[key] = self.NORM.get(key, identity)(val)
        if self.date:
            self.update({
                'id': join_url(self.date.year, id, ext=False),
                'template': self.template or 'entry',
                'month_name': self.date.strftime('%B'),
                'prevpost': None,
                'nextpost': None,
            })
        if 'tags' in site:
            self['tags'] -= set((tag for tag in self.tags if tag not in site['tags']))

        def _summary(m):
            summary = m.group(2).strip()
            self['summary'] = ContentPage.NORM['summary'](summary)
            return summary
        self['content'] = markdown(self.SUMMARY.sub(_summary, body).strip())

    def feed_item(self):
        url, title = self.full_url, self.title or 'Untitled'
        if self.backposted():
            title += ' [%s]' % self.date.strftime('%Y-%m-%d')
        tags = [rss2.Category(tag, site['home']) for tag in self.tags]
        return rss2.RSSItem(title=title, link=url, guid=rss2.Guid(url),
            description=self.content, pubDate=self.posted or self.date,
            categories=tags, enclosure=self.get('enclosure', None))


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


class TagPage(Page):
    sortkey_count = lambda self: (-len(self.tagged), self.name)
    sortkey_tag = lambda self: self.name

    def __init__(self, tag):
        Page.__init__(self, tag, template='tag', tagged={})
        self.name, self['tag'] = tag, tag

    def add(self, page):
        self['tagged'][page.id] = page

    @property
    def full_name(self):
        return site.get('tags', {}).get(self.name, self.name)


class PageManager:
    tags_by_count = lambda self: sorted(self.tags.values(), key=TagPage.sortkey_count)
    tags_by_name = lambda self: sorted(self.tags.values(), key=TagPage.sortkey_tag)

    def __init__(self):
        self.pages, self.tags = {}, {}
        tdir = site['dirs']['templates']
        self.lookup = TemplateLookup(directories=[tdir], input_encoding='utf-8')

    def __getitem__(self, id):
        return self.pages[id]

    def __iter__(self):
        return iter(self.pages.values())

    def add(self, page):
        if page.id in self.pages:
            die('duplicate page id: %s' % page.id)
        self.pages[page.id] = page

        if type(page) is TagPage:
            self.tags[page.name] = page
        elif 'tags' in page:
            page_tags = {}
            for tag_name in page.get('tags', []):
                if tag_name in self.tags:
                    tag = self.tags[tag_name]
                else:
                    tag = TagPage(tag_name)
                    self.add(tag)
                tag.add(page)
                page_tags[tag_name] = tag
            page['tags'] = page_tags

    def select(self, limit=None, dated=True, tag=None, chrono=False, sortby_origin=None):
        if sortby_origin is None:
            sortby_origin = bool(chrono)
        sortkey = sortby_origin and Page.sortkey_origin or Page.sortkey_posted
        results = sorted(self.pages.values(), key=sortkey, reverse=not chrono)
        if dated:
            results = [page for page in results if page.date]
            if tag:
                results = [page for page in results if tag in page.tags]
        return tuple(results)[:limit]

    def render(self):
        for page in self:
            t = page.template or site['default_template']
            template = self.lookup.get_template('%s.html' % t)
            print '%14s : /%s' % (t, page.id)

            vars = dict(site, **page)
            if vars['title']:
                vars['head_title'] = vars['title_format'] % vars
            try:
                html = template.render_unicode(**vars).strip()
                fname = path.join(site['dirs']['deploy'], page.id) + '.html'
                with open(fname, 'w') as f:
                    f.write(html.encode('utf-8'))
            except NameError:
                die('template error: undefined variable in', template.filename)


def build(site_path, clean=False):
    try: os.chdir(site_path)
    except OSError: die('invalid path:', site_path)
    if not path.exists(CONFIG_FILE):
        die('%s not found' % CONFIG_FILE)

    with open(CONFIG_FILE) as f:
        for k, v in yaml.load(f).items():
            k = norm_key(k)
            if type(v) is dict:
                site[k] = dict(site.get(k, {}), **v)
            else:
                site[k] = v

    base_path = path.realpath(os.curdir)
    deploy_path = path.realpath(site['dirs']['deploy'])
    if clean:
        shutil.rmtree(deploy_path, ignore_errors=True)
        mkdir(deploy_path)

    os.chdir(site['dirs']['files'])
    excludes, includes = re.compile(site['files_exclude']), re.compile(site['files_include'])
    for root, _, files in os.walk(os.curdir):
        mkdir(path.normpath(path.join(deploy_path, root)))
        for fname in files:
            if excludes.match(fname) and not includes.match(fname):
                continue
            src, dest = path.join(root, fname), path.join(deploy_path, root, fname)
            ext = path.splitext(fname)[1]
            if ext in site['files_rename']:
                dest = path.splitext(dest)[0] + site['files_rename'][ext]
            if path.isfile(dest) and path.getmtime(src) <= path.getmtime(dest):
                continue
            FILES_ACTIONS.get(ext, shutil.copy2)(src, dest)
            print '%s => %s' % (path.relpath(src, base_path), path.relpath(dest, base_path))
    os.chdir(base_path)

    pages, years = PageManager(), defaultdict(list)
    for root, _, files in os.walk(site['dirs']['content']):
        exts = ['.%s' % ext for ext in site['content_extensions']]
        for file in filter(lambda f: path.splitext(f)[1] in exts, files):
            with codecs.open(path.join(root, file), 'r', encoding='utf-8') as fp:
                page = ContentPage(fp)
                pages.add(page)
                if page.date:
                    years[page.date.year].append(page)

    for year, posts in sorted(years.items()):
        posts = sorted(posts, key=Page.sortkey_origin)
        pages.add(ArchivePage(posts, year))
        for prevpost, post, nextpost in neighbours(posts):
            post['prevpost'], post['nextpost'] = prevpost, nextpost

    dirs = filter(bool, [os.path.dirname(p.id) for p in pages])
    for d in sorted(set(dirs)):
        mkdir(os.path.join(deploy_path, d))

    site.update({
        'get': lambda id: pages[str(id)],
        'pages': pages.select,
        'domain': site['domain'].rstrip('/'),
        'root': '/' + site.get('root', '').lstrip('/'),
        'head_title': site.get('site_title', ''),
        'site_tags': pages.tags,
        'tags_by_count': pages.tags_by_count,
        'tags_by_name': pages.tags_by_name,
        'years': sorted(years.keys()),
        'default_template': site.get('default_template', 'page'),
    })
    site['home'] = site['domain'] + site['root']
    try: pages.render()
    except MakoException as e: die('template error:', e)

    if site['feed']:
        feed_posts = pages.select(10)
        feed_date = feed_posts[0].posted or feed_posts[0].date
        feed = rss2.RSS2(items=[p.feed_item() for p in feed_posts],
            title=site['site_title'], description=site.get('description', ''),
            link=site['domain'] + site['root'], generator='Pilcrow',
            language=site['lang'], lastBuildDate=feed_date)
        with open(path.join(deploy_path, site['feed']), 'w') as f:
            feed.write_xml(f, 'utf-8')

if __name__ == '__main__':
    parser = optparse.OptionParser()
    parser.add_option('-x', '--clean', action='store_true', default=False)
    options, args = parser.parse_args()
    build(args and args[0] or '.', clean=options.clean)
