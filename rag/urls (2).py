from django.urls import path
from . import views

urlpatterns = [
    path('suggest/', views.generate_suggestions, name='rag-suggest'),
    path('status/',  views.rag_status,            name='rag-status'),
    path('explain/', views.explain_prediction,    name='rag-explain'),
    path('ask/',     views.ask_coordinator_question, name='rag-ask'),
    path('ask-analytics/', views.ask_analytics, name='rag-ask-analytics'),
]
