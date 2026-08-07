from django.db import migrations, models


def set_pending_for_existing_requests(apps, schema_editor):
    StockMovement = apps.get_model("items", "StockMovement")
    StockMovement.objects.filter(movement_type="request").filter(
        models.Q(request_status="") | models.Q(request_status__isnull=True)
    ).update(request_status="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0014_stock_line_refund_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="request_status",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Used for stock requests only.",
                max_length=16,
            ),
        ),
        migrations.RunPython(set_pending_for_existing_requests, migrations.RunPython.noop),
    ]
