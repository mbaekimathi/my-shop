from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0013_company_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_format_credit",
            field=models.CharField(default="CRD", max_length=8),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_format_quotation",
            field=models.CharField(default="QTN", max_length=8),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_format_sale",
            field=models.CharField(default="SAL", max_length=8),
        ),
        migrations.AlterField(
            model_name="shopreceipt",
            name="receipt_number",
            field=models.CharField(db_index=True, max_length=40, unique=True),
        ),
    ]
