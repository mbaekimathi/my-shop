from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0086_companyprofile_theme"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="theme",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Shop brand palette. Blank uses the company theme.",
                max_length=32,
            ),
        ),
    ]
