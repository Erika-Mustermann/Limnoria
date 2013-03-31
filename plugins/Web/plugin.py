# -*- coding: utf8 -*-
###
# Copyright (c) 2005, Jeremiah Fincher
# Copyright (c) 2009, James McCoy
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import os
import re
import sqlite3
import urlparse
#from urllib import quote
from contextlib import closing
import supybot.conf as conf
import supybot.ircdb as ircdb
import supybot.utils as utils
from supybot.commands import *
import supybot.ircmsgs as ircmsgs
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
# TODO: Make BeautifulSoup 4 optional through config?
try:
    from bs4 import BeautifulSoup
except ImportError:
    raise callbacks.Error, 'You need to have Beautiful Soup 4 installed ' \
                           'to use this plugin.  Download it at ' \
                           '<https://pypi.python.org/pypi/beautifulsoup4>'
try:
    from supybot.i18n import PluginInternationalization, \
                             internationalizeDocstring
    _ = PluginInternationalization('MessageParser')
except:
    # This are useless functions that's allow to run the plugin on a bot
    # without the i18n plugin
    _ = lambda x:x
    internationalizeDocstring = lambda x:x


class Web(callbacks.PluginRegexp, plugins.ChannelDBHandler):
    """Add the help for "@help Web" here."""
    threaded = True
    flags = re.IGNORECASE|re.VERBOSE
    regexps = ['titleSnarfer']
    def __init__(self, irc):
        self.__parent = super(Web, self)
        self.__parent.__init__(irc)
        callbacks.PluginRegexp.__init__(self, irc)
        plugins.ChannelDBHandler.__init__(self)

    def makeDb(self, filename):
        """Create the database and connect to it."""
        if os.path.exists(filename):
            db = sqlite3.connect(filename)
            db.text_factory = str
            return db
        db = sqlite3.connect(filename)
        db.text_factory = str
        cursor = db.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS urls (
                          url TEXT PRIMARY KEY,
                          nick TEXT,
                          channel TEXT,
                          message TEXT,
                          timestamp TIMESTAMP
                          )""")
        db.commit()
        return db

    def _updateDB(self, msg, url):
        """Record when this URL was seen."""
        if url.endswith('/'):
            url = url[:-1]
        if ircmsgs.isAction(msg):
            text = ircmsgs.unAction(msg)
        else:
            text = msg.args[1]
        db = self.getDb(msg.args[0])
        cursor = db.cursor()
        linked = cursor.execute("""SELECT nick, channel, message, timestamp
                                   FROM urls WHERE url = ?""",
                                   (url,)).fetchone()
        if linked:
            return {'nick': linked[0], 'channel': linked[1],
                    'timestamp': linked[3]}
        else:
            cursor.execute("""INSERT OR IGNORE INTO urls (url, nick, channel,
                              message, timestamp) VALUES (?,?,?,?,?)""",
                              (url, msg.nick, msg.args[0], text, msg.receivedAt))
            db.commit()
            self.log.debug('Adding %u to db.', url)
            return None

    def _countDB(self, channel):
        """Return number of URLs in database"""
        db = self.getDb(channel)
        cursor = db.cursor()
        rowCount = cursor.execute('SELECT COUNT(*) FROM urls').fetchone()
        return rowCount[0]

    def callCommand(self, command, irc, msg, *args, **kwargs):
        try:
            super(Web, self).callCommand(command, irc, msg, *args, **kwargs)
        except utils.web.Error, e:
            irc.reply(str(e))

    _WebRe = r"""
              \b
              (                       # Capture 1: entire matched URL
              (?:
                  https?://               # http or https protocol
                  |                       #   or
                  www\d{0,3}[.]           # "www.", "www1.", "www2." … "www999."
                  |                           #   or
                  [a-z0-9.\-]+[.][a-z]{2,4}/  # looks like domain name followed by a slash
              )
              (?:                       # One or more:
                  [^\s()<>]+                  # Run of non-space, non-()<>
                  |                           #   or
                  \(([^\s()<>]+|(\([^\s()<>]+\)))*\)  # balanced parens, up to 2 levels
              )+
              (?:                       # End with:
                  \(([^\s()<>]+|(\([^\s()<>]+\)))*\)  # balanced parens, up to 2 levels
                  |                               #   or
                  [^\s`!()\[\]{};:'".,<>?«»“”‘’]        # not a space or one of these punct chars
              )
              )"""

    # TODO: More accurate times, but may not be worth effort
    def elapsed(current_time, timestamp):
        """Returns a nice approximation of elapsed time"""
        secs = int(current_time-timestamp)
        if secs < 60:
            return '%ds' % secs
        elif secs < 3600:
            return '%dm' % (secs // 60)
        elif secs < 86400:
            return '%dh' % (secs // 3600)
        else:
            return '%dd' % (secs // 86400)

    # TODO: add code that would be common to titleSnarfer and @title
    def _urlParser(self, msg, url):
        pass

    def titleSnarfer(self, irc, msg, match):
        channel = msg.args[0]
        if not irc.isChannel(channel):
            return
        if callbacks.addressed(irc.nick, msg):
            return
        if not self.registryValue('titleSnarfer', channel):
            return
        url = urlparse.urlsplit(match.group(0)).geturl() # lowercases scheme
        r = self.registryValue('nonSnarfingRegexp', channel)
        if r and r.search(url):
            self.log.debug('Not titleSnarfing %q.', url)
            return

        size = conf.supybot.protocols.http.peekSize()
        headers = {'User-Agent':'Mozilla/5.0 (Windows NT 6.1; rv:20.0) \
                    Gecko/20100101 Firefox/20.0'}
        if not url.startswith(('http://', 'https://')):
            url = 'http://%s' % url # prevents dupes in db & errors in netloc
        urlP = urlparse.urlsplit(url)
        url = urlP.geturl()
        origDomain = urlP.hostname
        try:
            url = url.decode('ascii')
        except UnicodeDecodeError:  # Process unicode URLs into IDN
            # netloc2 = [username] @ [password] : <hostname> : [port]
            netloc2 = ''
            if (urlP.username):
                netloc2 =  '%s@' % urlP.username
                if (urlP.password):
                    netloc2 =  '%s:%s@' % (netloc2[:-1], urlP.password)
            netloc2 = netloc2 + urlP.hostname.decode('utf8').encode('idna')
            if (urlP.port):
                netloc2 += ':%s' % urlP.port
            url = urlparse.urlunsplit((urlP.scheme, netloc2, urlP.path,
                                      urlP.query, urlP.fragment))
            self.log.debug('Unicode URL: %u', url)

        # TODO: add support for auth to getUrlFd
        with closing(utils.web.getUrlFd(url, headers)) as urlFd:
            contType = urlFd.info().gettype()
            title = None
            if contType in ('text/html', 'text/xml', 'application/xml',
                            'application/xhtml+xml'):
                title = BeautifulSoup(urlFd.read(size)).title.string
                contType = 'html'
            destUrl = urlFd.geturl()
            destDomain = urlparse.urlsplit(destUrl).hostname
        # after with closing() = only links that resolve are added to db
        linked = self._updateDB(msg, url)

        if title:
            # removes possible new lines and white spaces
            title = (' '.join(title.split())).encode('utf8')
            # TODO: give option for title string length limit
            title = '{0}{1}'.format(title[:150], (title[150:] and '...'))
            if origDomain == destDomain:
#                s = 'Title: %s' % title    # Doesn't require u''
                s = 'Title: {0}'.format(title)
            else:
                s = 'Title: {0} ({1})'.format(title, destDomain)
        else:
            if contType != 'html':
                if origDomain == destDomain:
                    self.log.info('Couldn\'t snarf title of %u: %s.', url,
                                  'URL is not a webpage')
                    return
                else:
                    s = 'Redirects to: {0}'.format(destUrl)
            else:
                s = 'Untitled ({0})'.format(destDomain)
        irc.reply(s, prefixNick=False)
        if linked:
            elapsedTime = elapsed(msg.receivedAt, linked['timestamp'])
            irc.reply(('(First linked by %s in %s, %s ago.)' % (linked['nick'],
                       linked['channel'], elapsedTime)), prefixNick=False)
#            self.log.debug('Msg: %s' % linked['message'])
    titleSnarfer = urlSnarfer(titleSnarfer)
    titleSnarfer.__doc__ = _WebRe

    @internationalizeDocstring
    def headers(self, irc, msg, args, url):
        """<url>

        Returns the HTTP headers of <url>.  Only HTTP urls are valid, of
        course.
        """
        # TODO: replace try/except with "with closing()"
        fd = utils.web.getUrlFd(url)
        try:
            s = ', '.join([format(_('%s: %s'), k, v)
                           for (k, v) in fd.headers.items()])
            irc.reply(s)
        finally:
            fd.close()
    headers = wrap(headers, ['httpUrl'])

    _doctypeRe = re.compile(r'(<!DOCTYPE[^>]+>)', re.M)
    @internationalizeDocstring
    def doctype(self, irc, msg, args, url):
        """<url>

        Returns the DOCTYPE string of <url>.  Only HTTP urls are valid, of
        course.
        """
        size = conf.supybot.protocols.http.peekSize()
        s = utils.web.getUrl(url, size=size) \
                        .decode('utf8')
        m = self._doctypeRe.search(s)
        if m:
            s = utils.str.normalizeWhitespace(m.group(0))
            irc.reply(s)
        else:
            irc.reply(_('That URL has no specified doctype.'))
    doctype = wrap(doctype, ['httpUrl'])

    @internationalizeDocstring
    def size(self, irc, msg, args, url):
        """<url>

        Returns the Content-Length header of <url>.  Only HTTP urls are valid,
        of course.
        """
        # TODO: replace try/except with "with closing()"
        fd = utils.web.getUrlFd(url)
        try:
            try:
                size = fd.headers['Content-Length']
                irc.reply(format(_('%u is %S long.'), url, int(size)))
            except KeyError:
                size = conf.supybot.protocols.http.peekSize()
                s = fd.read(size)
                if len(s) != size:
                    irc.reply(format(_('%u is %S long.'), url, len(s)))
                else:
                    irc.reply(format(_('The server didn\'t tell me how long %u '
                                     'is but it\'s longer than %S.'),
                                     url, size))
        finally:
            fd.close()
    size = wrap(size, ['httpUrl'])

    @internationalizeDocstring
    def title(self, irc, msg, args, optlist, url):
        """[--no-filter] <url>

        Returns the HTML <title>...</title> of a URL.
        If --no-filter is given, the bot won't strip special chars (action,
        DCC, ...).
        """
        # TODO: rewrite to support _urlParser()
        size = conf.supybot.protocols.http.peekSize()
        text = utils.web.getUrl(url, size=size)
        try:
            text = text.decode('utf8', 'replace')
        except:
            pass
        parser = Title()
        try:
            parser.feed(text)
        except HTMLParser.HTMLParseError:
            self.log.debug('Encountered a problem parsing %u.  Title may '
                           'already be set, though', url)
        if parser.title:
            title = utils.web.htmlToText(parser.title.strip())
            if not [y for x,y in optlist if x == 'no-filter']:
                for i in range(1, 4):
                    title = title.replace(chr(i), '')
            irc.reply(title)
        elif len(text) < size:
            irc.reply(_('That URL appears to have no HTML title.'))
        else:
            irc.reply(format(_('That URL appears to have no HTML title '
                             'within the first %S.'), size))
    title = wrap(title, [getopts({'no-filter': ''}), 'httpUrl'])

    @internationalizeDocstring
    def stats(self, irc, msg, args, channel):
        """[<channel>]

        Returns the number of URLs in the Web database.  <channel> is only
        required if the message isn't sent in the channel itself.
        """
        count = self._countDB(channel)
        irc.reply(format(_('I have %n in my database.'), (count, 'link')))
    stats = wrap(stats, ['channeldb'])
    
    @internationalizeDocstring
    def urlquote(self, irc, msg, args, text):
        """<text>

        Returns the URL quoted form of the text.
        """
        irc.reply(utils.web.urlquote(text))
    urlquote = wrap(urlquote, ['text'])

    @internationalizeDocstring
    def urlunquote(self, irc, msg, args, text):
        """<text>

        Returns the text un-URL quoted.
        """
        s = utils.web.urlunquote(text)
        irc.reply(s)
    urlunquote = wrap(urlunquote, ['text'])

    @internationalizeDocstring
    def fetch(self, irc, msg, args, url):
        """<url>

        Returns the contents of <url>, or as much as is configured in
        supybot.plugins.Web.fetch.maximum.  If that configuration variable is
        set to 0, this command will be effectively disabled.
        """
        max = self.registryValue('fetch.maximum')
        if not max:
            irc.error(_('This command is disabled '
                      '(supybot.plugins.Web.fetch.maximum is set to 0).'),
                      Raise=True)
        fd = utils.web.getUrl(url, size=max) \
                        .decode('utf8')
        irc.reply(fd)
    fetch = wrap(fetch, ['url'])
    
    # TODO: make compatible with sqlite3 database
    @internationalizeDocstring
    def last(self, irc, msg, args, channel, optlist):
        """[<channel>] [--{from,with,without,near,proto} <value>] [--nolimit]

        Gives the last URL matching the given criteria.  --from is from whom
        the URL came; --proto is the protocol the URL used; --with is something
        inside the URL; --without is something that should not be in the URL;
        --near is something in the same message as the URL.  If --nolimit is
        given, returns all the URLs that are found to just the URL.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        predicates = []
        f = None
        nolimit = False
        for (option, arg) in optlist:
            if isinstance(arg, basestring):
                arg = arg.lower()
            if option == 'nolimit':
                nolimit = True
            elif option == 'from':
                def f(record, arg=arg):
                    return ircutils.strEqual(record.by, arg)
            elif option == 'with':
                def f(record, arg=arg):
                    return arg in record.url.lower()
            elif option == 'without':
                def f(record, arg=arg):
                    return arg not in record.url.lower()
            elif option == 'proto':
                def f(record, arg=arg):
                    return record.url.lower().startswith(arg)
            elif option == 'near':
                def f(record, arg=arg):
                    return arg in record.near.lower()
            if f is not None:
                predicates.append(f)
        def predicate(record):
            for predicate in predicates:
                if not predicate(record):
                    return False
            return True
        urls = [record.url for record in self.db.urls(channel, predicate)]
        if not urls:
            irc.reply(_('No URLs matched that criteria.'))
        else:
            if nolimit:
                urls = [format('%u', url) for url in urls]
                s = ', '.join(urls)
            else:
                # We should optimize this with another URLDB method eventually.
                s = urls[0]
            irc.reply(s)
    last = wrap(last, ['channeldb',
                       getopts({'from': 'something', 'with': 'something',
                                'near': 'something', 'proto': 'something',
                                'nolimit': '', 'without': 'something',})])

Class = Web

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
