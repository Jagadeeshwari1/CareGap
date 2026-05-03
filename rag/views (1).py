"""
RAG API Views
─────────────
POST /api/rag/suggest/   → generate habit suggestions for a patient
GET  /api/rag/status/    → check if Ollama is reachable and index is built
"""

import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings

from patients.models import Patient, Observation, Condition
from patients.risk_engine import assess_risk
from patients.duckdb_client import get_patient_metadata, get_patient_detail
from .pipeline import rag_pipeline


@api_view(['POST'])
def generate_suggestions(request):
    """
    Generate personalized lifestyle habit suggestions for a patient.

    POST body:
    {
        "patient_id": "uuid-string"
    }
    """
    patient_id = request.data.get('patient_id', '').strip()
    if not patient_id:
        return Response({'error': 'patient_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        patient = Patient.objects.get(patient_id=patient_id)
        # Run risk engine to populate profile (SQLite patients only)
        observations = Observation.objects.filter(patient=patient)
        conditions   = Condition.objects.filter(patient=patient)
        risk_result  = assess_risk(patient, observations, conditions)

        patient_profile = {
            'name':            patient.full_name(),
            'age':             patient.age,
            'gender':          patient.gender,
            'city':            patient.city,
            'has_diabetes':    risk_result.has_diabetes,
            'has_hypertension': risk_result.has_hypertension,
            'hba1c_value':     risk_result.hba1c_value,
            'latest_sbp':      risk_result.latest_sbp,
            'risk_tier':       risk_result.tier,
            'risk_score':      risk_result.score,
        }
        p_name = patient.full_name()
        p_tier = risk_result.tier
        p_score = risk_result.score
    except Patient.DoesNotExist:
        # DuckDB Fallback
        meta = get_patient_metadata(patient_id)
        if not meta:
            return Response({'error': 'Patient not found in any database.'}, status=status.HTTP_404_NOT_FOUND)
        
        # For DuckDB-only patients, we use a simplified profile
        patient_profile = {
            'name':   meta['name'],
            'age':    meta['age'],
            'gender': meta['gender'],
            'city':   meta['city'],
            'has_diabetes':     meta['cohort'] == 'chronic', # Heuristic
            'has_hypertension': meta['cohort'] == 'chronic',
            'hba1c_value':      meta.get('latest_hba1c'),
            'latest_sbp':       meta.get('latest_sbp'),
            'risk_tier':        'UNKNOWN',
            'risk_score':       0,
        }
        p_name = meta['name']
        p_tier = 'UNKNOWN'
        p_score = 0

    result = rag_pipeline.generate_suggestions(patient_profile)

    return Response({
        'patient_id':   patient_id,
        'patient_name': p_name,
        'risk_tier':    p_tier,
        'risk_score':   p_score,
        **result,
    })


@api_view(['GET'])
def rag_status(request):
    """
    Health check:
    - Is Ollama reachable?
    - Is the FAISS index built?
    """
    import requests
    ollama_ok = False
    ollama_models = []
    try:
        r = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            ollama_ok = True
            ollama_models = [m['name'] for m in r.json().get('models', [])]
    except Exception:
        pass

    index_path = settings.FAISS_INDEX_PATH
    index_built = (
        (index_path / 'knowledge.index').exists() and
        (index_path / 'chunks.json').exists()
    )

    return Response({
        'ollama_reachable': ollama_ok,
        'ollama_url':       settings.OLLAMA_BASE_URL,
        'configured_model': settings.OLLAMA_MODEL,
        'available_models': ollama_models,
        'faiss_index_built': index_built,
        'status': 'ready' if (ollama_ok and index_built) else 'not_ready'
    })


@api_view(['POST'])
def explain_prediction(request):
    """Explain a specific prediction result in plain English."""
    patient_id = request.data.get('patient_id')
    prediction_data = request.data.get('prediction_data')
    
    if not (patient_id and prediction_data):
        return Response({'error': 'patient_id and prediction_data required'}, status=400)
    
    from patients.models import Patient
    from patients.models import Patient
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        profile = {
            'name': patient.full_name(),
            'age': patient.age,
            'gender': patient.gender
        }
    except Patient.DoesNotExist:
        # DuckDB Metadata Fallback
        meta = get_patient_metadata(patient_id)
        if not meta:
            return Response({'error': 'Patient not found.'}, status=404)
        profile = {
            'name': meta['name'],
            'age': meta['age'],
            'gender': meta['gender']
        }
    
    try:
        explanation = rag_pipeline.explain_prediction(profile, prediction_data)
        return Response({'explanation': explanation})
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
def ask_coordinator_question(request):
    """Open-ended AI assistant for coordinators to ask about a patient."""
    patient_id = request.data.get('patient_id')
    question = request.data.get('question')
    
    if not (patient_id and question):
        return Response({'error': 'patient_id and question required'}, status=400)
    
    from patients.models import Patient
    from patients.models import Patient
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        from patients.models import Observation, Condition
        from patients.risk_engine import assess_risk
        observations = Observation.objects.filter(patient=patient)
        conditions   = Condition.objects.filter(patient=patient)
        risk_result  = assess_risk(patient, observations, conditions)

        profile = {
            'name':            patient.full_name(),
            'age':             patient.age,
            'gender':          patient.gender,
            'has_diabetes':    risk_result.has_diabetes,
            'has_hypertension': risk_result.has_hypertension,
            'hba1c_value':     risk_result.hba1c_value,
            'latest_sbp':      risk_result.latest_sbp,
            'risk_tier':       risk_result.tier,
            'risk_score':      risk_result.score,
        }
    except Patient.DoesNotExist:
        # DuckDB Metadata Fallback
        meta = get_patient_metadata(patient_id)
        if not meta:
            return Response({'error': 'Patient not found.'}, status=404)
        profile = {
            'name': meta['name'], 
            'age': meta['age'], 
            'gender': meta['gender'],
            'hba1c_value': meta.get('latest_hba1c'),
            'latest_sbp': meta.get('latest_sbp'),
        }

    history = request.data.get('history', [])
    
    try:
        answer = rag_pipeline.generate_coordinator_answer(profile, question, history)
        return Response({'answer': answer})
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
def ask_analytics(request):
    """Answers questions about clinical analytics and explorer metrics."""
    question = request.data.get('question')
    if not question:
        return Response({'error': 'question required'}, status=400)
    
    history = request.data.get('history', [])
    try:
        answer = rag_pipeline.generate_analytics_answer(question, history)
        return Response({'answer': answer})
    except Exception as e:
        return Response({'error': str(e)}, status=500)
