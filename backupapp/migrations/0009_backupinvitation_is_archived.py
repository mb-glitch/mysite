from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('backupapp', '0008_alter_backupinvitation_token_link_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='backupinvitation',
            name='is_archived',
            field=models.BooleanField(
                default=False, verbose_name='W archiwum',
                help_text='Ukryj zaproszenie i wyłącz jego link instalacyjny.',
            ),
        ),
    ]
