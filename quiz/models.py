from django.db import models
from pgvector.django import VectorField


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
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='quizzes')
    upload_session = models.ForeignKey('UploadSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes')
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes')
    questions = models.ManyToManyField(Question, related_name='quizzes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Quizzes'

    def __str__(self):
        return f"Quiz for {self.chapter} — {self.created_at:%Y-%m-%d %H:%M}"


class QuizAttempt(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    session_key = models.CharField(max_length=100, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    total_questions = models.PositiveSmallIntegerField(default=10)
    ai_recommendation = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Attempt on {self.quiz} — Score: {self.score}/{self.total_questions}"

    def score_percentage(self):
        if self.total_questions == 0:
            return 0
        return round((self.score / self.total_questions) * 100)
    
    def incorrect_count(self):
        return self.total_questions - self.score


class QuizAnswer(models.Model):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    selected_answer = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')])
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return f"Answer to Q{self.question.id} — {'Correct' if self.is_correct else 'Wrong'}"


class UploadSession(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='upload_sessions')
    session_key = models.CharField(max_length=100, blank=True)
    summary = models.TextField(blank=True)
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
    embedding = VectorField(dimensions=768)
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
    embedding = VectorField(dimensions=768)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['upload_session_id', 'uploaded_file_id', 'chunk_index']

    def __str__(self):
        return f"UploadedChunk session={self.upload_session_id} file={self.uploaded_file_id}#{self.chunk_index}"

