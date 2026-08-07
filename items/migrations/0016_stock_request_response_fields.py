from django.db import migrations, models
import django.db.models.deletion


def normalize_request_status(apps, schema_editor):
    StockMovement = apps.get_model("items", "StockMovement")
    StockMovement.objects.filter(
        movement_type="request", request_status="seen"
    ).update(request_status="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
        ("items", "0015_stock_request_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="requester_notified",
            field=models.BooleanField(
                default=True,
                help_text="False after a decision until the requesting shop acknowledges it.",
            ),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="responded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="stock_request_responses",
                to="employees.employeeprofile",
            ),
        ),
        migrations.RunPython(normalize_request_status, migrations.RunPython.noop),
    ]
