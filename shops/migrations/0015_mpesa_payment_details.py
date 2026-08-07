from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0014_receipt_number_formats"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="mpesa_account_number",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="mpesa_business_number",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="mpesa_collection_type",
            field=models.CharField(
                blank=True,
                choices=[("paybill", "Paybill"), ("buy_goods", "Buy Goods")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="mpesa_till_number",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
