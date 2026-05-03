"""
Patient API Views
─────────────────
GET  /api/patients/search/?q=<name>          → fuzzy patient search
GET  /api/patients/<patient_id>/             → full patient profile
GET  /api/patients/<patient_id>/risk/        → risk assessment result
GET  /api/patients/<patient_id>/urgent-care/ → nearby urgent cares (HIGH risk)
"""

import logging
import random
from datetime import timedelta

from django.db.models import Count, Exists, FloatField, Max, OuterRef, Q, Sum, IntegerField
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import Patient, Observation, Encounter, Condition, Medication
from .serializers import PatientListSerializer, PatientDetailSerializer
from .risk_engine import assess_risk
from .urgent_care_matcher import find_urgent_cares
from .ml_models import FEATURE_NAMES
import numpy as np

logger = logging.getLogger(__name__)


def _to_json_safe(data):
    """Recursively convert date/timestamp and numpy objects to JSON-safe primitives."""
    import datetime
    import numpy as np
    import pandas as pd
    
    if isinstance(data, dict):
        return {k: _to_json_safe(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple, set)):
        return [_to_json_safe(v) for v in data]
    elif isinstance(data, (datetime.date, datetime.datetime, pd.Timestamp)):
        return data.isoformat()
    elif isinstance(data, (np.integer, np.floating)):
        return data.item()
    elif isinstance(data, np.ndarray):
        return [_to_json_safe(v) for v in data.tolist()]
    elif pd.isna(data):
        return None
    return data


# ── 1. Patient Search ─────────────────────────────────────────────
from patients.duckdb_client import search_patients, get_patient_detail

