from django.db import migrations, models


def nexus_rushtech_to_nexus(apps, schema_editor):
    CompanyDarajaSettings = apps.get_model("shops", "CompanyDarajaSettings")
    MpesaStkPayment = apps.get_model("shops", "MpesaStkPayment")
    CompanyDarajaSettings.objects.filter(stk_provider="nexus_rushtech").update(
        stk_provider="nexus"
    )
    MpesaStkPayment.objects.filter(stk_provider="nexus_rushtech").update(
        stk_provider="nexus"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0060_stk_nexus_provider"),
    ]

    operations = [
        migrations.RunPython(nexus_rushtech_to_nexus, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="companydarajasettings",
            name="stk_provider",
            field=models.CharField(
                choices=[
                    ("daraja", "Safaricom Daraja (your credentials)"),
                    ("nexus", "Nexus collections API"),
                ],
                default="daraja",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="mpesastkpayment",
            name="stk_provider",
            field=models.CharField(
                blank=True,
                choices=[
                    ("daraja", "Safaricom Daraja (your credentials)"),
                    ("nexus", "Nexus collections API"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
