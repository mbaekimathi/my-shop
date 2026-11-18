from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0014_web_push_subscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeprofile",
            name="extra_roles",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Additional roles this employee may sign in as "
                    "(comma-separated role keys)."
                ),
                max_length=200,
            ),
        ),
    ]
