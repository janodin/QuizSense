# Generated migration: add status field to Quiz model
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0010_alter_textbookchunk_embedding_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="quiz",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
