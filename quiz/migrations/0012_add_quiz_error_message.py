# Generated migration: add error_message field to Quiz model
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0011_add_quiz_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="quiz",
            name="error_message",
            field=models.TextField(blank=True),
        ),
    ]
