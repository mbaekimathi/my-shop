from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0036_fix_shops_id_autoincrement"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="credit_due_date",
            field=models.DateField(
                blank=True,
                help_text="Expected payment date for credit sales.",
                null=True,
            ),
        ),
    ]