@api_view(['GET'])
def patient_search(request):
    """
    Search patients by name or city with pagination.
    Rewritten to use DuckDB!
    """
    query = request.GET.get('q', '').strip()
    cohort = request.GET.get('cohort', '').strip()
    # Triggering reload
    try:
        limit = min(int(request.GET.get('limit', 50)), 200)
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(int(request.GET.get('offset', 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    return Response(_to_json_safe(search_patients(query=query, cohort=cohort, limit=limit, offset=offset)))


# ── 2. Patient Profile ────────────────────────────────────────────
@api_view(['GET'])
def patient_detail(request, patient_id):
    """Full patient profile with all related data, now using DuckDB."""
    patient_data = get_patient_detail(patient_id)
    if not patient_data:
        return Response({'error': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)
        
    return Response(_to_json_safe(patient_data))


# ── 3. Risk Assessment ────────────────────────────────────────────
@api_view(['GET'])
def patient_risk(request, patient_id):
    """
    Run the risk engine for a patient and return structured result.
    Used to drive the dashboard risk card.
    """
    import time
    from django.core.cache import cache
    from .risk_engine import assess_risk

    cache_key = f'patient_risk_{patient_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)

    try:
        from patients.duckdb_client import get_patient_detail
        patient_data = get_patient_detail(patient_id)
        if not patient_data:
            return Response({'error': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        # Simple mocks for assess_risk compatibility
        from types import SimpleNamespace
        patient_mock = SimpleNamespace(
            patient_id=patient_data['patient_id'],
            age=patient_data['age'],
            full_name=lambda: f"{patient_data['first']} {patient_data['last']}"
        )
        observations = [SimpleNamespace(code=o['code'], value=o['value'], date=o['date']) for o in patient_data['observations']]
        conditions = [SimpleNamespace(code=c['code'], description=c['description'], stop=c['stop']) for c in patient_data['conditions']]
        
        result = assess_risk(patient_mock, observations, conditions)
    except Exception as e:
        print(f'[risk] Error: {e}')
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    payload = {
        'patient_id':            patient_data['patient_id'],
        'patient_name':          f"{patient_data['first']} {patient_data['last']}",
        'tier':                  result.tier,
        'score':                 result.score,
        'reasons':               result.reasons,
        'hba1c_days_gap':        result.hba1c_days_gap,
        'hba1c_value':           result.hba1c_value,
        'latest_sbp':            result.latest_sbp,
        'has_diabetes':          result.has_diabetes,
        'has_hypertension':      result.has_hypertension,
        'recommended_action':    result.recommended_action,
        'followup_urgency_days': result.followup_urgency_days,
    }
    cache.set(cache_key, payload, 600)
    return Response(_to_json_safe(payload))


@api_view(['GET'])
def patient_urgent_cares(request, patient_id):
    """
    Returns nearby urgent care facilities matched to patient's
    city and insurance type. Intended for HIGH-risk patients only
    but can be called for any patient.
    """
    from patients.duckdb_client import get_patient_detail
    patient_data = get_patient_detail(patient_id)
    if not patient_data:
        return Response({'error': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)

    # Mock object for find_urgent_cares
    from types import SimpleNamespace
    patient_mock = SimpleNamespace(
        patient_id=patient_data['patient_id'],
        full_name=lambda: f"{patient_data['first']} {patient_data['last']}",
        city=patient_data['city'],
        insurance=patient_data['insurance'],
        lat=patient_data.get('lat', 37.7749), # Default to SF if missing
        lon=patient_data.get('lon', -122.4194)
    )

    facilities = find_urgent_cares(patient_mock, max_results=5)

    return Response(_to_json_safe({
        'patient_id':     patient_data['patient_id'],
        'patient_name':   f"{patient_data['first']} {patient_data['last']}",
        'patient_city':   patient_data['city'],
        'patient_insurance': patient_data['insurance'],
        'facilities':     facilities,
    }))


# ── 5a. Fast basic stats (stat cards only — instant) ──────────────
@api_view(['GET'])
def dashboard_stats_basic(request):
    """
    Returns only cohort counts + condition rates.
    Rewritten to use DuckDB for instant response.
    """
    from django.core.cache import cache
    from patients.duckdb_client import get_dashboard_stats_basic
    
    cached = cache.get('dashboard_stats_basic')
    if cached is not None:
        return Response(cached)

    payload = _to_json_safe(get_dashboard_stats_basic())
    cache.set('dashboard_stats_basic', payload, 600)
    return Response(payload)


# ── 5b. Full dashboard stats (charts + care gaps) ─────────────────
@api_view(['GET'])
def dashboard_stats(request):
    """
    Full population analytics for dashboard charts.
    Rewritten to use DuckDB! Benchmarked at <1s vs 15-30s on SQLite.
    """
    from django.core.cache import cache
    from patients.duckdb_client import get_dashboard_stats
    import time

    cached = cache.get('dashboard_stats')
    if cached is not None:
        return Response(cached)

    # Cache stampede guard
    if cache.get('dashboard_stats_computing'):
        for _ in range(10):
            time.sleep(1)
            cached = cache.get('dashboard_stats')
            if cached is not None: return Response(cached)

    cache.set('dashboard_stats_computing', True, 60)
    try:
        payload = _to_json_safe(get_dashboard_stats())
        cache.set('dashboard_stats', payload, 600)
        return Response(payload)
    finally:
        cache.delete('dashboard_stats_computing')

# ── 6. Analytics Explorer ─────────────────────────────────────────
@api_view(['GET'])
def analytics(request):
    """
    Flexible population analytics with user-defined filters.
    Rewritten to use DuckDB!
    """
    from django.core.cache import cache
    from patients.duckdb_client import get_analytics_explorer
    import hashlib, json

    filters = {
        'cohort':    request.GET.get('cohort', '').strip(),
        'gender':    request.GET.get('gender', '').strip(),
        'age_min':   request.GET.get('age_min', '').strip(),
        'age_max':   request.GET.get('age_max', '').strip(),
        'condition': request.GET.get('condition', '').strip(),
    }

    cache_key = 'analytics_' + hashlib.md5(json.dumps(sorted(filters.items())).encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)

    payload = _to_json_safe(get_analytics_explorer(filters))
    cache.set(cache_key, payload, 600)
    return Response(payload)


# ── 7. Predictive Analytics (per-patient ML) ──────────────────────
@api_view(['GET'])
def patient_predict(request, patient_id):
    """
    ML-powered 6-month risk forecast for a single patient.

    Uses the trained Lasso, Random Forest, and XGBoost models
    for progression probability, and numpy.polyfit on recent lab/vitals
    history for HbA1c and SBP trajectory projections.

    Falls back to rule-based risk score when no trained model exists
    (model_available: false in response).
    """
    from patients.ml_models import (
        predict_multi_hba1c_trajectory,
        predict_multi_sbp_trajectory,
        decompose_risk,
    )
    from patients.duckdb_client import get_patient_features, get_patient_detail, get_patient_metadata

    # ── 1. Identify Patient & Cohort ──
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        p_info = {
            'patient_name': patient.full_name(),
            'age': patient.age,
            'gender': patient.gender,
            'cohort': patient.cohort
        }
    except Patient.DoesNotExist:
        # DuckDB Metadata Fallback
        p_info = get_patient_metadata(patient_id)
        if not p_info:
            return Response({'error': 'Patient not found in dataset'}, status=404)
        p_info = {
            'patient_name': p_info['name'],
            'age': p_info['age'],
            'gender': p_info['gender'],
            'cohort': p_info['cohort']
        }

    cohort = p_info['cohort']

    # ── 2. Cache-First Strategy (Adults only) ──
    # Pediatric risk is computed on-demand to ensure accurate percentile/gaps
    if cohort != 'pediatric':
        import os
        import pickle
        cache_path = os.path.join('patients', 'data', 'triage_cache.pkl')
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'rb') as f:
                    cached_data = pickle.load(f)
                    pred_map = cached_data.get('prediction_map', {})
                    if patient_id in pred_map:
                        payload = pred_map[patient_id]
                        
                        det = get_patient_detail(patient_id)
                        observations = [Observation(code=o['code'], value=o['value'], date=o['date']) for o in det['observations']]
                        
                        payload['sugar_forecast'] = predict_multi_hba1c_trajectory(observations)
                        payload['bp_forecast'] = predict_multi_sbp_trajectory(observations)
                        
                        p_feat = get_patient_features(patient_id)
                        payload['current_vitals'] = {
                            'sbp': p_feat.get('latest_sbp', 'N/A') if p_feat else 'N/A',
                            'hba1c': p_feat.get('latest_hba1c', 'N/A') if p_feat else 'N/A'
                        }
                        
                        # Ensure recommendation is fresh
                        prob = payload['progression_probability']
                        rec = 'Continue current care plan'
                        if prob >= 0.70: rec = 'Immediate provider outreach recommended.'
                        elif prob >= 0.40: rec = 'Schedule follow-up within 30 days.'
                        payload['recommendation'] = rec
                        
                        return Response(_to_json_safe(payload))
            except Exception as e:
                logger.error("Predict Cache read failure: %s", e)

    # ── 3. Fallback/On-Demand Computation ──
    features_dict = get_patient_features(patient_id)
    if not features_dict:
        return Response({'error': 'Features could not be extracted'}, status=500)
    
    # ── 4. Compute Risk (Cohort-Specific) ──
    if cohort == 'pediatric':
        from patients.ml_models import calculate_pediatric_risk
        det = get_patient_detail(patient_id)
        ped_risk = calculate_pediatric_risk(
            features_dict['age'], 
            p_info['gender'], 
            features_dict.get('weight_kg'), 
            features_dict.get('height_cm'),
            conditions=det.get('conditions', []),
            observations=det.get('observations', [])
        )
        payload = {
            'patient_id': patient_id,
            **p_info,
            'bmi': ped_risk['bmi'] if ped_risk else 0,
            'percentile': ped_risk['percentile'] if ped_risk else 50,
            'risk_tier': ped_risk['category'] if ped_risk else 'Healthy weight',
            'care_gaps': ped_risk.get('care_gaps', []),
            'has_asthma': ped_risk.get('has_asthma', False),
            'recommendation': ped_risk['recommended_action'] if ped_risk else "Continue routine monitoring"
        }
    elif cohort == 'at_risk':
        from patients.ml_models import predict_at_risk_onset
        # At-risk patients get the onset ensemble probability
        features_arr = np.array([features_dict[k] for k in FEATURE_NAMES], dtype=float)
        onset_prob = predict_at_risk_onset(features_arr)
        
        rec = 'Focus on preventive lifestyle habits.'
        if onset_prob >= 0.60: rec = 'High risk of onset. Schedule screening within 6 months.'
        
        payload = {
            'patient_id': patient_id,
            **p_info,
            'onset_probability': onset_prob,
            'recommendation': rec,
            'vitals_summary': {
                'sbp': features_dict['latest_sbp'],
                'hba1c': features_dict['latest_hba1c']
            }
        }
    else:
        # Chronic (Default)
        from datetime import datetime
        decomposed = decompose_risk(features_dict)
        det = get_patient_detail(patient_id)
        
        # We need to mock Observation objects that the trajectory logic expects
        # We parse the ISO string dates from DuckDB into real datetime objects
        mock_p = Patient(patient_id=patient_id) 
        observations = []
        for o in det.get('observations', []):
            try:
                obs_date = o['date']
                if isinstance(obs_date, str):
                    dt = datetime.fromisoformat(obs_date.replace('Z', '+00:00'))
                else:
                    dt = obs_date
                
                observations.append(Observation(
                    patient=mock_p,
                    code=o['code'],
                    value=o['value'],
                    date=dt
                ))
            except (ValueError, TypeError):
                continue

        sugar_forecast = predict_multi_hba1c_trajectory(observations)
        bp_forecast    = predict_multi_sbp_trajectory(observations)

        prob = decomposed['overall']['probability']
        rec = 'Continue current care plan'
        if prob >= 0.70: rec = 'Immediate provider outreach recommended.'
        elif prob >= 0.40: rec = 'Schedule follow-up within 30 days.'

        payload = {
            'patient_id': patient_id,
            **p_info,
            'features': features_dict,
            'progression_probability': prob,
            'model_scores': decomposed['overall']['model_scores'],
            'model_available': decomposed['overall']['model_available'],
            'shap_values': decomposed['overall'].get('shap_values', {}),
            'sugar_risk': decomposed['sugar_driven'],
            'bp_risk': decomposed['bp_driven'],
            'sugar_forecast': sugar_forecast,
            'bp_forecast': bp_forecast,
            'recommendation': rec,
            'current_vitals': {
                'sbp': features_dict.get('latest_sbp', 'N/A'),
                'hba1c': features_dict.get('latest_hba1c', 'N/A')
            }
        }
    
    return Response(_to_json_safe(payload))



_TRIAGE_CACHE_KEY = 'triage_list_ml'
_TRIAGE_EMERGENCY_THRESHOLD = 0.75
_TRIAGE_URGENT_LOWER = 0.10
_TRIAGE_HIGH_RISK_THRESHOLD = 0.60
_TRIAGE_FALLBACK_LIMIT = 35

def _with_score_defaults(row):
    return {
        **row,
        'probability': row.get('probability'),
        'model_available': row.get('model_available', False),
    }

def _filter_by_probability(rows, min_prob=None, max_prob=None):
    filtered = []
    for row in rows:
        prob = row.get('probability')
        if prob is None:
            continue
        if min_prob is not None and prob < min_prob:
            continue
        if max_prob is not None and prob >= max_prob:
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda r: r['probability'], reverse=True)


def _identify_risk_drivers(p_data):
    """
    Returns a human-readable list of primary drivers for the risk score.
    Based on thresholds aligned with the ensemble model features.
    """
    if not p_data:
        return ""
        
    drivers = []
    age = p_data.get('age', 0)
    hba1c = p_data.get('latest_hba1c', 0)
    sbp = p_data.get('latest_sbp', 0)
    gaps = p_data.get('care_gaps_count', 0)
    has_db = p_data.get('has_diabetes', 0)
    has_ht = p_data.get('has_hypertension', 0)

    if age >= 80 and sbp >= 140:
        drivers.append("High-Acuity Senior (Guardrail)")
    if age >= 70 and age < 80:
        drivers.append("Advanced Age")
    if sbp >= 140:
        drivers.append("Stage 2 HTN")
    if hba1c >= 8.5:
        drivers.append("Uncontrolled T2D")
    if gaps >= 2:
        drivers.append("Multiple Care Gaps")
    if has_db and has_ht:
        drivers.append("Multi-morbidity")
    
    return " · ".join(drivers) if drivers else "Chronic Monitoring"


def _score_triage_candidates(rows):
    """
    Apply ML models to candidates in batch for performance.
    """
    from .ml_models import predict_ensemble_score, FEATURE_NAMES, decompose_risk, predict_multi_hba1c_trajectory, predict_multi_sbp_trajectory
    from .duckdb_client import get_batch_patient_features, get_patient_detail
    import numpy as np

    patient_ids = [r['patient_id'] for r in rows]
    batch_features = get_batch_patient_features(patient_ids)
    
    # We need observations for trajectories - this might be slow for ALL, 
    # so we'll only do it efficiently if possible.
    # Actually, let's keep it simple for now and cache what we can.

    scored = []
    prediction_map = {}

    for row in rows:
        pid = row['patient_id']
        features = batch_features.get(pid)
        
        if features:
            try:
                # 1. Decompose Risk
                decomposed = decompose_risk(features)
                prob = decomposed['overall']['probability']
                
                # Ensure high-acuity seniors are handled
                if features.get('age', 0) >= 80 and features.get('latest_sbp', 0) >= 140:
                    prob = max(prob, 0.76)

                # 2. Store in Prediction Map for Instant Lookups
                prediction_map[pid] = {
                    'patient_id': pid,
                    'patient_name': row.get('name', pid),
                    'age': features.get('age'),
                    'gender': row.get('gender'),
                    'cohort': row.get('cohort', 'chronic'),
                    'progression_probability': prob,
                    'model_scores': decomposed['overall']['model_scores'],
                    'sugar_risk': decomposed['sugar_driven'],
                    'bp_risk': decomposed['bp_driven'],
                    # Forecasts will be computed on demand or cached if we have observations
                    # For now, we'll store the basic risk data which is the heaviest part
                }

                scored.append({
                    **row,
                    'probability': prob,
                    'model_available': decomposed['overall']['model_available'],
                    'risk_drivers': _identify_risk_drivers(features),
                })
            except Exception as exc:
                logger.error("Failed to score triage patient %s: %s", pid, exc)
    
    return scored, prediction_map


def _get_triage_payload():
    from django.core.cache import cache
    from patients.duckdb_client import get_triage_list

    cached = cache.get(_TRIAGE_CACHE_KEY)
    if cached is not None:
        return cached

    base = get_triage_list()
    # Score EVERY chronic patient found in DuckDB for full prediction map coverage
    candidates = base.get('emergency_patients', []) + base.get('urgent_patients', []) + base.get('at_risk_patients', [])
    scored, prediction_map = _score_triage_candidates(candidates)

    emergency = _filter_by_probability(scored, min_prob=_TRIAGE_EMERGENCY_THRESHOLD)
    if not emergency:
        emergency = [_with_score_defaults(row) for row in base.get('emergency_patients', [])][:_TRIAGE_FALLBACK_LIMIT]

    urgent = _filter_by_probability(
        scored,
        min_prob=_TRIAGE_URGENT_LOWER,
        max_prob=_TRIAGE_EMERGENCY_THRESHOLD
    )
    if not urgent:
        urgent = [_with_score_defaults(row) for row in base.get('urgent_patients', [])][:_TRIAGE_FALLBACK_LIMIT]

    # Calculate full risk breakdown for forecasting
    risk_breakdown = {
        'emergency': sum(1 for row in scored if row.get('probability') is not None and row['probability'] >= _TRIAGE_EMERGENCY_THRESHOLD),
        'high':      sum(1 for row in scored if row.get('probability') is not None and row['probability'] >= 0.60 and row['probability'] < _TRIAGE_EMERGENCY_THRESHOLD),
        'moderate':  sum(1 for row in scored if row.get('probability') is not None and row['probability'] >= 0.30 and row['probability'] < 0.60),
        'elevated':  sum(1 for row in scored if row.get('probability') is not None and row['probability'] >= 0.10 and row['probability'] < 0.30),
    }

    payload = _to_json_safe({
        'emergency_patients': emergency[:_TRIAGE_FALLBACK_LIMIT],
        'urgent_patients': urgent[:_TRIAGE_FALLBACK_LIMIT],
        'risk_breakdown': risk_breakdown,
        'high_risk_volume': risk_breakdown['emergency'] + risk_breakdown['high'] + risk_breakdown['moderate'],
        'prediction_map': prediction_map, # This is the "Pickle Everything" key
    })
    cache.set(_TRIAGE_CACHE_KEY, payload, 300)
    return payload
@api_view(['GET'])
def triage_list(request):
    """
    Returns the pre-computed high-risk triage lists (Emergency and Urgent).
    This loads instantly from a pickled cache to prevent dashboard slowness.
    """
    import os
    import pickle
    
    cache_path = os.path.join('patients', 'data', 'triage_cache.pkl')
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                data = pickle.load(f)
                return Response(data)
        except Exception as e:
            logger.error("Failed to load triage cache: %s", e)
            
    # Fallback to empty context if no cache exists
    return Response({
        'emergency_patients': [],
        'urgent_patients': [],
        'risk_breakdown': {},
        'high_risk_volume': 0,
        'generated_at': timezone.now().isoformat()
    })


@api_view(['GET'])
def resource_forecast(request):
    """
    Returns the pre-computed 30-day resource forecast derived from the
    ensemble ML scoring. This loads instantly from a pickled cache to 
    prevent UI timeouts.
    """
    import os
    import pickle
    
    cache_path = os.path.join('patients', 'data', 'forecast_cache.pkl')
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                data = pickle.load(f)
                return Response(data)
        except Exception as e:
            logger.error("Failed to load forecast cache: %s", e)
            
    # Fallback to empty response if no cache exists
    return Response({
        'error': 'Forecast cache not found. Please run precompute_forecast command.',
        'generated_at': timezone.now().isoformat(),
        'resources': {}
    }, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _age(birthdate):
    if not birthdate:
        return None
    from datetime import date
    today = date.today()
    bd = birthdate if hasattr(birthdate, 'year') else birthdate
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))





