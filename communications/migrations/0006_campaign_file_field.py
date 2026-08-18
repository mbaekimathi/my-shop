from django.db import migrations, models

import communications.models


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0005_whatsapp_group"),
    ]

    operations = [
        migrations.AlterField(
            model_name="broadcastcampaign",
            name="image",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=communications.models.campaign_image_path,
            ),
        ),
    ]
