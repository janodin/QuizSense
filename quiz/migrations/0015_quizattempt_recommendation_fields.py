from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quiz', '0014_add_processing_stage'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name='uploadsession',
                    name='processing_stage',
                    field=models.CharField(
                        blank=True,
                        choices=[
                            ('extracting', 'Extracting text'),
                            ('summarizing', 'Generating summary'),
                            ('embedding', 'Building embeddings'),
                            ('finalizing', 'Finalizing'),
                        ],
                        default='',
                        max_length=20,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name='quizattempt',
            name='recommendation_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='quizattempt',
            name='recommendation_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('generating', 'Generating'),
                    ('completed', 'Completed'),
                    ('failed', 'Failed'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
    ]
