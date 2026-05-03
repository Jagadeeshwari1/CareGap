"""
CareGap Risk Engine
───────────────────
Determines a patient's risk tier based on:
  • HbA1c last-test gap   (LOINC 4548-4)
  • Systolic BP readings  (LOINC 8480-6)
  • Active diabetes / hypertension conditions

Risk Tiers
──────────
  HIGH        → Immediate intervention needed
  MODERATE    → Follow-up visit recommended
  PREVENTIVE  → Lifestyle/habit guidance via RAG
  NORMAL      → No current gaps detected
"""

from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskResult:
    tier: str                        # HIGH | MODERATE | PREVENTIVE | NORMAL
    score: int                       # 0-100 composite risk score
    reasons: list[str]               # Human-readable reasons
    hba1c_days_gap: Optional[int]    # Days since last HbA1c test
    hba1c_value: Optional[float]     # Most recent HbA1c %
    latest_sbp: Optional[float]      # Most recent systolic BP mmHg
    has_diabetes: bool
    has_hypertension: bool
    recommended_action: str
    followup_urgency_days: Optional[int]  # Recommended days until follow-up


# ── Thresholds ──────────────────────────────────────────────────────
HBAC1C_CRITICAL_GAP   = 365    # days — overdue for annual test
HBAC1C_WARNING_GAP    = 270    # days — approaching overdue
HBAC1C_HIGH_VALUE     = 8.0    # % — poor control
HBAC1C_MODERATE_VALUE = 7.0    # % — borderline control

SBP_CRITICAL  = 160    # mmHg — stage 2 / hypertensive crisis
SBP_HIGH      = 140    # mmHg — stage 2
SBP_MODERATE  = 130    # mmHg — stage 1


def _parse_date(val):
    """Robustly parse a date from datetime, string, pandas Timestamp, or NaT."""
    if val is None:
        return None
    try:
        import pandas as pd
        if pd.isnull(val):
            return None
    except Exception:
        pass
    if hasattr(val, 'date'):
        # datetime or pandas Timestamp
        return val.date() if callable(val.date) else val.date
    if isinstance(val, str):
        from datetime import datetime
        # Handle ISO strings like '2023-04-01T10:00:00-06:00' or '2023-04-01'
        try:
            return datetime.fromisoformat(val[:10]).date()
        except ValueError:
            return None
    if hasattr(val, 'year'):  # already a date object
        return val
    return None



def _is_active(stop_val) -> bool:
    """Return True if the condition stop date is null/None/NaT (i.e. still active)."""
    if stop_val is None:
        return True
    try:
        import pandas as pd
        return pd.isnull(stop_val)
    except Exception:
        return False


