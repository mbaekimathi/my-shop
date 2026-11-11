from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0091_shop_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="auto_delivery_on_way",
            field=models.BooleanField(default=False),
        ),
    ]
