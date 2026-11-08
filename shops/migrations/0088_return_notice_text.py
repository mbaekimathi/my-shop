from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0087_shop_theme"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="return_notice_text",
            field=models.CharField(
                blank=True,
                default="Goods once bought cannot be returned",
                help_text="Return-policy line printed on sale and credit receipts when enabled.",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="return_notice_text",
            field=models.CharField(
                blank=True,
                default="Goods once bought cannot be returned",
                help_text="Return-policy line printed on sale and credit receipts when enabled.",
                max_length=200,
            ),
        ),
        migrations.AlterField(
            model_name="companypossettings",
            name="show_return_notice_on_receipt",
            field=models.BooleanField(
                default=True,
                help_text="When on, sale and credit receipts print the return-policy line.",
            ),
        ),
        migrations.AlterField(
            model_name="shoppossettings",
            name="show_return_notice_on_receipt",
            field=models.BooleanField(
                default=True,
                help_text="When on, sale and credit receipts print the return-policy line.",
            ),
        ),
    ]
