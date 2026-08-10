from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0032_company_working_hours_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="companyworkinghourssettings",
            name="enabled",
            field=models.BooleanField(
                default=False,
                help_text="Prompt shop staff to open/close the till during working hours.",
            ),
        ),
    ]
