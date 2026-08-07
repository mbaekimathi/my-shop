from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="password_hash",
            field=models.CharField(default="!", max_length=128),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="shop",
            name="login_code",
            field=models.CharField(db_index=True, max_length=6, unique=True),
        ),
    ]
