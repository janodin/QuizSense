import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')

import django
django.setup()

from quiz.services.pipeline_service import (
    get_generation_provider,
    GenerationResult,
    GenerationType
)

print("Testing pipeline service...")
provider = get_generation_provider()
print("Provider type:", type(provider).__name__)
print("Providers:", [p.get_provider_name() for p in provider._providers])

result = provider.generate_summary(
    "Python is a programming language. It has dynamic typing and garbage collection.",
    "Test Chapter",
    "N/A"
)
print("Summary result: success=%s, duration=%.1fms" % (result.success, result.duration_ms))
if result.data:
    print("Data preview:", result.data[:100])
if result.error:
    print("Error:", result.error)

print("\nTesting MCQ generation...")
mcq_result = provider.generate_mcq(
    "Python is a programming language. What is the correct answer?",
    "Test Chapter",
    "N/A"
)
print("MCQ result: success=%s, duration=%.1fms" % (mcq_result.success, mcq_result.duration_ms))
if mcq_result.data:
    print("MCQ count:", len(mcq_result.data))
if mcq_result.error:
    print("Error:", mcq_result.error)

print("\nAll tests completed!")