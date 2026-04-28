from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quiz', '0007b_create_chunk_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='uploadsession',
            name='processing_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='uploadsession',
            name='processing_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='uploadsession',
            name='processing_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='uploadsession',
            name='processing_status',
            field=models.CharField(
                choices=[('pending', 'Pending'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed')],
                default='pending',
                max_length=20,
            ),
        ),
    ]
