from django.contrib import admin
from .models import (
	Chapter,
	Topic,
	UploadSession,
	UploadedFile,
	TextbookChunk,
	UploadedChunk,
	Question,
	Quiz,
	QuizAttempt,
	QuizAnswer,
)

admin.site.register(Chapter)
admin.site.register(Topic)
admin.site.register(UploadSession)
admin.site.register(UploadedFile)
admin.site.register(TextbookChunk)
admin.site.register(UploadedChunk)
admin.site.register(Question)
admin.site.register(Quiz)
admin.site.register(QuizAttempt)
admin.site.register(QuizAnswer)

