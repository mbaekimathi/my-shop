from django.db import migrations


def sync_require_reference_to_shop_overrides(apps, schema_editor):
    CompanyPosSettings = apps.get_model("shops", "CompanyPosSettings")
    ShopPosSettings = apps.get_model("shops", "ShopPosSettings")
    company = CompanyPosSettings.objects.order_by("pk").first()
    if company is None:
        return
    value = bool(company.require_mpesa_reference_on_confirm)
    ShopPosSettings.objects.filter(override_receipt=True).exclude(
        require_mpesa_reference_on_confirm=value
    ).update(require_mpesa_reference_on_confirm=value)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0101_require_mpesa_reference_on_confirm"),
    ]

    operations = [
        migrations.RunPython(
            sync_require_reference_to_shop_overrides, noop_reverse
        ),
    ]
