import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')
import django
django.setup()

from quiz.services.pipeline_service import (
    MiniMaxProvider,
    GenerationResult,
    GenerationType
)

# Directly test MiniMaxProvider.generate_mcq
provider = MiniMaxProvider()

try:
    result = provider.generate_mcq(
        'Python is a programming language. Variables store data.',
        'Test Chapter',
        'N/A'
    )
    print('Result type:', type(result))
    print('Result count:', len(result))
    if result:
        print('First item:', result[0])
except Exception as e:
    print('Error:', type(e).__name__, str(e))