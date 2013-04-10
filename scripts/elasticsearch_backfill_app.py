# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""This app tests that inserting a crash report into elasticsearch works.
It simply uses socorro.external.elasticsearch.crashstorage to send a report
and verifies that it was correctly inserted.

This app can be invoked like this:
    .../scripts/elasticsearch_backfill_app.py --help
set your path to make that simpler
set both socorro and configman in your PYTHONPATH
"""

import datetime
import json

from configman import Namespace, converters
from isodate.isoerror import ISO8601Error

from socorro.app import generic_app
from socorro.external.elasticsearch.crashstorage import (
    ElasticSearchCrashStorage
)
from socorro.external.hbase.crashstorage import HBaseCrashStorage
from socorro.external.hbase.hbase_client import HBaseConnectionForCrashReports
from socorro.lib import datetimeutil


class ElasticsearchBackfillApp(generic_app.App):
    app_name = 'elasticsearch_backfill'
    app_version = '0.1'
    app_description = __doc__

    required_config = Namespace()
    required_config.add_option(
        'elasticsearch_storage_class',
        default=ElasticSearchCrashStorage,
        doc='The class to use to store crash reports in elasticsearch.'
    )
    required_config.add_option(
        'hbase_storage_class',
        default=HBaseCrashStorage,
        doc='The class to use to pull crash reports from HBase.'
    )

    required_config.add_option(
        'end_date',
        default=datetimeutil.utc_now().date(),
        doc='Backfill until this date.',
        from_string_converter=converters.date_converter
    )
    required_config.add_option(
        'duration',
        default=7,
        doc='Number of days to backfill. '
    )
    required_config.add_option(
        'index_doc_number',
        default=50,
        doc='Number of crashes to index at a time. '
    )

    def main(self):
        self.es_storage = self.config.elasticsearch_storage_class(self.config)
        hb_client = HBaseConnectionForCrashReports(
            self.config.hbase_host,
            self.config.hbase_port,
            self.config.hbase_timeout,
        )

        current_date = self.config.end_date

        one_day = datetime.timedelta(days=1)
        for i in range(self.config.duration):
            es_index = self.get_index_for_date(current_date)
            day = current_date.strftime('%y%m%d')

            self.config.logger.info('backfilling crashes for %s', day)

            # First create the index if it doesn't already exist
            self.es_storage.create_index(es_index)

            reports = hb_client.get_list_of_processed_json_for_date(
                day,
                number_of_retries=5
            )

            crashes_to_index = []

            for report in reports:
                processed_crash = self.format_dates_in_crash(
                    json.loads(report)
                )

                # print 'storing %s' % processed_crash['uuid']

                if len(crashes_to_index) > self.config.index_doc_number:
                    # print 'now indexing crashes! '
                    self.index_crashes(es_index, crashes_to_index)
                    crashes_to_index = []

                crashes_to_index.append(processed_crash)

            if len(crashes_to_index) > 0:
                self.index_crashes(es_index, crashes_to_index)

            current_date -= one_day

        return 0

    def get_index_for_date(self, day):
        """return the elasticsearch index for a day"""
        index = self.config.elasticsearch_index

        if not index:
            return None
        if '%' in index:
            index = day.strftime(index)

        return index

    def format_dates_in_crash(self, processed_crash):
        # HBase returns dates in a format that elasticsearch does not
        # understand. To keep our elasticsearch mapping simple, we
        # transform all dates to a recognized format.
        for attr in processed_crash:
            try:
                processed_crash[attr] = datetimeutil.date_to_string(
                    datetimeutil.string_to_datetime(
                        processed_crash[attr]
                    )
                )
            except (ValueError, TypeError, ISO8601Error):
                # the attribute is not a date
                pass

        return processed_crash

    def index_crashes(self, es_index, crashes_to_index):
        self.es_storage.es.bulk_index(
            es_index,
            self.config.elasticsearch_doctype,
            crashes_to_index,
            id_field='uuid'
        )


if __name__ == '__main__':
    generic_app.main(ElasticsearchBackfillApp)
