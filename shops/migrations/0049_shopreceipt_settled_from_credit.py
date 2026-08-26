from django.db import migrations, models


def mark_settled_from_credit_events(apps, schema_editor):
    """Flag sales that still have credit-account audit events linked."""
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    ClientCreditAccountEvent = apps.get_model("shops", "ClientCreditAccountEvent")
    receipt_ids = (
        ClientCreditAccountEvent.objects.filter(receipt_id__isnull=False)
        .values_list("receipt_id", flat=True)
        .distinct()
    )
    ShopReceipt.objects.filter(kind="sale", pk__in=receipt_ids).update(
        settled_from_credit=True
    )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0048_company_developer_payment_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="settled_from_credit",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text=(
                    "True when this sale was converted from a fully paid credit receipt."
                ),
            ),
        ),
        migrations.RunPython(mark_settled_from_credit_events, noop_reverse),
    ]
