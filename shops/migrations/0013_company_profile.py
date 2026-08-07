import shops.models
from django.db import migrations, models


def create_default_company_profile(apps, schema_editor):
    CompanyProfile = apps.get_model("shops", "CompanyProfile")
    CompanyProfile.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0012_companypossettings_receipt_paper_width"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(blank=True, default="", max_length=200)),
                (
                    "phone_number",
                    models.CharField(blank=True, default="", max_length=40),
                ),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("location", models.CharField(blank=True, default="", max_length=255)),
                (
                    "logo",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to=shops.models.company_logo_path,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Company profile",
                "verbose_name_plural": "Company profile",
            },
        ),
        migrations.RunPython(create_default_company_profile, migrations.RunPython.noop),
    ]
