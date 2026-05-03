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


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
	list_display = ['id', 'number', 'title']
	search_fields = ['title']
	ordering = ['number']


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
	list_display = ['id', 'title', 'chapter']
	search_fields = ['title', 'chapter__title']
	list_filter = ['chapter']
	ordering = ['chapter__number', 'id']


@admin.register(UploadSession)
class UploadSessionAdmin(admin.ModelAdmin):
	list_display = ['id', 'chapter', 'session_key', 'processing_status', 'created_at']
	search_fields = ['chapter__title']
	list_filter = ['processing_status', 'chapter']
	ordering = ['-created_at']


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
	list_display = ['id', 'chapter', 'file_type', 'uploaded_at']
	search_fields = ['file__name']
	list_filter = ['file_type', 'chapter']
	ordering = ['-uploaded_at']


@admin.register(TextbookChunk)
class TextbookChunkAdmin(admin.ModelAdmin):
	list_display = ['id', 'chapter', 'source_title', 'chunk_index', 'created_at']
	search_fields = ['source_title', 'content']
	list_filter = ['chapter']
	ordering = ['chapter__number', 'source_title', 'chunk_index']


@admin.register(UploadedChunk)
class UploadedChunkAdmin(admin.ModelAdmin):
	list_display = ['id', 'upload_session', 'uploaded_file', 'chapter', 'chunk_index', 'created_at']
	list_filter = ['chapter', 'upload_session']
	ordering = ['upload_session_id', 'uploaded_file_id', 'chunk_index']


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
	list_display = ['id', 'text', 'chapter', 'topic', 'correct_answer', 'created_at']
	search_fields = ['text']
	list_filter = ['chapter', 'topic']
	ordering = ['-created_at']


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
	list_display = ['id', 'chapter', 'status', 'generated_at', 'created_at']
	search_fields = ['chapter__title']
	list_filter = ['status', 'chapter']
	ordering = ['-created_at']


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
	list_display = ['id', 'quiz', 'session_key', 'score', 'total_questions', 'recommendation_status', 'started_at']
	search_fields = ['quiz__chapter__title']
	list_filter = ['recommendation_status', 'quiz__chapter']
	ordering = ['-started_at']


@admin.register(QuizAnswer)
class QuizAnswerAdmin(admin.ModelAdmin):
	list_display = ['id', 'attempt', 'question', 'selected_answer', 'is_correct']
	list_filter = ['is_correct', 'question__chapter']
	ordering = ['attempt_id', 'question_id']
