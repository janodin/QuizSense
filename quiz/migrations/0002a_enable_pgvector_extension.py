from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('quiz', '0002_uploadedfile_summary'),
    ]
    
    run_before = [
        ('quiz', '0003_textbookchunk_uploadsession_uploadedchunk_and_more'),
    ]

    operations = [
        CreateExtension('vector'),
    ]