def assess_risk(patient, observations, conditions) -> RiskResult:
    """
    Core risk assessment function.

    Args:
        patient:      patients.models.Patient instance (or SimpleNamespace)
        observations: list/QuerySet of Observation for this patient
        conditions:   list/QuerySet of Condition for this patient

    Returns:
        RiskResult dataclass
    """
    reasons    = []
    score      = 0
    today      = date.today()

    # Convert to lists to avoid breaking prefetch_related with new DB queries
    cond_list = list(conditions)
    obs_list  = list(observations)

    # ── 1. Condition flags ─────────────────────────────────────────
    from patients.models import Condition

    has_diabetes = any(
        str(c.code) in Condition.DIABETES_CODES and _is_active(c.stop)
        for c in cond_list
    )
    has_hypertension = any(
        str(c.code) in Condition.HYPERTENSION_CODES and _is_active(c.stop)
        for c in cond_list
    )

    if has_diabetes:
        score += 15
        reasons.append("Active diabetes diagnosis on record")
    if has_hypertension:
        score += 15
        reasons.append("Active hypertension diagnosis on record")

    # ── 2. HbA1c gap analysis ──────────────────────────────────────
    from patients.models import Observation
    
    hba1c_obs = [o for o in obs_list if o.code == Observation.LOINC_HBA1C and o.date is not None]
    hba1c_obs.sort(key=lambda x: x.date, reverse=True)
    
    hba1c_days_gap  = None
    hba1c_value     = None

    if hba1c_obs:
        latest_hba1c = hba1c_obs[0]
        try:
            hba1c_value = float(latest_hba1c.value)
        except (ValueError, TypeError):
            hba1c_value = None

        last_date = _parse_date(latest_hba1c.date)
        if last_date is None:
            hba1c_days_gap = None
        else:
            hba1c_days_gap = (today - last_date).days

        if hba1c_days_gap > HBAC1C_CRITICAL_GAP:
            score += 35
            reasons.append(
                f"HbA1c test overdue — last tested {hba1c_days_gap} days ago "
                f"(threshold: {HBAC1C_CRITICAL_GAP} days)"
            )
        elif hba1c_days_gap > HBAC1C_WARNING_GAP:
            score += 20
            reasons.append(
                f"HbA1c test approaching overdue — {hba1c_days_gap} days since last test"
            )

        if hba1c_value:
            if hba1c_value >= HBAC1C_HIGH_VALUE:
                score += 25
                reasons.append(f"HbA1c value {hba1c_value}% — poor glycemic control (≥ {HBAC1C_HIGH_VALUE}%)")
            elif hba1c_value >= HBAC1C_MODERATE_VALUE:
                score += 12
                reasons.append(f"HbA1c value {hba1c_value}% — borderline control (≥ {HBAC1C_MODERATE_VALUE}%)")
    else:
        # No HbA1c on record at all — treat as maximum gap
        hba1c_days_gap = None
        if has_diabetes:
            score += 40
            reasons.append("Diabetic patient with no HbA1c test on record — immediate testing needed")

    # ── 3. Blood pressure analysis ────────────────────────────────
    sbp_obs = [o for o in obs_list if o.code == Observation.LOINC_SBP and o.date is not None]
    sbp_obs.sort(key=lambda x: x.date, reverse=True)
    
    latest_sbp = None

    if sbp_obs:
        try:
            latest_sbp = float(sbp_obs[0].value)
        except (ValueError, TypeError):
            latest_sbp = None

        if latest_sbp:
            if latest_sbp >= SBP_CRITICAL:
                score += 35
                reasons.append(
                    f"Critical systolic BP: {latest_sbp} mmHg "
                    f"(≥ {SBP_CRITICAL} mmHg — requires urgent follow-up within 30 days)"
                )
            elif latest_sbp >= SBP_HIGH:
                score += 20
                reasons.append(f"Elevated systolic BP: {latest_sbp} mmHg — stage 2 hypertension range")
            elif latest_sbp >= SBP_MODERATE:
                score += 10
                reasons.append(f"Borderline systolic BP: {latest_sbp} mmHg — stage 1 range")

    # ── 4. Age-based risk factor ──────────────────────────────────
    if patient.age:
        if patient.age >= 65:
            score += 10
            reasons.append(f"Age {patient.age} — elevated cardiovascular risk")
        elif patient.age >= 45:
            score += 5

    # ── 5. Determine tier ─────────────────────────────────────────
    score = min(score, 100)

    # Add extra flag for emergency triggers
    is_emergency = (
        (latest_sbp and latest_sbp >= SBP_CRITICAL) or
        (hba1c_value and hba1c_value >= 9.0) or
        score >= 80
    )

    if is_emergency:
        tier   = 'EMERGENCY'
        action = (
            "CRITICAL MEDICAL ATTENTION REQUIRED. Dispatch to Emergency Room immediately. "
            "High risk of acute cardiovascular or metabolic event."
        )
        followup_days = 0 # Immediate
    elif score >= 60:
        tier   = 'HIGH'
        action = (
            "Urgent Care outreach required. Match patient insurance and locate "
            "nearby urgent care facilities. Contact within 24–48 hours."
        )
        followup_days = 7
    elif score >= 30:
        tier   = 'MODERATE'
        action = (
            "Schedule a follow-up visit within the recommended window. "
            "Review recent labs and medication adherence."
        )
        followup_days = 30
    elif score >= 10:
        tier   = 'PREVENTIVE'
        action = (
            "Patient is currently stable but at future risk. "
            "Provide personalized lifestyle and habit recommendations via care guidance."
        )
        followup_days = 90
    else:
        tier   = 'NORMAL'
        action = "No current care gaps detected. Continue routine monitoring schedule."
        followup_days = 180

    return RiskResult(
        tier=tier,
        score=score,
        reasons=reasons,
        hba1c_days_gap=hba1c_days_gap,
        hba1c_value=hba1c_value,
        latest_sbp=latest_sbp,
        has_diabetes=has_diabetes,
        has_hypertension=has_hypertension,
        recommended_action=action,
        followup_urgency_days=followup_days,
    )
