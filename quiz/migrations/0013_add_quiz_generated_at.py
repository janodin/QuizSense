# Generated migration: add generated_at field to Quiz model
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0012_add_quiz_error_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="quiz",
            name="generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
