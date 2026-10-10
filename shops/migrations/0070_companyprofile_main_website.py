from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0069_shopreceiptline_return_batches_reason_help"),
    ]

    operations = [
        migrations.AddField(
            model_name="companyprofile",
            name="main_website_domain",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Apex domain that should show the main shop website, e.g. richcom.co.ke.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="companyprofile",
            name="main_website_shop",
            field=models.ForeignKey(
                blank=True,
                help_text="Shop catalogue shown on the main (apex) domain.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="shops.shop",
            ),
        ),
    ]
