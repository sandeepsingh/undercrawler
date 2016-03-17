import re
import contextlib
from datetime import datetime
import hashlib

import autopager
import formasaurus
import scrapy
from scrapy.linkextractors import LinkExtractor
from scrapy.utils.url import canonicalize_url
from scrapy.utils.python import unique

from ..items import CDRItem


class BaseSpider(scrapy.Spider):
    name = 'base'

    def __init__(self, url, *args, **kwargs):
        if url.startswith('.'):
            with open(url) as f:
                start_urls = [line.strip() for line in f]
        else:
            start_urls = [url]
        self.start_urls = [self._normalize_url(_url) for _url in start_urls]
        allow = [self._start_url_re(_url) for _url in self.start_urls]
        self.link_extractor = LinkExtractor(allow=allow)
        self.iframe_link_extractor = LinkExtractor(allow=allow, tags=['iframe'],
                                                   attrs=['src'])
        super().__init__(*args, **kwargs)

    def start_requests(self):
        for url in self.start_urls:
            yield self.splash_request(url)

    def splash_request(self, url, callback=None, **kwargs):
        callback = callback or self.parse
        return scrapy.Request(url, callback=callback, **kwargs)

    def parse(self, response):
        url = response.url
        if not self.link_extractor.matches(url):
            return

        forms = formasaurus.extract_forms(response.text) if response.text \
                else []
        yield self.cdr_item(response, dict(
            is_page=response.meta.get('is_page', False),
            is_onclick=response.meta.get('is_onclick', False),
            is_iframe=response.meta.get('is_iframe', False),
            depth=response.meta.get('depth', None),
            forms=[meta for _, meta in forms],
            ))

        if self.settings.getbool('PREFER_PAGINATION'):
            # Follow pagination links; pagination is not a subject of
            # a max depth limit. This also prioritizes pagination links because
            # depth is not increased for them.
            with _dont_increase_depth(response):
                for url in self._pagination_urls(response):
                    # self.logger.debug('Pagination link found: %s', url)
                    yield self.splash_request(url, meta={'is_page': True})

        # Follow all in-domain links.
        # Pagination requests are sent twice, but we don't care because
        # they're be filtered out by a dupefilter.
        for link in self.link_extractor.extract_links(response):
            yield self.splash_request(link.url)

        # urls extracted from onclick handlers
        for url in get_js_links(response):
            priority = 0 if _looks_like_url(url) else -15
            url = response.urljoin(url)
            yield self.splash_request(url, meta={'is_onclick': True},
                                      priority=priority)

        # go to iframes
        for link in self.iframe_link_extractor.extract_links(response):
            yield self.splash_request(link.url, meta={'is_iframe': True})

    def cdr_item(self, response, metadata):
        url = response.url
        timestamp = int(datetime.utcnow().timestamp() * 1000)
        return CDRItem(
            _id=hashlib.sha256('{}-{}'.format(url, timestamp).encode('utf-8'))\
                .hexdigest().upper(),
            content_type=response.headers['content-type']\
                .decode('ascii', 'ignore'),
            crawler=self.settings.get('CDR_CRAWLER'),
            extracted_metadata=metadata,
            extracted_text='\n'.join(
                response.xpath('//body').xpath('string()').extract()),
            raw_content=response.text,
            team=self.settings.get('CDR_TEAM'),
            timestamp=timestamp,
            url=url,
            version=2.0,
        )

    def _normalize_url(self, url):
        if not url.startswith('http'):
            url = 'http://' + url
        return url

    def _start_url_re(self, url):
        http_www = r'^https?://(www\.)?'
        return re.compile(http_www + re.sub(http_www, '', url), re.I)

    def _pagination_urls(self, response):
        return [
            url for url in
            unique(canonicalize_url(url) for url in autopager.urls(response))
            if self.link_extractor.matches(url)
        ]


@contextlib.contextmanager
def _dont_increase_depth(response):
    # XXX: a hack to keep the same depth for outgoing requests
    response.meta['depth'] -= 1
    try:
        yield
    finally:
        response.meta['depth'] += 1



_onclick_search = re.compile("(?P<sep>('|\"))(?P<url>.+?)(?P=sep)").search

def get_onclick_url(attr_value):
    """
    >>> get_onclick_url("window.open('page.html?productid=23','win2')")
    'page.html?productid=23'
    >>> get_onclick_url("window.location.href='http://www.jungleberry.co.uk/Fair-Trade-Earrings/Aguas-Earrings.htm'")
    'http://www.jungleberry.co.uk/Fair-Trade-Earrings/Aguas-Earrings.htm'
    """
    m = _onclick_search(attr_value)
    return m.group("url").strip() if m else m


def get_js_links(response):
    """ Extract URLs from JS. """
    urls = [
        get_onclick_url(value)
        for value in response.xpath('//*/@onclick').extract()
    ]
    # TODO: extract all URLs from <script> tags as well?
    return [url for url in urls if url]


def _looks_like_url(txt):
    """
    Return True if text looks like an URL (probably relative).
    >>> _looks_like_url("foo.bar")
    False
    >>> _looks_like_url("http://example.com")
    True
    >>> _looks_like_url("/page2")
    True
    >>> _looks_like_url("index.html")
    True
    >>> _looks_like_url("foo?page=1")
    True
    >>> _looks_like_url("x='what?'")
    False
    >>> _looks_like_url("visit this page?")
    False
    >>> _looks_like_url("?")
    False
    """
    if " " in txt or "\n" in txt:
        return False
    if "/" in txt:
        return True
    if re.search(r'\?\w+=.+', txt):
        return True
    if re.match(r"\w+\.html", txt):
        return True
    return False
