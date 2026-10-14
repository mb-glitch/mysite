"""Verify upgrades retain old push history and generate distinct deduplication keys."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OutboxMigrationTests(TransactionTestCase):
    def test_existing_push_history_survives_upgrade(self):
        old_target = [('notifications', '0001_initial')]
        latest_target = [('notifications', '0002_alter_pushdelivery_options_pushdelivery_channel_and_more')]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            User = apps.get_model('auth', 'User')
            Device = apps.get_model('notifications', 'PushDevice')
            Delivery = apps.get_model('notifications', 'PushDelivery')
            user = User.objects.create(username='migration-user')
            device = Device.objects.create(user=user, token='fake-token', token_hash='a' * 64)
            rows = [Delivery.objects.create(device=device, user=user, event_key=f'event:{i}',
                    group='Librus', title='Old title', body='Old body', state=state, attempts=1)
                    for i, state in enumerate(['sent', 'pending'])]
            executor = MigrationExecutor(connection)
            executor.migrate(latest_target)
            apps = executor.loader.project_state(latest_target).apps
            Delivery = apps.get_model('notifications', 'PushDelivery')
            migrated = list(Delivery.objects.order_by('event_key'))
            self.assertEqual([row.pk for row in migrated], [row.pk for row in rows])
            self.assertEqual([row.state for row in migrated], ['sent', 'pending'])
            self.assertEqual(len({row.dedup_key for row in migrated}), 2)
            for row in migrated:
                self.assertEqual(len(row.dedup_key), 64)
                self.assertEqual(row.recipient_name, 'migration-user')
                self.assertEqual(row.channel, 'push')
                self.assertEqual(row.body, 'Old body')
        finally:
            MigrationExecutor(connection).migrate(latest_target)
