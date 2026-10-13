from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0043_swap_legacy_transfer_people"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="response_note",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Reason when a pending transfer is declined or cancelled.",
            ),
        ),
    ]
