from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0006_shop_day_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_discount",
            field=models.BooleanField(default=True),
        ),
    ]
