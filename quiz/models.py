from django.db import models

import json


class VectorEmbedding(models.TextField):
    """Store an embedding vector as JSON text in the database."""

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if isinstance(value, list):
            return value
        return json.loads(value)

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value)


class Chapter(models.Model):
    number = models.PositiveSmallIntegerField(unique=True)
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ['number']

    def __str__(self):
        return f"Chapter {self.number}: {self.title}"


class Topic(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='topics')
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ['chapter__number', 'id']

    def __str__(self):
        return f"{self.chapter} — {self.title}"


class UploadedFile(models.Model):
    FILE_TYPE_CHOICES = [
        ('pdf', 'PDF'),
        ('docx', 'Word Document'),
    ]
    upload_session = models.ForeignKey('UploadSession', on_delete=models.CASCADE, related_name='files', null=True, blank=True)
    chapter = models.ForeignKey(Chapter, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_files')
    file = models.FileField(upload_to='uploads/')
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES)
    extracted_text = models.TextField(blank=True)
    summary = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file.name} ({self.file_type}) — {self.uploaded_at:%Y-%m-%d %H:%M}"


class Question(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='questions')
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True, related_name='questions')
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.SET_NULL, null=True, blank=True, related_name='questions')
    text = models.TextField()
    choice_a = models.CharField(max_length=500)
    choice_b = models.CharField(max_length=500)
    choice_c = models.CharField(max_length=500)
    choice_d = models.CharField(max_length=500)
    correct_answer = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')])
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def choices_items(self):
        return [
            ('A', self.choice_a),
            ('B', self.choice_b),
            ('C', self.choice_c),
            ('D', self.choice_d),
        ]

    def __str__(self):
        return f"Q: {self.text[:60]}..."


class Quiz(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='quizzes')
    upload_session = models.ForeignKey('UploadSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes')
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes')
    questions = models.ManyToManyField(Question, related_name='quizzes')
    status = models.CharField(max_length=50, default='pending', choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True, null=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Quizzes'

    def __str__(self):
        return f"Quiz for {self.chapter} — {self.created_at:%Y-%m-%d %H:%M}"


class QuizAttempt(models.Model):
    RECOMMENDATION_PENDING = 'pending'
    RECOMMENDATION_PROCESSING = 'processing'
    RECOMMENDATION_COMPLETED = 'completed'
    RECOMMENDATION_FAILED = 'failed'
    RECOMMENDATION_STATUS_CHOICES = [
        (RECOMMENDATION_PENDING, 'Pending'),
        (RECOMMENDATION_PROCESSING, 'Processing'),
        (RECOMMENDATION_COMPLETED, 'Completed'),
        (RECOMMENDATION_FAILED, 'Failed'),
    ]

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    session_key = models.CharField(max_length=100, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    total_questions = models.PositiveSmallIntegerField(default=10)
    ai_recommendation = models.TextField(blank=True)
    recommendation_status = models.CharField(max_length=50, default='pending', choices=RECOMMENDATION_STATUS_CHOICES)
    recommendation_error = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Attempt on {self.quiz} — Score: {self.score}/{self.total_questions}"

    def score_percentage(self):
        if self.total_questions == 0:
            return 0
        return round((self.score / self.total_questions) * 100)

    def save(self, *args, **kwargs):
        if self.score > self.total_questions:
            self.score = self.total_questions
        super().save(*args, **kwargs)


class QuizAnswer(models.Model):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    selected_answer = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')])
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ['attempt', 'question']

    def __str__(self):
        return f"Answer to Q{self.question.id} — {'Correct' if self.is_correct else 'Wrong'}"


class UploadSession(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='upload_sessions')
    session_key = models.CharField(max_length=100, blank=True)
    summary = models.TextField(blank=True)
    processing_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    processing_error = models.TextField(blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Upload Session #{self.id} for {self.chapter}"


class TextbookChunk(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='textbook_chunks')
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True, related_name='textbook_chunks')
    source_title = models.CharField(max_length=255)
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    embedding = VectorEmbedding(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['chapter__number', 'source_title', 'chunk_index']

    def __str__(self):
        return f"TextbookChunk {self.source_title}#{self.chunk_index}"


class UploadedChunk(models.Model):
    upload_session = models.ForeignKey(UploadSession, on_delete=models.CASCADE, related_name='chunks')
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.CASCADE, related_name='chunks')
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='uploaded_chunks')
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    embedding = VectorEmbedding(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['upload_session_id', 'uploaded_file_id', 'chunk_index']

    def __str__(self):
        return f"UploadedChunk session={self.upload_session_id} file={self.uploaded_file_id}#{self.chunk_index}"

