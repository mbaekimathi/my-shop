from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0070_companyprofile_main_website"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_trade_in",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_format_trade_in",
            field=models.CharField(default="TI", max_length=8),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="enable_trade_in",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="receipt_format_trade_in",
            field=models.CharField(default="TI", max_length=8),
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="source_shop",
            field=models.ForeignKey(
                blank=True,
                help_text="Shop that sent the goods on a trade-in receipt.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="trade_ins_sent",
                to="shops.shop",
            ),
        ),
        migrations.AlterField(
            model_name="shopreceipt",
            name="kind",
            field=models.CharField(
                choices=[
                    ("sale", "Sale"),
                    ("credit", "Credit"),
                    ("quotation", "Quotation"),
                    ("trade_out", "Trade out"),
                    ("trade_in", "Trade in"),
                ],
                db_index=True,
                default="sale",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="shopreceipt",
            name="trade_settlements",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Trade settlement events: [{type, amount|value, method?, item_id?, item_name?, qty?, buying_price?, stock_movement_id?, at, by_id}, ...].",
            ),
        ),
    ]
