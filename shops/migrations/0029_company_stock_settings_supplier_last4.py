from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0028_company_stock_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="companystocksettings",
            name="enable_supplier_last4_search",
            field=models.BooleanField(
                default=True,
                help_text="Allow searching suppliers by the last 4 phone digits and autofill full details.",
            ),
        ),
    ]
