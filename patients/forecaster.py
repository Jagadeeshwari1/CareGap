import math

def forecast_resources(risk_breakdown):
    """
    Calculates the 30-day hospital resource requirements based on 
    a breakdown of risk tiers (EMERGENCY, HIGH, MODERATE).
    
    Logic:
    1. Hospitalization Rates (High Acuity Calibration):
       - EMERGENCY: 80% (Extreme vitals)
       - HIGH: 40% (Unstable chronic)
       - MODERATE: 15% (Rising risk)
    2. ICU Rate: 15% of hospitalized patients require ICU.
    3. Nurse Ratios: 
       - 1 nurse per 4 general beds (1:4)
       - 1 nurse per 1 ICU room (1:1)
    """
    
    emergency = risk_breakdown.get('emergency', 0)
    high      = risk_breakdown.get('high', 0)
    moderate  = risk_breakdown.get('moderate', 0)
    elevated  = risk_breakdown.get('elevated', 0)
    
    # Predicted admission counts
    h_emergency = int(emergency * 0.90)  # Extreme acuity
    h_high      = int(high * 0.50)       # High risk unstable
    h_moderate  = int(moderate * 0.25)   # Rising risk
    h_elevated  = int(elevated * 0.10)   # Early warning (e.g. Stage 2 HTN)
    
    total_beds = h_emergency + h_high + h_moderate + h_elevated
    
    # ICU requirement (subset of admissions)
    icu_beds = int(total_beds * 0.15) 
    
    # Staffing (standard nurse-to-patient ratios)
    # General beds: 1:4 ratio, ICU: 1:1 ratio
    nurses = math.ceil((total_beds - icu_beds) / 4) + icu_beds
    
    return {
        'risk_breakdown': risk_breakdown,
        'period_days': 30,
        'resources': {
            'beds': {
                'count': max(1, total_beds),
                'label': 'General Hospital Beds',
                'description': 'Stabilization for patients with HbA1c > 8.0 or SBP > 140 (Including Uncontrolled Chronic Stage 2).'
            },
            'icu': {
                'count': max(0, icu_beds),
                'label': 'ICU Rooms',
                'description': 'Reserved for Critical Care cohort (Ensemble Risk Score > 75%).'
            },
            'nurses': {
                'count': max(1, nurses),
                'label': 'Nurse Staffing',
                'description': '24/7 staffing requirement for 4:1 general and 1:1 critical care monitoring.'
            }
        }
    }
