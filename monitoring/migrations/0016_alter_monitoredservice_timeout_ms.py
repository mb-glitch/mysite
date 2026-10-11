import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('monitoring', '0015_rename_user_monitoredservice_owner_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='monitoredservice',
            name='timeout_ms',
            field=models.IntegerField(
                default=60_000,
                help_text='Oczekiwany odstęp między raportami w milisekundach',
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
    ]
