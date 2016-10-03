# encoding: utf-8
"""
Copyright (c) 2012 - 2015, Ernesto Ruge, Christian Scholz
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import collections
import datetime
try:
    import html.parser as html_parser
except ImportError:
    # Python2 compatibility
    import HTMLParser as html_parser
import logging
import re
import sys
import time
try:
    from urllib.request import build_opener as build_url_opener
    from urllib.request import HTTPDefaultErrorHandler as http_exception
except ImportError:
    # Python2 compatibility
    from requests.exceptions import ConnectionError as http_exception
    import requests
    class build_url_opener(object):
        def open(self, *args, **kwargs):
            if kwargs.get("data", None) is None:
                response = requests.get(*args, **kwargs)
            else:
                response = requests.post(*args, **kwargs)
            return response.text
        def add_headers(self, headers):
            pass

RequestResult = collections.namedtuple("RequestResult", ("content", "real_url", "redirected", "requires_auth"))

try:
    import urllib.request
    import urllib.error
    class url_opener:
        def __init__(self):
            # "FancyURLopener" handles redirects automatically
            self._opener = urllib.request.FancyURLopener()
        def addheaders(self, *args):
            self._opener.addheaders(*args)
        def get_content(self, url, post_data=None, binary=False):
            data = urllib.parse.urlencode(post_data) if post_data else None
            with self._opener.open(url, data=data) as conn:
                content = conn.read()
                real_url = conn.geturl()
                redirected = real_url != url
                requires_auth = "noauth" in real_url
                # We can (and should) ignore "binary" (and never decode the
                # bytes), since lxml will take care for the encoding anyway.
                return RequestResult(content, real_url, redirected, requires_auth)
except ImportError:
    # Python2 compatibility
    import requests.exceptions
    import requests
    class url_opener:
        def addheaders(self, *args):
            pass
        def get_content(self, url, post_data=None, binary=False):
            try:
                if post_data is None:
                    response = requests.get(url)
                else:
                    response = requests.post(url, data=post_data)
            except requests.exceptions.ConnectionError:
                raise
            if response.history:
                redirected = True
                real_url = response.history[0].url
            else:
                redirected = False
                real_url = url
            requires_auth = "noauth" in response.url
            content = response.text
            if not binary:
                content = content.encode("ascii", "xmlcharrefreplace")
            return RequestResult(content, real_url, redirected, requires_auth)

from lxml import etree, html
from lxml.cssselect import CSSSelector
import magic
from pytz import timezone

from risscraper.model.person import Person
from risscraper.model.membership import Membership
from risscraper.model.organization import Organization
from risscraper.model.meeting import Meeting
from risscraper.model.consultation import Consultation
from risscraper.model.paper import Paper
from risscraper.model.agendaitem import AgendaItem
from risscraper.model.file import File
import risscraper.queue


class ScraperAllRis(object):

    # find everything inside a body of a subdocument
    body_re = re.compile(b"<?xml .*<body[ ]*>(.*)</body>")
    # marker for no date being found
    TIME_MARKER = datetime.datetime(1903, 1, 1)

    """
    adoption_css = CSSSelector("#rismain table.risdeco tbody tr td table.tk1 "
                               "tbody tr td table.tk1 tbody tr td table tbody "
                               "tr.zl12 td.text3")
    adoption_css = CSSSelector("table.risdeco tr td table.tk1 tr td.ko1 "
                               "table.tk1 tr td table tr.zl12 td.text3")
    """
    # selects the td which holds status information such as "beschlossen"
    adoption_css = CSSSelector("tr.zl12:nth-child(3) > td:nth-child(5)")
    # selects the td which holds the link to the TOP with transcript
    top_css = CSSSelector("tr.zl12:nth-child(3) > td:nth-child(7) > "
                          "form:nth-child(1) > input:nth-child(1)")
    # table with info block
    table_css = CSSSelector(".ko1 > table:nth-child(1)")
    attachment_1_css = CSSSelector('input[name=DOLFDNR]')
    attachments_css = CSSSelector('table.risdeco table.tk1 table.tk1 table.tk1')
    #main_css = CSSSelector("#rismain table.risdeco")


    def __init__(self, config, db, options):
        # configuration
        self.config = config
        # command line options and defaults
        self.options = options
        # database object
        self.db = db
        # prepare URL retriever
        self.url_opener = url_opener()
        self.url_opener.addheaders = [
            ('User-agent', config['scraper']['user_agent_name'])]

        # Queues
        if self.options.workfromqueue:
            self.person_queue = risscraper.queue.Queue('ALLRIS_PERSON', config, db)
            self.meeting_queue = risscraper.queue.Queue('ALLRIS_MEETING', config, db)
            self.paper_queue = risscraper.queue.Queue('ALLRIS_PAPER', config, db)
        # system info (PHP/ASP)
        self.template_system = None
        self.urls = None
        self.xpath = None

    def work_from_queue(self):
        """
        Empty queues if they have values. Queues are emptied in the
        following order:
        1. Person
        2. Meeting
        3. Paper
        """
        while self.person_queue.has_next():
            job = self.person_queue.get()
            self.get_person(person_id=job['key'])
            self.get_person_organization(person_id=job['key'])
            self.person_queue.resolve_job(job)
        while self.meeting_queue.has_next():
            job = self.meeting_queue.get()
            self.get_meeting(meeting_id=job['key'])
            self.meeting_queue.resolve_job(job)
        while self.paper_queue.has_next():
            job = self.paper_queue.get()
            self.get_paper(paper_id=job['key'])
            self.paper_queue.resolve_job(job)
        # when everything is done, we remove DONE jobs
        self.person_queue.garbage_collect()
        self.meeting_queue.garbage_collect()
        self.paper_queue.garbage_collect()

    def guess_system(self):
        """
        Tries to find out which AllRis version we are working with
        and adapts configuration
        TODO: XML Guess
        """
        self.template_system = 'xml'
        logging.info("Nothing to guess until now.")

    def find_person(self):
        # example: https://ksd.rostock.de/bi/kp041.asp?selfaction=ws
        find_person_url = self.config['scraper']['base_url'] + 'kp041.asp?selfaction=ws'
        logging.info("Getting person overview from %s", find_person_url)

        """parse an XML file and return the tree"""
        parser = etree.XMLParser(recover=True)
        response = self.get_url(find_person_url)
        if not response:
            return
        tree = etree.fromstring(response.content, parser=parser)
        h = html_parser.HTMLParser()

        # element 0 is the special block
        # element 1 is the list of persons
        for node in tree[1].iterchildren():
            elem = {}
            for e in node.iterchildren():
                if e.text:
                    elem[e.tag] = h.unescape(e.text)
                else:
                    elem[e.tag] = ''

            # now retrieve person details such as organization memberships etc.
            # we also get the age (but only that, no date of birth)
            person = Person(originalId=int(elem['kplfdnr']))
            if elem['link_kp']:
                person.originalUrl = elem['link_kp']
            # personal information

            if elem['adtit']:
                person.title = elem['adtit']
            if elem['antext1'] == 'Frau':
                person.sex = 1
            elif elem['antext1'] == 'Herr':
                person.sex = 2
            if elem['advname']:
                person.firstname = elem['advname']
            if elem['adname']:
                person.lastname = elem['adname']

            # address
            if elem['adstr']:
                person.address = elem['adstr']
            if elem['adhnr']:
                person.house_number = elem['adhnr']
            if elem['adplz']:
                person.postalcode = elem['adplz']
            if elem['adtel']:
                person.phone = elem['adtel']

            # contact
            if elem['adtel']:
                person.phone = elem['adtel']
            if elem['adtel2']:
                person.mobile = elem['adtel2']
            if elem['adfax']:
                person.fax = elem['adfax']
            if elem['adfax']:
                person.fax = elem['adfax']
            if elem['ademail']:
                person.email = elem['ademail']
            if elem['adwww1']:
                person.website = elem['adwww1']

            person_party = elem['kppartei']
            if person_party:
                if person_party in self.config['scraper']['party_alias']:
                    person_party = self.config['scraper']['party_alias'][person_party]
                new_organization = Organization(originalId=person_party,
                                                name=person_party,
                                                classification='party')
                original_id = '%s-%s' % (person.originalId, person_party)
                person.membership = [Membership(originalId=original_id,
                                                organization=new_organization)]

            if elem['link_kp'] is not None:
                if hasattr(self, 'person_queue'):
                    self.person_queue.add(person.originalId)
            else:
                logging.info("Person %s %s has no link", person.firstname,
                             person.lastname)
            self.db.save_person(person)

    def find_meeting(self, start_date=None, end_date=None):
        """ Find meetings within a given time frame and add them to the meeting
        queue.
        """
        meeting_find_url = (self.config['scraper']['allris']['meeting_find_url']
                            % (self.config['scraper']['base_url'],
                               start_date.strftime("%d.%m.%Y"),
                               end_date.strftime("%d.%m.%Y")))
        logging.info("Getting meeting overview from %s", meeting_find_url)

        parser = etree.XMLParser(recover=True)
        h = html_parser.HTMLParser()

        response = self.get_url(meeting_find_url)
        if not response:
            return
        xml = response.content
        xml = re.sub(b'</a>', '', xml)
        xml = re.sub(b'<a href="([^"]*)" target="_blank" ?>', br'\1', xml)
        root = etree.fromstring(xml, parser=parser)
        for item in root:
            if item.tag == 'list':
                root = item
                break
        for item in root.iterchildren():
            raw_meeting = {}
            for e in item.iterchildren():
                if e.text:
                    raw_meeting[e.tag] = h.unescape(e.text)
                else:
                    raw_meeting[e.tag] = ''
            meeting = Meeting(originalId=int(raw_meeting['silfdnr']))
            meeting.start = self.parse_date(raw_meeting['sisbvcs'])
            meeting.end = self.parse_date(raw_meeting['sisevcs'])
            meeting.name = raw_meeting['siname']
            meeting.originalUrl = ("%sto010.asp?SILFDNR=%s&options=4"
                                   % (self.config['scraper']['base_url'],
                                      raw_meeting['silfdnr']))
            meeting.name = raw_meeting['sitext']
            meeting.organization_name = raw_meeting['grname']
            # meeting.description = raw_meeting['sitext'] # WHAT TO DO WITH THIS
            self.db.save_meeting(meeting)
            self.meeting_queue.add(meeting.originalId)

    def get_organization(self, organization_id=None, organization_url=None):
        pass

    def get_person(self, person_id=None, person_url=None):
        # we dont need this(?)
        pass

    def get_person_organization(self, person_id=None, organization_url=None):
        url = ("%skp020.asp?KPLFDNR=%s&history=true"
               % (self.config['scraper']['base_url'], person_id))

        logging.info("Getting person organization from %s", url)
        # maps name of type to form name and membership type
        membership = collections.namedtuple('Membership', ('mtype', 'field'))
        membership_map = {
            u'Rat der Stadt' : membership('parliament', 'PALFDNR'),
            u'Parlament' : membership('parliament', 'PALFDNR'),
            u'Bürgerschaft' : membership('parliament', 'PALFDNR'),
            u'Fraktion' : membership('organisation', 'FRLFDNR'),
            u'Fraktionen': membership('parliament', 'FRLFDNR'),
            u'Ausschüsse' : membership('organization', 'AULFDNR'),
            u'Stadtbezirk': membership('parliament', 'PALFDNR'),
            u'BVV': membership('parliament', 'PALFDNR'),
            u'Bezirksparlament': membership('parliament', 'PALFDNR'),
            u'Bezirksverordnetenversammlung': membership('parliament',
                                                         'PALFDNR'),
            u'Ortsbeiräte': membership('organization', 'AULFDNR'),
            u'Aufsichtsräte': membership('organization', 'AULFDNR'),
            u'sonstige Gremien': membership('organization', 'AULFDNR'),
            # At least in Rostock there can be an empty organization type.
            # see: https://ksd.rostock.de/bi/kp020.asp?KPLFDNR=300&history=true
            u'': membership('organization', 'AULFDNR'),
        }
        # Stupid re-try concept because AllRis sometimes misses start < at
        # tags at first request.
        try_counter = 0
        while True:
            try:
                response = self.get_url(url)
                if not response:
                    return
                tree = html.fromstring(response.content)

                memberships = []
                person = Person(originalId=person_id)

                # Different versions contain different "main" divs:
                #   Rostock (ALLRIS net Version 3.8.8): "rismain"
                #   others: "rismain_raw"
                for key in ("rismain_raw", "rismain"):
                    # There are three tables on this page:
                    #    Anschrift, Sonstiges, Mitarbeit
                    # We are interested in "Mitarbeit".
                    table = tree.xpath('//*[@id="%s"]/table[2]' % key)
                    if table:
                        break
                # obtain the table with the membership list via a simple state machine
                if table:
                    table = table[0]
                    mtype = None
                    field = None
                    # for checking if it changes
                    old_group_id = None
                    # for checking if it changes
                    old_group_name = None
                    # might break otherwise
                    group_id = None
                    for line in table.findall("tr"):
                        if line[0].tag == "th":
                            # This is a subtitle for the following memberships.
                            # Carefully look inside - maybe it is empty.
                            what = (line[0].text or "").strip()
                            field = None
                            field_list = None
                            if what in membership_map:
                                mtype = membership_map[what].mtype
                                field = membership_map[what].field
                            elif 'Wahlperiode' in what:
                                mtype = 'parliament'
                                # 'FRLFDNR'
                                field_list = ['KPLFDNR', 'AULFDNR']
                            elif u"Auskünfte gemäß BVV" in what:
                                break
                            else:
                                logging.error("Unknown organization type %s "
                                              "at person detail page %s",
                                              what, person_id)
                                continue
                        else:
                            """
                            This is a membership description consisting of
                            organization icon, organization name, role and
                            timespan.
                            Example:
                                <tr><td><form action="au020.asp" method="post" style="margin:0">
                                        <input name="AULFDNR" value="157" type="hidden">
                                        <input name="altoption" value="Ausschuss" type="hidden">
                                        <input class="il1_au" value="AU" title="Ausschuss" type="submit"></form></td>
                                    <td class="text1"><a href="au020.asp?AULFDNR=157&amp;altoption=Ausschuss">
                                        Ausschuss für Stadt- und Regionalentwicklung, Umwelt und Ordnung</a></td>
                                    <td class="text4">Mitglied&nbsp;</td>
                                    <td class="text4">14.07.1999 - 13.12.1999</td><td>&nbsp;</td></tr>
                            """
                            if "Keine Information" in line.text_content():
                                # skip because no content is available
                                # Typically "Fraktion" is undefined.
                                continue

                            # Empty line = strange stuff comes after this
                            if len(list(line)) < 2:
                                break

                            # first get the name of group
                            group_name = line[1].text_content()
                            organization = Organization(name=group_name)

                            organization.classification = mtype

                            # Now the first col might be a form with more
                            # useful information which will carry through
                            # until we find another one.
                            # With it. we still check the name though.
                            form = line[0].find("form")
                            if form is not None:
                                if field:
                                    group_id = int(form.find(
                                        "input[@name='%s']" % field).get(
                                            "value"))
                                elif field_list:
                                    for field in field_list:
                                        temp_form = form.find(
                                            "input[@name='%s']" % field)
                                        if temp_form is not None:
                                            group_id = int(temp_form.get(
                                                "value"))
                                organization.originalId = group_id
                                # remember it for next loop
                                old_group_id = group_id
                                # remember it for next loop
                                old_group_name = group_name
                            else:
                                # We did not find a form. We assume that the
                                # old group still applies but we nevertheless
                                # check if the groupname is still the same.
                                if old_group_name != group_name:
                                    logging.warn("Group name differs but we "
                                                 "didn't get a form with new "
                                                 "group id: group name=%s, old "
                                                 "group name=%s, old group "
                                                 "id=%s at url %s",
                                                 group_name, old_group_name,
                                                 old_group_id, url)
                                    organization.originalId = None
                                else:
                                    organization.originalId = old_group_id
                            membership = Membership(organization=organization)
                            membership.originalId = '%s-%s' % (person_id,
                                                               group_id)

                            # TODO: create a list of roles so we can
                            #       index them somehow
                            role = line[2].text_content()
                            raw_date = line[3].text_content()
                            # parse the date information
                            if "seit" in raw_date:
                                # Example: "seit 02.07.2014"
                                dparts = raw_date.split()
                                membership.endDate = dparts[-1]
                            elif "Keine" in raw_date or not raw_date.strip():
                                # no date information available
                                start_date = end_date = None
                            else:
                                # Example: "14.07.1999 - 13.12.1999"
                                dparts = raw_date.split()
                                membership.startDate = dparts[0]
                                membership.endDate = dparts[-1]
                            if organization.originalId is not None:
                                memberships.append(membership)
                            else:
                                logging.warn("Bad organization (%s): %s",
                                             url, line.text_content())

                    person.membership = memberships
                    oid = self.db.save_person(person)
                    return
                else:
                    logging.info("table missing, nothing to do at %s", url)
                    return
            except AttributeError:
                if try_counter < 3:
                    logging.info("Try again: Getting person organizations with "
                                 "person id %d from %s", person_id, url)
                    try_counter += 1
                else:
                    logging.error("Failed getting person organizations with "
                                  "person id %d from %s", person_id, url)
                    return


    def get_person_organization_presence(self, person_id=None, person_url=None):
        # URL is like si019.asp?SILFDNR=5672
        # TODO
        pass


    def get_meeting(self, meeting_url=None, meeting_id=None):
        """ Load meeting details (e.g. agendaitems) for the given detail page
        URL or numeric ID
        """
        meeting_url = ("%sto010.asp?selfaction=ws&template=xyz&SILFDNR=%s"
                       % (self.config['scraper']['base_url'], meeting_id))

        logging.info("Getting meeting %d from %s", meeting_id, meeting_url)

        response = self.get_url(meeting_url)
        if not response:
            return
        # If r.history has an item we have a problem
        if response.redirected:
            # TODO: verify that status=302 implies "requires_auth"
            #if r.history[0].status_code == 302:
            if response.requires_auth:
                logging.info("Meeting %d from %s seems to be private",
                             meeting_id, meeting_id)
            else:
                logging.error("Strange redirect %d from %s",
                              meeting_id, meeting_url)
            return
        h = html_parser.HTMLParser()
        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(response.content, parser=parser)

        meeting = Meeting(originalId=meeting_id)

        # special area
        special = {}
        for item in root[0].iterchildren():
            special[item.tag] = item.text
        # Woher kriegen wir das Datum? Nur über die Übersicht?
        #if 'sisb' in special:
        #if 'sise' in special:
        if 'saname' in special:
            meeting.type = special['saname']
        # head area
        head = {}
        for item in root[1].iterchildren():
            if item.text:
                head[item.tag] = h.unescape(item.text)
            else:
                head[item.text] = ''
        if 'sitext' in head:
            meeting.name = head['sitext']
        if 'raname' in head:
            meeting.room = head['raname']
        if 'raort' in head:
            meeting.address = head['raort']
        agendaitems = []

        for item in root[2].iterchildren():
            elem = {}
            for e in item.iterchildren():
                elem[e.tag] = e.text

            section = [elem['tofnum'], elem['tofunum'], elem['tofuunum']]
            section = [x for x in section if x != "0"]
            elem['section'] = ".".join(section)
            agendaitem = AgendaItem()

            agendaitem.originalId = int(elem['tolfdnr'])
            agendaitem.public = (elem['toostLang'] == u'öffentlich')
            #agendaitem.name = elem['totext1']
            # get agenda detail page
            # TODO: Own Queue
            time.sleep(self.config['scraper']['wait_time'])
            agendaitem_url = ('%sto020.asp?selfaction=ws&template=xyz&TOLFDNR=%s'
                              % (self.config['scraper']['base_url'],
                                 agendaitem.originalId))
            logging.info("Getting agendaitem %d from %s",
                         agendaitem.originalId, agendaitem_url)

            agendaitem_r = self.get_url(agendaitem_url)
            if not agendaitem_r:
                return

            if agendaitem_r.redirected:
                logging.info("Agenda item %d from %s seems to be private",
                             meeting_id, meeting_url)
            else:
                agendaitem_xml = agendaitem_r.content
                # TODO: mixup of agendaitem_parser / parser below?
                agendaitem_parser = etree.XMLParser(recover=True)
                agendaitem_root = etree.fromstring(agendaitem_xml,
                                                   parser=parser)
                add_agenda_item = {}
                for add_item in agendaitem_root[0].iterchildren():
                    if add_item.tag == "rtfWP" and len(add_item) > 0:
                        try:
                            agendaitem.resolution_text = h.unescape(
                                etree.tostring(add_item[0][1][0]))
                        except:
                            logging.warn("Unable to parse resolution text at "
                                         "%s", agendaitem_url)
                    else:
                        if add_item.text:
                            add_agenda_item[add_item.tag] = h.unescape(
                                add_item.text)
                if 'toptext' in add_agenda_item:
                    agendaitem.name = add_agenda_item['toptext']

                # there are papers with id = 0. we don't need them.
                if int(elem['volfdnr']):
                    consult_id = '%s-%s' % (agendaitem.originalId,
                                            elem['volfdnr'])
                    consultation = Consultation(originalId=consult_id)
                    paper_id = int(elem['volfdnr'])
                    if 'voname' in add_agenda_item:
                        consultation.paper = Paper(
                            originalId=paper_id, name=add_agenda_item['voname'])
                    else:
                        consultation.paper = Paper(originalId=paper_id)
                    agendaitem.consultation = [consultation]
                    if 'vobetr' in add_agenda_item:
                        if add_agenda_item['vobetr'] != agendaitem.name:
                            logging.warn("different values for name: %s and %s",
                                         agendaitem.name,
                                         add_agenda_item['vobetr'])
                    if hasattr(self, 'paper_queue'):
                        self.paper_queue.add(int(elem['volfdnr']))
                if 'totyp' in add_agenda_item:
                    agendaitem.result = add_agenda_item['totyp']
                agendaitems.append(agendaitem)
        meeting.agendaItem = agendaitems

        oid = self.db.save_meeting(meeting)
        logging.info("Meeting %d stored with _id %s", meeting_id, oid)


    def get_paper(self, paper_url=None, paper_id=None):
        """
        Load paper details for the paper given by detail page URL
        or numeric ID
        """
        paper_url = ('%svo020.asp?VOLFDNR=%s'
                     % (self.config['scraper']['base_url'], paper_id))
        logging.info("Getting paper %d from %s", paper_id, paper_url)

        # Stupid re-try concept because AllRis sometimes misses
        # start < at tags at first request.
        try_counter = 0
        while True:
            try:
                response = self.get_url(paper_url)
                if not response:
                    return
                if response.requires_auth:
                    logging.warn("Paper %s in %s seems to private",
                                 paper_id, paper_url)
                    return
                doc = html.fromstring(response.content)
                data = {}

                # Beratungsfolge-Table checken
                # lets hope we always have this table
                table = self.table_css(doc)[0]
                self.consultation_list_start = False
                last_headline = ''
                for line in table:
                    if line.tag == 'tr':
                        headline = line[0].text
                    elif line.tag == 'td':
                        headline = line.text
                    else:
                        logging.error("ERROR: Serious error in data table. "
                                      "Unable to parse.")
                    if headline:
                        headline = headline.split(":")[0].lower()
                        if headline[-1] == ":":
                            headline = headline[:-1]
                        if headline == "betreff":
                            value = line[1].text_content().strip()
                            # There is some html comment with a script
                            # tag in front of the text which we remove.
                            value = value.split("-->")[1]
                            # remove all multiple spaces from the string
                            data[headline] = " ".join(value.split())
                        elif headline in ['verfasser', u'federführend',
                                          'drucksache-art']:
                            data[headline] = line[1].text.strip()
                        elif headline in ['status']:
                            data[headline] = line[1].text.strip()
                            # related papers
                            if len(line) > 2:
                                if len(line[3]):
                                    # Gets originalId. is there something
                                    # else at this position? (will break)
                                    paper_id = line[3][0][0][1][0].get(
                                        'href').split('=')[1].split('&')[0]
                                    data['relatedPaper'] = [Paper(
                                        originalId=paper_id)]

                        # Lot's of scraping just because of the date (?)
                        elif headline == "beratungsfolge":
                            # The actual list will be in the next row
                            # inside a table, so we only set a marker.
                            self.consultation_list_start = True
                        elif self.consultation_list_start:
                            elem = line[0][0]
                            # The first line is pixel images, so skip
                            # it, then we need to jump in steps of two.
                            amount = (len(elem) - 1) / 2
                            consultations = []
                            date_list = []
                            i = 0
                            item = None
                            for elem_line in elem:
                                if i == 0:
                                    i += 1
                                    continue

                                """
                                Here we need to parse the actual list which can have different forms. A complex example
                                can be found at http://ratsinfo.aachen.de/bi/vo020.asp?VOLFDNR=10822
                                The first line is some sort of headline with the committee in question and the type of consultation.
                                After that 0-n lines of detailed information of meetings with a date, transscript and decision.
                                The first line has 3 columns (thanks to colspan) and the others have 7.

                                Here we make every meeting a separate entry, we can group them together later again if we want to.
                                """

                                # now we need to parse the actual list
                                # those lists
                                new_consultation = Consultation()
                                new_consultation.status = \
                                        elem_line[0].attrib['title'].lower()
                                if len(elem_line) == 3:
                                    # The order is "color/status", name of
                                    # committee / link to TOP, more info we
                                    # define a head dict here which can be
                                    # shared for the other lines once we find
                                    # another head line we will create a new
                                    # one here.
                                    new_consultation.role = \
                                            elem_line[2].text.strip()

                                    # Name of committee, e.g.
                                    # "Finanzausschuss", unfort. without id
                                    #'committee' : elem_line[1].text.strip(),
                                # For some obscure reasons sometimes action
                                # is missing.
                                elif len(elem_line) == 2:
                                    # The order is "color/status", name of
                                    # committee / link to TOP, more info.
                                    status = \
                                            elem_line[0].attrib['title'].lower()
                                    # We define a head dict here which can be
                                    # shared for the other lines once we find
                                    # another head line we will create a new
                                    # one here.
                                    # name of committee, e.g.
                                    # "Finanzausschuss", unfort. without id
                                    #'committee' : elem_line[1].text.strip(),
                                elif len(elem_line) == 7:
                                    try:
                                        # This is about line 2 with lots of
                                        # more stuff to process.
                                        # Date can be text or a link with that
                                        # text.
                                        # We have a link (and ignore it).
                                        if len(elem_line[1]) == 1:
                                            date_text = elem_line[1][0].text
                                        else:
                                            date_text = elem_line[1].text
                                        date_list.append(
                                            datetime.datetime.strptime(
                                                date_text.strip(), "%d.%m.%Y"))
                                        if len(elem_line[2]):
                                            # Form with silfdnr and toplfdnr
                                            # but only in link (action=
                                            #   "to010.asp?topSelected=57023")
                                            form = elem_line[2][0]
                                            meeting_id = form[0].attrib['value']
                                            new_consultation.meeting = [Meeting(
                                                originalId=meeting_id)]
                                            # Full name of meeting, e.g.
                                            # "A/31/WP.16 öffentliche/
                                            #   nichtöffentliche Sitzung des
                                            # Finanzausschusses"
                                            #item['meeting'] = \
                                            #    elem_line[3][0].text.strip()
                                        else:
                                            # No link to TOP. Should not be
                                            # possible but happens.
                                            #   (TODO: Bugreport?)
                                            # Here we have no link but the text
                                            # is in the TD directly - will be
                                            # scaped as meeting.
                                            #item['meeting'] = \
                                            #    elem_line[3].text.strip()
                                            logging.warn(
                                                "AgendaItem in consultation "
                                                "list on the web page does not "
                                                "contain a link to the actual "
                                                "meeting at paper %s",
                                                paper_url)
                                        toplfdnr = None
                                        if len(elem_line[6]) > 0:
                                            form = elem_line[6][0]
                                            toplfdnr = form[0].attrib['value']
                                        if toplfdnr:
                                            new_consultation.originalId = \
                                                    "%s-%s" % (toplfdnr,
                                                               paper_id)
                                            # actually the id of the transcript
                                            new_consultation.agendaItem = \
                                                    AgendaItem(
                                                        originalId=toplfdnr)
                                            # e.g. "ungeändert beschlossen"
                                            new_consultation.agendaItem.result \
                                                    = elem_line[4].text.strip()
                                            consultations.append(
                                                new_consultation)
                                        else:
                                            logging.error(
                                                "missing agendaItem ID in "
                                                "consultation list at %s",
                                                paper_url)
                                    except (IndexError, KeyError):
                                        logging.error(
                                            "ERROR: Serious error in "
                                            "consultation list. Unable to "
                                            "parse.")
                                        logging.error(
                                            "Serious error in consultation "
                                            "list. Unable to parse.")
                                        return []
                                i += 1
                            # Theory: we don't need this at all, because it's
                            # scraped at meeting.
                            #data['consultations'] = consultations
                            # set the marker to False again as we have read it
                            self.consultation_list_start = False
                    last_headline = headline
                    # We simply ignore the rest (there might not be much more
                    # actually).
                # The actual text comes after the table in a div but it's not
                # valid XML or HTML this using regex.
                data['docs'] = self.body_re.findall(response.content)
                first_date = False
                for single_date in date_list:
                    if first_date:
                        if single_date < first_date:
                            first_date = single_date
                    else:
                        first_date = single_date
                paper = Paper(originalId=paper_id)
                paper.originalUrl = paper_url
                paper.name = data['betreff']
                paper.description = data['docs']
                if 'drucksache-art' in data:
                    paper.paperType = data['drucksache-art']
                if first_date:
                    paper.publishedDate = first_date.strftime("%d.%m.%Y")
                # see theory above
                #if 'consultations' in data:
                #    paper.consultation = data['consultations']
                paper.auxiliaryFile = []
                # get the attachments step 1 (Drucksache)
                file_1 = self.attachment_1_css(doc)
                if len(file_1):
                    if file_1[0].value:
                        href = ('%sdo027.asp'
                                % self.config['scraper']['base_url'])
                        original_id = file_1[0].value
                        name = 'Drucksache'
                        main_file = File(originalId=original_id, name=name)
                        main_file = self.get_file(main_file, href, True)
                        paper.mainFile = main_file
                # get the attachments step 2 (additional attachments)
                files = self.attachments_css(doc)
                if len(files) > 0:
                    if len(files[0]) > 1:
                        if files[0][1][0].text.strip() == "Anlagen:":
                            for tr in files[0][2:]:
                                link = tr[0][0]
                                href = ("%s%s"
                                        % (self.config['scraper']['base_url'],
                                           link.attrib["href"]))
                                name = link.text
                                path_tokens = link.attrib["href"].split('/')
                                original_id = "%d-%d" % (int(path_tokens[4]),
                                                         int(path_tokens[6]))
                                aux_file = File(originalId=original_id,
                                                name=name)
                                aux_file = self.get_file(aux_file, href)
                                paper.auxiliaryFile.append(aux_file)
                if not len(paper.auxiliaryFile):
                    del paper.auxiliaryFile
                oid = self.db.save_paper(paper)
                return
            except (KeyError, IndexError):
                if try_counter < 3:
                    logging.info("Try again: Getting paper %d from %s",
                                 paper_id, paper_url)
                    try_counter += 1
                else:
                    logging.error("Failed getting paper %d from %s",
                                  paper_id, paper_url)
                    return

    def get_file(self, file_obj, file_url, post=False):
        """
        Loads the file file from the server and stores it into
        the file object given as a parameter.

        The file parameter has to be an object of type
        model.file.File.
        """
        time.sleep(self.config['scraper']['wait_time'])
        logging.info("Getting file '%s'", file_obj.originalId)

        file_backup = file_obj
        logging.info("Getting file %s from %s", file_obj.originalId, file_url)

        if post:
            file_response = self.get_url(file_url, post_data={
                'DOLFDNR': file_obj.originalId, 'options': '64'}, binary=True)
        file_response = self.get_url(file_url, binary=True)
        if not file_response:
            logging.error("Error downloading file %s", file_url)
            return file_obj
        file_obj.content = file_response.content
        # catch strange magic exception
        try:
            file_obj.mimetype = magic.from_buffer(file_obj.content, mime=True)
        except magic.MagicException:
            logging.warn("Warning: unknown magic error at file %s from %s",
                         file_obj.originalId, file_url)
            return file_backup
        file_obj.filename = self.make_filename(file_obj)
        return file_obj

    def make_filename(self, file_obj):
        ext = 'dat'

        try:
            name = file_obj.name[:192]
        except (AttributeError, TypeError):
            name = file_obj.originalId

        for extension in self.config['file_extensions']:
            if extension[0] == file_obj.mimetype:
                ext = extension[1]
                break
        if ext == 'dat':
            logging.warn("No entry in config:main:file_extensions for %s at "
                         "file id %s", file_obj.mimetype, file_obj.originalId)
        return name + '.' + ext

    def get_url(self, url, post_data=None, binary=False):
        retry_counter = 0
        while retry_counter < 4:
            retry = False
            try:
                # urllib will create a POST request if "data" is not None
                return self.url_opener.get_content(
                    url, post_data=post_data, binary=binary)
            except IOError as exc:
                retry_counter += 1
                retry = True
                logging.warning("Connection Reset (%s) while getting %s, "
                                "trying again", exc, url)
                time.sleep(self.config['scraper']['wait_time'] * 5)
        if retry_counter == 4 and retry:
            logging.critical("HTTP Error while getting %s", url)
            sys.stderr.write("CRITICAL ERROR: HTTP Error while getting %s\n"
                             % url)
            return None

    # mrtopf
    def parse_date(self, s):
        """parse dates like 20121219T160000Z"""
        berlin = timezone('Europe/Berlin')
        year = int(s[0:4])
        month = int(s[4:6])
        day = int(s[6:8])
        hour = int(s[9:11])
        minute = int(s[11:13])
        second = int(s[13:15])
        return datetime.datetime(year, month, day, hour, minute, second, 0,
                                 tzinfo=berlin)


class TemplateError(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)
