from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0038_item_activity_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="rider_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional rider / courier name on inter-shop transfers.",
                max_length=120,
            ),
        ),
    ]
