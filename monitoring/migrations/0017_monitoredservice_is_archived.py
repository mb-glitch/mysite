from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('monitoring', '0016_alter_monitoredservice_timeout_ms'),
    ]

    operations = [
        migrations.AddField(
            model_name='monitoredservice',
            name='is_archived',
            field=models.BooleanField(
                default=False, verbose_name='W archiwum',
                help_text='Ukryj z bieżących list i wyłącz alerty. Token nadal przyjmuje raporty.',
            ),
        ),
    ]
