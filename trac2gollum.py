#!/usr/bin/env python2.7
import sys
import os
import sqlite3
from datetime import datetime
import subprocess
import re
import time
import urllib
import shutil

GIT = "/opt/local/bin/git"

def format_user(entry):
    """ git requires users to conform to "A B <ab@c.de>"

    >>> format_user(['', '', '', u'user', u'127.0.0.1'])
    (u'user', u'127.0.0.1')
    >>> format_user(['', '', '', u'user@home.local', u'127.0.0.1'])
    (u'user', u'user@home.local')
    >>> format_user(['', '', '', u'user <user@home.local>', u'127.0.0.1'])
    (u'user ', u'user@home.local')
    """
    user = entry[3]
    if u"<" in user and u"@" in user:
        user, mail = user.split(u"<")
        return (user, mail.strip(u">"))
    if u"@" in user:
        u, d = user.split(u"@")
        return (u, user)
    ip = entry[4]
    return (user, ip)


def format_comment(entry, final):
    """ creates / formats commit comment.
        "final" is true when content is converted from Trac markup to Markdown.
    """
    comment = entry[6] or (u'Page "%s" updated.' % (entry[0]))
    if final:
        return u'%s (automatically converted to Markdown)' % comment
    return comment


#
#   I hope you don't need to change anything below this line
#


def getargs():
    """ get database file to read from and git repository to write to from
        commandline and find the trac attachments directory
    """
    try:
        (trac_dir,git_dir) = sys.argv[1:3]

        db_file = os.path.join(trac_dir,"db","trac.db")
        if not os.path.isfile(db_file):
            raise Exception("Trac db file %s does not exist" % db_file)

        if not os.path.isdir(os.path.join(git_dir,".git")):
            raise Exception("Destination %s not found or not a git repository" % git_dir)

        trac_attachments = os.path.join(trac_dir,"attachments","wiki")
        if not os.path.isdir(trac_attachments):
            raise Exception("Trac attachments directory %s not found" % trac_attachments)

        return (sqlite3.connect(db_file),git_dir,trac_attachments)
    except IndexError:
        print u'Try: "%s trac-dir git-repo' % (sys.argv[0])
        sys.exit(1)


def format_time(timestamp):
    """ return a git compatible timestamp

    >>> format_time(1229442008.852975)
    u'1229442008 +0200'
    """
    return str(int(timestamp)) + " +0200".decode("UTF-8")


def convert_code(text):
    """ replace code blocks (very primitive)

    >>> convert_code(u"\\nTest\\n\\n{{{\\n#!sh\\nCode paragraph\\n}}}\\n\\nTest\\n")
    u'\\nTest\\n\\n```sh\\nCode paragraph\\n```\\n\\nTest\\n'
    >>> convert_code(u"\\nTest\\n\\n{{{\\nCode paragraph\\n}}}\\n\\nTest\\n")
    u'\\nTest\\n\\n\\n    Code paragraph\\n\\n\\nTest\\n'

    """
    result = u""
    start = False
    running = False
    original = text
    indent = u""
    for line in text.splitlines():
        if line.strip() == u"{{{":
            start = True
            running = True
        elif start:
            start = False
            if line.startswith("#!"):
                result += u"```" + line.replace("#!", "") + os.linesep
            else:
                indent = u"    "
                result += os.linesep + indent + line + os.linesep
        elif line.strip() == u"}}}" and running:
            running = False
            if indent:
                indent = u""
                result += os.linesep
            else:
                result += u"```" + os.linesep
        else:
            result += indent + line + os.linesep
    if running:
        # something went wrong; don't touch the text.
        return original
    return result


re_macro = re.compile(r'\[{2}(\w+)\]{2}')
re_inlinecode = re.compile(r'\{\{\{([^\n]+?)\}\}\}')
re_h4 = re.compile(r'====\s(.+?)\s====')
re_h3 = re.compile(r'===\s(.+?)\s===')
re_h2 = re.compile(r'==\s(.+?)\s==')
re_h1 = re.compile(r'=\s(.+?)\s=')
re_uri = re.compile(r'\[(?:wiki:)?([^\s]+)\s(.+)\]')
re_wiki_uri = re.compile(r'(\s)wiki:([A-Za-z0-9]+)(\s)')
re_CamelCaseUri = re.compile(r'([^"\/\!\[\]\|])(([A-Z][a-z0-9]+){2,})')
re_NoUri = re.compile(r'\!(([A-Z][a-z0-9]+){2,})')
re_strong = re.compile(r"'''(.+)'''")
re_italic = re.compile(r"''(.+)''")
re_ul = re.compile(r'(^\s\*)', re.MULTILINE)
re_ol = re.compile(r'^\s(\d+\.)', re.MULTILINE)


