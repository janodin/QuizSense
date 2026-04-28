from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('generate/<int:upload_session_id>/', views.generate_quiz, name='generate_quiz'),
    path('summary/<int:upload_session_id>/', views.study_summary, name='study_summary'),
    path('summary/<int:upload_session_id>/status/', views.upload_session_status, name='upload_session_status'),
    path('quiz/<int:quiz_id>/', views.take_quiz, name='take_quiz'),
    path('quiz/<int:quiz_id>/submit/', views.submit_quiz, name='submit_quiz'),
    path('results/<int:attempt_id>/', views.quiz_results, name='quiz_results'),
    path('review/<int:attempt_id>/', views.review_quiz, name='review_quiz'),
    path('insights/<int:attempt_id>/', views.quiz_insights, name='quiz_insights'),
]

