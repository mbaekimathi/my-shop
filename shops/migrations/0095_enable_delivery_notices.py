from django.db import migrations, models


def enable_delivery_notices(apps, schema_editor):
    Settings = apps.get_model("shops", "CompanyCommunicationsSettings")
    Settings.objects.filter(enable_automations=True).update(
        auto_delivery_on_way=True,
        auto_delivery_complete=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0094_auto_delivery_complete"),
    ]

    operations = [
        migrations.AlterField(
            model_name="companycommunicationssettings",
            name="auto_delivery_on_way",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="companycommunicationssettings",
            name="auto_delivery_complete",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(enable_delivery_notices, migrations.RunPython.noop),
    ]
