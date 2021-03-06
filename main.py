# encoding: utf-8

"""
Copyright (c) 2012 - 2015, Marian Steinbach, Ernesto Ruge
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

import argparse
import calendar
import datetime
import inspect
import logging
import os
import sys

from risscraper.scraperallris import ScraperAllRis
from risscraper.scrapersessionnet import ScraperSessionNet

import config as db_config

CMD_SUBFOLDER = os.path.realpath(os.path.abspath(os.path.join(os.path.split(
    inspect.getfile(inspect.currentframe()))[0], "city")))
if CMD_SUBFOLDER not in sys.path:
    sys.path.insert(0, CMD_SUBFOLDER)


def main():
    parser = argparse.ArgumentParser(
        description='Scrape Dein Ratsinformationssystem')
    parser.add_argument('--body', '-b', dest='body_uid', required=True,
                        help=("UID of the body"))
    parser.add_argument('--interactive', '-i', default=0, dest="interactive",
                        help=("Interactive mode: brings messages above given "
                              "level to stdout"))
    parser.add_argument('--queue', '-q', dest="workfromqueue",
                        action="store_true", default=False,
                        help=('Set this flag to activate "greedy" scraping. '
                              'This means that links from sessions to '
                              'submissions are followed. This is implied '
                              'if --start is given, otherwise it is off by '
                              'default.'))
    # date
    parser.add_argument('--start', dest="start_month", default=False,
                        help=('Find sessions and related content starting in '
                              'this month. Format: "YYYY-MM". When this is '
                              'used, the -q parameter is implied.'))
    parser.add_argument('--end', dest="end_month", default=False,
                        help=('Find sessions and related content up to this '
                              'month. Requires --start parameter to be set, '
                              'too. Format: "YYYY-MM"'))
    # organization
    parser.add_argument('--organizationid', dest="organization_id",
                        default=False, help='Scrape a specific organization, '
                                            'identified by its numeric ID')
    parser.add_argument('--organizationurl', dest="organization_url",
                        default=False, help='Scrape a specific organization, '
                                            'identified by its detail page URL')
    # person
    parser.add_argument('--personid', dest="person_id", default=False,
                        help='Scrape a specific person, identified by its '
                             'numeric ID')
    parser.add_argument('--personurl', dest="person_url", default=False,
                        help='Scrape a specific person, identified by its '
                             'detail page URL')
    # meeting
    parser.add_argument('--meetingid', dest="meeting_id", default=False,
                        help='Scrape a specific meeting, identified by its '
                             'numeric ID')
    parser.add_argument('--meetingurl', dest="meeting_url", default=False,
                        help='Scrape a specific meeting, identified by its '
                             'detail page URL')
    # paper
    parser.add_argument('--paperid', dest="paper_id", default=False,
                        help='Scrape a specific paper, identified by its '
                             'numeric ID')
    parser.add_argument('--paperurl', dest="paper_url", default=False,
                        help='Scrape a specific paper, identified by its '
                             'detail page URL')

    parser.add_argument('--erase', dest="erase_db", action="store_true",
                        default=False, help='Erase all database content '
                                            'before start. Caution!')
    parser.add_argument('--status', dest="status", action="store_true",
                        default=False, help='Print out queue status')
    options = parser.parse_args()

    # setup db
    db = None
    if db_config.DB_TYPE == 'mongodb':
        import db.mongodb
        db = db.mongodb.MongoDatabase(db_config)
        config = db.get_config(options.body_uid)
        db.setup(config)

    # set up logging
    logfile = 'scrapearis.log'
    if config['scraper']['log_base_dir'] is not None:
        now = datetime.datetime.utcnow()
        logfile = '%s/%s-%s.log' % (config['scraper']['log_base_dir'],
                                    config['city']['_id'],
                                    now.strftime('%Y%m%d-%H%M'))
    levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    loglevel = 'INFO'
    if config['scraper']['log_level'] is not None:
        loglevel = config['scraper']['log_level']
    logging.basicConfig(
        filename=logfile, level=levels[loglevel],
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    # prevent "Starting new HTTP connection (1):" INFO messages from requests
    requests_log = logging.getLogger("requests")
    requests_log.setLevel(logging.WARNING)

    # interactive logging
    if options.interactive in levels:
        root = logging.getLogger()
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(levels[options.interactive])
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        ch.setFormatter(formatter)
        root.addHandler(ch)

    logging.info('Starting scraper with configuration from "%s" and loglevel "%s"',
                 config['city']['_id'], loglevel)

    # queue status
    if options.status:
        db.queue_status()

    # erase db
    if options.erase_db:
        print "Erasing database"
        db.erase()

    if options.start_month:
        try:
            options.start_month = datetime.datetime.strptime(
                options.start_month, '%Y-%m')
        except ValueError:
            sys.stderr.write("Bad format or invalid month for --start "
                             "parameter. Use 'YYYY-MM'.\n")
            sys.exit()
        if options.end_month:
            try:
                options.end_month = datetime.datetime.strptime(
                    "%s-%s" % (options.end_month, calendar.monthrange(
                        int(options.end_month[0:4]),
                        int(options.end_month[5:7]))[1]),
                    '%Y-%m-%d')
            except ValueError:
                sys.stderr.write("Bad format or invalid month for --end "
                                 "parameter. Use 'YYYY-MM'.\n")
                sys.exit()
            if options.end_month < options.start_month:
                sys.stderr.write("Error with --start and --end parameter: end "
                                 "month should be after start month.\n")
                sys.exit()
        else:
            options.end_month = options.start_month
        options.workfromqueue = True

    # TODO: Autodetect basic type
    if ((config['scraper']['type'] == 'sessionnet-asp')
            or (config['scraper']['type'] == 'sessionnet-php')):
        scraper = ScraperSessionNet(config, db, options)
    elif config['scraper']['type'] == 'allris':
        scraper = ScraperAllRis(config, db, options)

    scraper.guess_system()
    # person
    if options.person_id:
        #scraper.find_person() #should be part of scraper
        #scraper.get_person(person_id=int(options.person_id))
        # should be part of scraper
        scraper.get_person_organization(person_id=int(options.person_id))
    if options.person_url:
        #scraper.find_person() #should be part of scraper
        #scraper.get_person(person_url=options.person_url)
        # should be part of scraper
        scraper.get_person_organization(person_url=options.person_url)
    # organization
    if options.organization_id:
        scraper.get_organization(organization_id=int(options.organization_id))
    if options.organization_url:
        scraper.get_organization(organization_url=options.organization_url)
    # meeting
    if options.meeting_id:
        scraper.get_meeting(meeting_id=int(options.meeting_id))
    if options.meeting_url:
        scraper.get_meeting(meeting_url=options.meeting_url)
    # paper
    if options.paper_id:
        scraper.get_paper(paper_id=int(options.paper_id))
    if options.paper_url:
        scraper.get_paper(paper_url=options.paper_url)


    if options.start_month:
        scraper.find_person()
        scraper.find_meeting(start_date=options.start_month,
                             end_date=options.end_month)

    if options.workfromqueue:
        scraper.work_from_queue()

    logging.info('Scraper finished.')


if __name__ == '__main__':
    main()
