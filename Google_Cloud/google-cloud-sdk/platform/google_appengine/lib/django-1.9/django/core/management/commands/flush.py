from __future__ import unicode_literals

import sys
from importlib import import_module

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.core.management.sql import emit_post_migrate_signal, sql_flush
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.utils import six
from django.utils.six.moves import input


class Command(BaseCommand):
    help = ('Removes ALL DATA from the database, including data added during '
           'migrations. Does not achieve a "fresh install" state.')

    def add_arguments(self, parser):
        parser.add_argument('--noinput', '--no-input',
            action='store_false', dest='interactive', default=True,
            help='Tells Django to NOT prompt the user for input of any kind.')
        parser.add_argument('--database', action='store', dest='database',
            default=DEFAULT_DB_ALIAS,
            help='Nominates a database to flush. Defaults to the "default" database.')

    def handle(self, **options):
        database = options.get('database')
        connection = connections[database]
        verbosity = options.get('verbosity')
        interactive = options.get('interactive')
        # The following are stealth options used by Django's internals.
        reset_sequences = options.get('reset_sequences', True)
        allow_cascade = options.get('allow_cascade', False)
        inhibit_post_migrate = options.get('inhibit_post_migrate', False)

        self.style = no_style()

        # Import the 'management' module within each installed app, to register
        # dispatcher events.
        for app_config in apps.get_app_configs():
            try:
                import_module('.management', app_config.name)
            except ImportError:
                pass

        sql_list = sql_flush(self.style, connection, only_django=True,
                             reset_sequences=reset_sequences,
                             allow_cascade=allow_cascade)

        if interactive:
            confirm = input("""You have requested a flush of the database.
This will IRREVERSIBLY DESTROY all data currently in the %r database,
and return each table to an empty state.
Are you sure you want to do this?

    Type 'yes' to continue, or 'no' to cancel: """ % connection.settings_dict['NAME'])
        else:
            confirm = 'yes'

        if confirm == 'yes':
            try:
                with transaction.atomic(using=database,
                                        savepoint=connection.features.can_rollback_ddl):
                    with connection.cursor() as cursor:
                        for sql in sql_list:
                            cursor.execute(sql)
            except Exception as e:
                new_msg = (
                    "Database %s couldn't be flushed. Possible reasons:\n"
                    "  * The database isn't running or isn't configured correctly.\n"
                    "  * At least one of the expected database tables doesn't exist.\n"
                    "  * The SQL was invalid.\n"
                    "Hint: Look at the output of 'django-admin sqlflush'. "
                    "That's the SQL this command wasn't able to run.\n"
                    "The full error: %s") % (connection.settings_dict['NAME'], e)
                six.reraise(CommandError, CommandError(new_msg), sys.exc_info()[2])

            # Empty sql_list may signify an empty database and post_migrate would then crash
            if sql_list and not inhibit_post_migrate:
                # Emit the post migrate signal. This allows individual applications to
                # respond as if the database had been migrated from scratch.
                emit_post_migrate_signal(verbosity, interactive, database)
        else:
            self.stdout.write("Flush cancelled.\n")