def format_text(text):
    """ converts trac wiki to gollum markdown syntax

    >>> format_text(u"= One =\\n== Two ==\\n=== Three ===\\n==== Four ====")
    u'# One\\n## Two\\n### Three\\n#### Four\\n'
    >>> format_text(u"Paragraph with ''italic'' and '''bold'''.")
    u'Paragraph with *italic* and **bold**.\\n'
    >>> format_text(u"Example with [wiki:a/b one link].")
    u'Example with [[one link|a/b]].\\n'
    >>> format_text(u"Beispiel mit [http://blog.fefe.de Fefes Blog] Link.")
    u'Beispiel mit [[Fefes Blog|http://blog.fefe.de]] Link.\\n'
    >>> format_text(u"Beispiel mit CamelCase Link.")
    u'Beispiel mit [[CamelCase]] Link.\\n'
    >>> format_text(u"Fieser [WarumBackup Argumente fuer dieses Angebot] Link")
    u'Fieser [[Argumente fuer dieses Angebot|WarumBackup]] Link\\n'
    >>> format_text(u"Beispiel ohne !CamelCase Link.")
    u'Beispiel ohne CamelCase Link.\\n'
    >>> format_text(u"Beispiel mit wiki:wikilink")
    u'Beispiel mit [[wikilink]]\\n'
    >>> format_text(u"Test {{{inline code}}}\\n\\nand more {{{inline code}}}.")
    u'Test `inline code`\\n\\nand more `inline code`.\\n'
    >>> format_text(u"\\n * one\\n * two\\n")
    u'\\n* one\\n* two\\n'
    >>> format_text(u"\\n 1. first\\n 2. second\\n")
    u'\\n1. first\\n2. second\\n'
    >>> format_text(u"There is a [[macro]] here.")
    u'There is a (XXX macro: "macro") here.\\n'
    """
    # TODO: ticket: and source: links are not yet handled
    text = convert_code(text)
    text = re_macro.sub(r'(XXX macro: "\1")', text)
    text = re_inlinecode.sub(r'`\1`', text)
    text = re_h4.sub(r'#### \1', text)
    text = re_h3.sub(r'### \1', text)
    text = re_h2.sub(r'## \1', text)
    text = re_h1.sub(r'# \1', text)
    text = re_uri.sub(r'[[\2|' + r'\1]]', text)
    text = re_CamelCaseUri.sub(r'\1[[\2]]', text)
    text = re_wiki_uri.sub(r'\1[[\2]]\3', text)
    text = re_NoUri.sub(r'\1', text)
    text = re_strong.sub(r'**\1**', text)
    text = re_italic.sub(r'*\1*', text)
    text = re_ul.sub(r'*', text)
    text = re_ol.sub(r'\1', text)
    return text


def format_page(page):
    """ rename WikiStart to Home

    >>> format_page(u'test')
    u'test'
    >>> format_page(u'WikiStart')
    u'Home'
    """
    if page == u"WikiStart":
        return u"Home"
    # Gollum wiki replaces slash and space with dash:
    return page.replace(u"/", u"-").replace(u" ", u"-")


def read_database(db):
    # get all pages except those generated by the trac system itself (help etc.)
    pages = [x[0] for x in db.execute('select name from wiki where author != "trac" group by name', []).fetchall()]
    for page in pages:
        formatted_page_name = format_page(page)
        for revision in db.execute('select * from wiki where name is ? order by version', [page]).fetchall():
            user, email = format_user(revision)
            yield {
                "page": formatted_page_name,
                "version": revision[1],
                "time": format_time(revision[2]),
                "username": user,
                "useremail": email,
                "ip": revision[4],
                "text": revision[5],
                "comment": format_comment(revision, final=False),
                "attachments": [],
            }
        latest = db.execute('select name, max(version), time, author, ipnr, text, comment from wiki where name is ?',
                            [page]).fetchall()[0]
        attachments = db.execute('select filename,time,description,author,ipnr from attachment where id is ?', [page]).fetchall()
        yield {
            "page": formatted_page_name,
            "version": latest[1],
            "time": format_time(time.time()),
            "username": "Trac2Gollum",
            "useremail": "github.com/hinnerk/Trac2Gollum.git",
            "ip": latest[4],
            "text": format_text(latest[5]),
            "comment": format_comment(latest, final=True),
            "attachments": map(
                lambda x: {
                    "source": os.path.join(page,urllib.quote(x[0])),
                    "destination" : "attachments/%s/%s" % (formatted_page_name,x[0]),
                    "time": format_time(x[1]),
                    "username": format_user(x)[0],
                    "useremail": x[4],
                    "ip": x[4],
                    "comment": x[2] or "Attachment %s added" % x[0],
                    },
                attachments)
        }

def copyfile(src,dest):
    dest_dir = os.path.dirname(dest)
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    shutil.copyfile(src,dest)


def main():
    (db, target, attachments_src) = getargs()


    def git_commit(entry):
        subprocess.check_call([GIT, "commit", "-m", entry["comment"]], cwd=target,
                              env={"GIT_COMMITTER_DATE": entry["time"],
                                   "GIT_AUTHOR_DATE": entry["time"],
                                   "GIT_AUTHOR_NAME": entry["username"],
                                   "GIT_AUTHOR_EMAIL": entry["useremail"],
                                   "GIT_COMMITTER_NAME": "Trac2Gollum",
                                   "GIT_COMMITTER_EMAIL": "http://github.com/hinnerk/Trac2Gollum.git"})
    def git_add(page):
        subprocess.check_call([GIT, "add", page], cwd=target)


    source = read_database(db)
    for entry in source:
        # make paths conform to local filesystem
        page = os.path.normpath(entry["page"] + u".md")
        if not os.path.supports_unicode_filenames:
            page = page.encode("utf-8")
        try:
            open(os.path.join(target, page), "wb").write(entry["text"].encode("utf-8"))
            git_add(page)
            for attachment in entry["attachments"]:
                copyfile(os.path.join(attachments_src,attachment["source"]),
                         os.path.join(target,attachment["destination"]))
                git_add(attachment["destination"])
            try:
                git_commit(entry)
            # trying to circumvent strange unicode-encoded file name problems:
            except subprocess.CalledProcessError:
                [git_add(x) for x in os.listdir(target)]
                git_commit(entry)

        except Exception, e:
            print "\n\n\nXXX Problem: ", e
            sys.exit(23)
    # finally garbage collect git repository
    subprocess.check_call([GIT, "gc"], cwd=target)


if __name__ == "__main__":
    main()
