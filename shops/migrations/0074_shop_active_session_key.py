# Generated manually for single-session enforcement

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shops', '0073_show_client_phone_help_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='shop',
            name='active_session_key',
            field=models.CharField(blank=True, default='', editable=False, help_text='Session key of the single live shop portal session.', max_length=40),
        ),
    ]
