from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0049_shopreceipt_settled_from_credit"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceiptline",
            name="return_batches",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Per-return events: [{qty, at, by_id, serials}, ...]. "
                    "Used so stock reports attribute returns to the day they happened."
                ),
            ),
        ),
    ]
