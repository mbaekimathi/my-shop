from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0085_show_return_notice_on_receipt"),
    ]

    operations = [
        migrations.AddField(
            model_name="companyprofile",
            name="theme",
            field=models.CharField(
                blank=True,
                default="harbor",
                help_text="Company brand palette applied across MY-SHOP (independent of light/dark).",
                max_length=32,
            ),
        ),
    ]
