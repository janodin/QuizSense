"""
Minimal automated tests for QuizSense.

Run with:
  python manage.py test quiz.tests --settings=quizsense.settings
  python manage.py test quiz.tests.ChunkingServiceTest --settings=quizsense.settings  (no DB)
"""

import json
import re
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponseForbidden

from quiz.models import Chapter, Topic, UploadSession, UploadedFile, Quiz, QuizAttempt, QuizAnswer
from quiz.views import (
    _check_ownership,
    _get_session_key,
    submit_quiz,
    study_summary,
    quiz_results,
    review_quiz,
)


def _parse_mcq_response(raw):
    """Parse MCQ JSON response from AI."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"AI Response parsing failed. Raw starts with: {raw[:50]}")

    try:
        questions = json.loads(cleaned[start:end + 1])
        return questions[:10]
    except Exception as e:
        raise ValueError(f"JSON Parse Error: {e}")


# ---------------------------------------------------------------------------
# Unit tests — no database required
# ---------------------------------------------------------------------------

class ChunkingServiceTest(TestCase):
    """Test chunking logic without any external dependencies."""

    def test_split_text_into_chunks_short_text(self):
        from quiz.services.chunking_service import split_text_into_chunks
        text = "Hello world. This is a short text."
        chunks = split_text_into_chunks(text, chunk_size_words=500, overlap_words=100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_split_text_into_chunks_exact_boundary(self):
        from quiz.services.chunking_service import split_text_into_chunks
        # 500 words exactly → 1 chunk
        words = " ".join([f"word{i}" for i in range(500)])
        chunks = split_text_into_chunks(words, chunk_size_words=500, overlap_words=100)
        self.assertEqual(len(chunks), 1)

    def test_split_text_into_chunks_two_chunks(self):
        from quiz.services.chunking_service import split_text_into_chunks
        # 1000 words with 500 chunk size → 2 chunks
        words = " ".join([f"word{i}" for i in range(1000)])
        chunks = split_text_into_chunks(words, chunk_size_words=500, overlap_words=100)
        self.assertEqual(len(chunks), 2)

    def test_split_text_into_chunks_overlap(self):
        from quiz.services.chunking_service import split_text_into_chunks
        # 1000 words → 2 chunks with 100-word overlap
        words = " ".join([f"word{i}" for i in range(1000)])
        chunks = split_text_into_chunks(words, chunk_size_words=500, overlap_words=100)
        self.assertEqual(len(chunks), 2)
        # The two chunks should share ~100 words at the boundary
        words_in_chunk1 = set(chunks[0].split())
        words_in_chunk2 = set(chunks[1].split())
        overlap = words_in_chunk1 & words_in_chunk2
        self.assertGreater(len(overlap), 0, "Chunks should overlap")

    def test_split_text_into_chunks_empty(self):
        from quiz.services.chunking_service import split_text_into_chunks
        self.assertEqual(split_text_into_chunks(""), [])
        self.assertEqual(split_text_into_chunks(None), [])

    def test_split_text_into_chunks_whitespace_normalized(self):
        from quiz.services.chunking_service import split_text_into_chunks
        text = "Hello\n\n\n   world    \t  is   \n  here.  "
        chunks = split_text_into_chunks(text, chunk_size_words=500, overlap_words=100)
        self.assertEqual(len(chunks), 1)
        # Internal whitespace normalized to single spaces
        self.assertNotIn("\n", chunks[0])
        self.assertNotIn("\t", chunks[0])


class MCQParserTest(TestCase):
    """Test MCQ JSON parsing robustness."""

    def test_parse_valid_json_array(self):
        raw = '[{"question":"What is a variable?","choices":{"A":"A storage location","B":"A function","C":"A loop","D":"A class"},"correct_answer":"A","topic":"Variables"}]'
        result = _parse_mcq_response(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["question"], "What is a variable?")
        self.assertEqual(result[0]["correct_answer"], "A")

    def test_parse_json_with_code_fences(self):
        raw = '```json\n[{"question":"What is a variable?","choices":{"A":"A","B":"B","C":"C","D":"D"},"correct_answer":"A","topic":"Variables"}]\n```'
        result = _parse_mcq_response(raw)
        self.assertEqual(len(result), 1)

    def test_parse_json_truncated_brackets(self):
        # Parser finds [ and ] boundaries correctly
        raw = '[{"question":"Q1?","choices":{"A":"a","B":"b","C":"c","D":"d"},"correct_answer":"B","topic":"T1"}'
        result = _parse_mcq_response(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["correct_answer"], "B")

    def test_parse_malformed_raises_valueerror(self):
        raw = "This is not JSON at all"
        with self.assertRaises(ValueError):
            _parse_mcq_response(raw)

    def test_parse_caps_correct_answer(self):
        raw = '[{"question":"Q1?","choices":{"A":"a","B":"b","C":"c","D":"d"},"correct_answer":"C","topic":"T1"}]'
        result = _parse_mcq_response(raw)
        self.assertIn(result[0]["correct_answer"], ["A", "B", "C", "D"])


# ---------------------------------------------------------------------------
# View / integration tests — database required
# ---------------------------------------------------------------------------

class SessionOwnershipTest(TestCase):
    """Test session ownership checks on views."""

    def setUp(self):
        self.factory = RequestFactory()
        self.chapter = Chapter.objects.create(number=1, title="Test Chapter")
        self.topic = Topic.objects.create(chapter=self.chapter, title="Test Topic")

        # Create two sessions with different session keys
        self.session1 = UploadSession.objects.create(
            chapter=self.chapter,
            session_key="session_key_1",
        )
        self.session2 = UploadSession.objects.create(
            chapter=self.chapter,
            session_key="session_key_2",
        )

    def _add_session_to_request(self, request, session_key):
        """Simulate a request with an established session key."""
        middleware = SessionMiddleware(lambda request: None)
        middleware.process_request(request)
        request.session.save()
        if session_key:
            request.session.session_key = session_key
        return request

    def test_check_ownership_same_session(self):
        request = self.factory.get("/")
        request = self._add_session_to_request(request, "session_key_1")
        self.assertTrue(_check_ownership(request, self.session1))

    def test_check_ownership_different_session(self):
        request = self.factory.get("/")
        request = self._add_session_to_request(request, "session_key_2")
        self.assertFalse(_check_ownership(request, self.session1))

    def test_check_ownership_no_session_key_on_object(self):
        # Object with blank session_key — should still work (no false positives)
        obj = UploadSession(chapter=self.chapter, session_key="")
        request = self.factory.get("/")
        request = self._add_session_to_request(request, None)
        # Blank vs blank → equal → True
        self.assertTrue(_check_ownership(request, obj))


class BlankAnswerHandlingTest(TestCase):
    """Test that unanswered questions are blocked at submission."""

    def setUp(self):
        self.chapter = Chapter.objects.create(number=1, title="Test Chapter")
        self.topic = Topic.objects.create(chapter=self.chapter, title="Test Topic")
        self.upload_session = UploadSession.objects.create(
            chapter=self.chapter,
            session_key="test_session",
        )
        self.uploaded_file = UploadedFile.objects.create(
            upload_session=self.upload_session,
            chapter=self.chapter,
            file="test.pdf",
            file_type="pdf",
            extracted_text="Sample text for testing.",
        )
        self.quiz = Quiz.objects.create(
            chapter=self.chapter,
            upload_session=self.upload_session,
            uploaded_file=self.uploaded_file,
        )
        # Simpler: create questions directly
        from quiz.models import Question
        Question.objects.all().delete()
        self.q1 = Question.objects.create(
            chapter=self.chapter, topic=self.topic, uploaded_file=self.uploaded_file,
            text="What is 2+2?", choice_a="Three", choice_b="Four",
            choice_c="Five", choice_d="Six", correct_answer="B",
        )
        self.q2 = Question.objects.create(
            chapter=self.chapter, topic=self.topic, uploaded_file=self.uploaded_file,
            text="What is the capital of France?", choice_a="London", choice_b="Berlin",
            choice_c="Paris", choice_d="Rome", correct_answer="C",
        )
        self.q3 = Question.objects.create(
            chapter=self.chapter, topic=self.topic, uploaded_file=self.uploaded_file,
            text="What is H2O?", choice_a="Water", choice_b="Oxygen",
            choice_c="Hydrogen", choice_d="Carbon", correct_answer="A",
        )
        self.quiz.questions.add(self.q1, self.q2, self.q3)

    def _make_post_request(self, data):
        factory = RequestFactory()
        request = factory.post(f"/quiz/{self.quiz.id}/submit/", data)
        # Add session
        from django.contrib.sessions.middleware import SessionMiddleware
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        request.session.session_key = "test_session"
        return request

    def test_submit_with_all_answers_creates_attempt(self):
        request = self._make_post_request({
            f"q_{self.q1.id}": "B",
            f"q_{self.q2.id}": "C",
            f"q_{self.q3.id}": "A",
        })
        response = submit_quiz(request, self.quiz.id)
        # Should redirect to results (status 302)
        self.assertEqual(response.status_code, 302)
        self.assertIn("results", response.url)
        # Verify attempt was created with correct score
        attempt = QuizAttempt.objects.get(quiz=self.quiz)
        self.assertEqual(attempt.score, 3)
        self.assertEqual(attempt.total_questions, 3)

    def test_submit_with_one_missing_answer_returns_error(self):
        request = self._make_post_request({
            f"q_{self.q1.id}": "B",
            # q2 missing
            f"q_{self.q3.id}": "A",
        })
        response = submit_quiz(request, self.quiz.id)
        # Should redirect back to quiz, not create an attempt
        self.assertEqual(response.status_code, 302)
        self.assertIn("take_quiz", response.url)
        self.assertEqual(QuizAttempt.objects.filter(quiz=self.quiz).count(), 0)

    def test_submit_with_all_missing_answers_returns_error(self):
        request = self._make_post_request({})  # No answers at all
        response = submit_quiz(request, self.quiz.id)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, f'/quiz/{self.quiz.id}/take/')


class QuizScoringTest(TestCase):
    """Test quiz scoring logic."""

    def setUp(self):
        self.chapter = Chapter.objects.create(number=1, title="Test Chapter")
        self.topic = Topic.objects.create(chapter=self.chapter, title="Test Topic")
        self.upload_session = UploadSession.objects.create(
            chapter=self.chapter, session_key="test_session",
        )
        self.uploaded_file = UploadedFile.objects.create(
            upload_session=self.upload_session, chapter=self.chapter,
            file="test.pdf", file_type="pdf", extracted_text="Sample text.",
        )
        from quiz.models import Question
        self.q1 = Question.objects.create(
            chapter=self.chapter, topic=self.topic, uploaded_file=self.uploaded_file,
            text="Q1", choice_a="A", choice_b="B", choice_c="C", choice_d="D",
            correct_answer="A",
        )
        self.q2 = Question.objects.create(
            chapter=self.chapter, topic=self.topic, uploaded_file=self.uploaded_file,
            text="Q2", choice_a="A", choice_b="B", choice_c="C", choice_d="D",
            correct_answer="B",
        )
        self.quiz = Quiz.objects.create(
            chapter=self.chapter, upload_session=self.upload_session,
            uploaded_file=self.uploaded_file,
        )
        self.quiz.questions.add(self.q1, self.q2)

    def test_score_calculation(self):
        factory = RequestFactory()
        request = factory.post(f"/quiz/{self.quiz.id}/submit/", {
            f"q_{self.q1.id}": "A",  # correct
            f"q_{self.q2.id}": "A",  # wrong (correct is B)
        })
        from django.contrib.sessions.middleware import SessionMiddleware
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        request.session.session_key = "test_session"

        submit_quiz(request, self.quiz.id)
        attempt = QuizAttempt.objects.get(quiz=self.quiz)
        self.assertEqual(attempt.score, 1)
        self.assertEqual(attempt.total_questions, 2)
        self.assertEqual(attempt.score_percentage(), 50)


# ---------------------------------------------------------------------------
# AI Provider fallback tests
# ---------------------------------------------------------------------------

class AIProviderFallbackTest(TestCase):
    """Test that the AI provider correctly falls back to a smaller model."""

    @patch('requests.post')
    @patch('time.sleep')
    def test_fallback_triggered_on_primary_timeout(self, mock_sleep, mock_post):
        """Primary model times out 3 times, then fallback succeeds."""
        import requests as real_requests

        call_count = {'value': 0}

        def side_effect(*args, **kwargs):
            call_count['value'] += 1
            if call_count['value'] <= 3:
                raise real_requests.exceptions.Timeout("Connection timed out")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "Fallback model response"}}]
            }
            return response

        mock_post.side_effect = side_effect

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request_with_fallback("test prompt")
        self.assertEqual(result, "Fallback model response")
        self.assertEqual(call_count['value'], 4)

    @patch('requests.post')
    @patch('time.sleep')
    def test_fallback_triggered_on_primary_502(self, mock_sleep, mock_post):
        """Primary model returns 502 errors, fallback succeeds."""
        call_count = {'value': 0}

        def side_effect(*args, **kwargs):
            call_count['value'] += 1
            if call_count['value'] <= 3:
                response = MagicMock()
                response.status_code = 502
                response.text = "Bad Gateway"
                response.json.side_effect = Exception("No JSON")
                return response
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "Fallback succeeded"}}]
            }
            return response

        mock_post.side_effect = side_effect

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request_with_fallback("test prompt")
        self.assertEqual(result, "Fallback succeeded")
        self.assertEqual(call_count['value'], 4)

    @patch('requests.post')
    @patch('time.sleep')
    def test_primary_succeeds_no_fallback(self, mock_sleep, mock_post):
        """Primary model succeeds on first try, fallback never called."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "Primary model response"}}]
        }
        mock_post.return_value = response

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request_with_fallback("test prompt")
        self.assertEqual(result, "Primary model response")
        self.assertEqual(mock_post.call_count, 1)

    @patch('requests.post')
    @patch('time.sleep')
    def test_all_attempts_fail_raises_error(self, mock_sleep, mock_post):
        """Both primary and fallback exhaust all retries."""
        import requests as real_requests

        def side_effect(*args, **kwargs):
            raise real_requests.exceptions.Timeout("Always timeout")

        mock_post.side_effect = side_effect

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 1

        with self.assertRaises(ValueError) as ctx:
            provider._make_request_with_fallback("test prompt")

        self.assertIn("All", str(ctx.exception))
        self.assertIn("attempts failed", str(ctx.exception))

    @patch('requests.post')
    @patch('time.sleep')
    def test_retry_on_connection_error(self, mock_sleep, mock_post):
        """Connection error triggers retry, second attempt succeeds."""
        import requests as real_requests

        call_count = {'value': 0}

        def side_effect(*args, **kwargs):
            call_count['value'] += 1
            if call_count['value'] == 1:
                raise real_requests.exceptions.ConnectionError("Connection refused")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "Recovered after connection error"}}]
            }
            return response

        mock_post.side_effect = side_effect

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request("test prompt")
        self.assertEqual(result, "Recovered after connection error")
        self.assertEqual(call_count['value'], 2)

    @patch('requests.post')
    @patch('time.sleep')
    def test_retry_on_empty_response(self, mock_sleep, mock_post):
        """Empty content triggers retry, second attempt succeeds."""
        call_count = {'value': 0}

        def side_effect(*args, **kwargs):
            call_count['value'] += 1
            response = MagicMock()
            response.status_code = 200
            if call_count['value'] == 1:
                response.json.return_value = {
                    "choices": [{"message": {"content": ""}}]
                }
            else:
                response.json.return_value = {
                    "choices": [{"message": {"content": "Second attempt worked"}}]
                }
            return response

        mock_post.side_effect = side_effect

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request("test prompt")
        self.assertEqual(result, "Second attempt worked")
        self.assertEqual(call_count['value'], 2)

    @patch('requests.post')
    @patch('time.sleep')
    def test_retry_on_rate_limit_429(self, mock_sleep, mock_post):
        """Rate limit 429 triggers retry."""
        call_count = {'value': 0}

        def side_effect(*args, **kwargs):
            call_count['value'] += 1
            response = MagicMock()
            response.status_code = 429 if call_count['value'] == 1 else 200
            response.json.return_value = {
                "choices": [{"message": {"content": "After rate limit"}}]
            }
            response.text = "Too Many Requests"
            return response

        mock_post.side_effect = side_effect

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request("test prompt")
        self.assertEqual(result, "After rate limit")
        self.assertEqual(call_count['value'], 2)

    @override_settings(AI_PROVIDER_MODEL='test/primary-model', AI_PROVIDER_FALLBACK_MODEL='test/fallback-model')
    @patch('requests.post')
    @patch('time.sleep')
    def test_correct_models_used(self, mock_sleep, mock_post):
        """Verify primary and fallback model names are correct."""
        import requests as real_requests

        calls = []

        def capture_model(*args, **kwargs):
            calls.append(kwargs['json']['model'])
            if len(calls) <= 3:
                raise real_requests.exceptions.Timeout("timeout")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "ok"}}]
            }
            return response

        mock_post.side_effect = capture_model

        from quiz.services.pipeline_service import ModelProvider

        provider = ModelProvider()
        provider._max_retries = 2

        result = provider._make_request_with_fallback("test prompt")
        self.assertEqual(result, "ok")
        self.assertEqual(calls[:3], ['test/primary-model', 'test/primary-model', 'test/primary-model'])
        self.assertEqual(calls[3], 'test/fallback-model')
