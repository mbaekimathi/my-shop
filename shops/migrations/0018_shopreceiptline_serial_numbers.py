from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0017_receipt_qr_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceiptline",
            name="serial_numbers",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
