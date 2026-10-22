# Generated manually for single-session enforcement

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0010_employeeprofile_store_manager_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeprofile',
            name='active_session_key',
            field=models.CharField(blank=True, default='', editable=False, help_text='Session key of the single live employee session.', max_length=40),
        ),
    ]
