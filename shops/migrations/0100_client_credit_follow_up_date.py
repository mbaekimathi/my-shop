from django.db import migrations, models


def forwards_copy_receipt_follow_ups(apps, schema_editor):
    """Prefer the soonest upcoming receipt follow-up when seeding the account date."""
    Client = apps.get_model("shops", "Client")
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    today = None
    try:
        from django.utils import timezone

        today = timezone.localdate()
    except Exception:
        pass

    for client in Client.objects.all().iterator():
        qs = ShopReceipt.objects.filter(
            client_id=client.pk,
            kind="credit",
            credit_follow_up_date__isnull=False,
        ).exclude(status="cancelled")
        if not qs.exists():
            continue
        dates = list(qs.values_list("credit_follow_up_date", flat=True))
        if not dates:
            continue
        if today is not None:
            upcoming = sorted(d for d in dates if d >= today)
            chosen = upcoming[0] if upcoming else max(dates)
        else:
            chosen = max(dates)
        reminder = (
            qs.filter(credit_follow_up_date=chosen)
            .exclude(credit_due_reminder_sent_on__isnull=True)
            .values_list("credit_due_reminder_sent_on", flat=True)
            .first()
        )
        client.credit_follow_up_date = chosen
        client.credit_follow_up_reminder_sent_on = reminder
        client.save(
            update_fields=["credit_follow_up_date", "credit_follow_up_reminder_sent_on"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0099_shopreceipt_credit_follow_up_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="credit_follow_up_date",
            field=models.DateField(
                blank=True,
                help_text="Follow-up date for this client's open credit account.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="credit_follow_up_reminder_sent_on",
            field=models.DateField(
                blank=True,
                help_text="Local date the morning-of-follow-up client reminder was sent.",
                null=True,
            ),
        ),
        migrations.RunPython(forwards_copy_receipt_follow_ups, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="shopreceipt",
            name="credit_follow_up_date",
        ),
        migrations.RemoveField(
            model_name="shopreceipt",
            name="credit_due_reminder_sent_on",
        ),
    ]
