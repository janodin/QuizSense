# Generated manually — add processing_stage field to UploadSession via raw SQL
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('quiz', '0013_add_quiz_generated_at'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE quiz_uploadsession
                ADD COLUMN processing_stage VARCHAR(20) DEFAULT '';
            """,
            reverse_sql="""
                ALTER TABLE quiz_uploadsession
                DROP COLUMN IF EXISTS processing_stage;
            """,
        ),
    ]
