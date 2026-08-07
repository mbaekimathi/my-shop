from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0002_item_is_suspended"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="track_serial_number",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
