import os
import pandas as pd
import duckdb
import re
from datetime import datetime
from django.conf import settings
from functools import lru_cache

# Path to the DuckDB file
DUCKDB_PATH = os.path.join(settings.BASE_DIR, 'synthea_california.duckdb')

# Codes from models.py
HT_CODES = ('59621000', '38341003')
DIAB_CODES = ('44054006', '73211009')
LOINC_HBA1C = '4548-4'
LOINC_SBP = '8480-6'
LOINC_WEIGHT = '29463-7'
LOINC_HEIGHT = '8302-2'
LOINC_BMI = '89270-3'

def get_connection():
    """Return a read-only DuckDB connection."""
    return duckdb.connect(DUCKDB_PATH, read_only=True)

def _clean_name(name_str):
    """Strip trailing numbers that Synthea appends to names."""
    if not name_str: return ''
    return re.sub(r'\d+', '', str(name_str)).strip()

def _get_cohort_cte():
    """Returns the SQL CTE for classifying patients into cohorts, optimized for DuckDB."""
    return f"""
    WITH patient_base AS (
        SELECT 
            *,
            date_diff('year', cast(BIRTHDATE as date), current_date) AS calculated_age,
            (DEATHDATE IS NOT NULL) AS is_dead
        FROM patients
    ),
    chronic_flags AS (
        SELECT DISTINCT PATIENT 
        FROM conditions 
        WHERE code IN {HT_CODES + DIAB_CODES} AND stop IS NULL
    ),
    patient_with_cohort AS (
        SELECT 
            p.*,
            CASE 
                WHEN is_dead = true THEN 'deceased'
                WHEN calculated_age < 18 THEN 'pediatric'
                WHEN c.PATIENT IS NOT NULL THEN 'chronic'
                ELSE 'at_risk'
            END AS cohort
        FROM patient_base p
        LEFT JOIN chronic_flags c ON p.Id = c.PATIENT
    )
    """

def search_patients(query='', cohort='', limit=50, offset=0):
    """
    Search patients directly from the DuckDB 'patients' table.
    """
    cte = _get_cohort_cte()
    conn = get_connection()
    
    # Base query for alive patients
    sql = f"{cte} SELECT * FROM patient_with_cohort WHERE cohort != 'deceased'"
    params = []
    
    if cohort:
        sql += " AND cohort = ?"
        params.append(cohort.lower())
        
    if query:
        sql += " AND (FIRST ILIKE ? OR LAST ILIKE ? OR CITY ILIKE ? OR Id ILIKE ?)"
        term = f"%{query}%"
        params.extend([term, term, term, term])
        
    sql += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    # Execute using DuckDB pandas integration
    df = conn.execute(sql, params).df()
    
    results = []
    for _, row in df.iterrows():
        results.append({
            'patient_id': row['Id'],
            'first': _clean_name(row['FIRST']),
            'last': _clean_name(row['LAST']),
            'city': row['CITY'],
            'gender': row['GENDER'],
            'is_deceased': False,
            'cohort': row['cohort'],
            'age': row['calculated_age']
        })
        
    # Get total count
    count_sql = f"{cte} SELECT COUNT(*) FROM patient_with_cohort WHERE cohort != 'deceased'"
    count_params = []
    
    if cohort:
        count_sql += " AND cohort = ?"
        count_params.append(cohort.lower())
        
    if query:
        count_sql += " AND (FIRST ILIKE ? OR LAST ILIKE ? OR CITY ILIKE ? OR Id ILIKE ?)"
        count_params.extend([term, term, term, term])
        
    total = conn.execute(count_sql, count_params).fetchone()[0]
        
    conn.close()
    return {
        'total': total,
        'count': len(results),
        'offset': offset,
        'results': results
    }

@lru_cache(maxsize=1024)
def get_patient_metadata(patient_id):
    """
    Fetches core details for a patient from DuckDB without needing lists of observations.
    Useful for populating metadata for patients not in SQLite.
    """
    con = get_connection()
    # Use the CTE to get the real cohort
    cte = _get_cohort_cte()
    q = f"{cte} SELECT cohort, calculated_age, FIRST, LAST, GENDER, CITY FROM patient_with_cohort WHERE Id = ?"
    res = con.execute(q, [patient_id]).fetchone()
    con.close()
    
    if not res:
        return None
    
    cohort, age, first, last, gender, city = res
    return {
        'patient_id': patient_id,
        'name': f"{_clean_name(first)} {_clean_name(last)}",
        'age': int(age),
        'gender': 'Male' if gender == 'M' else ('Female' if gender == 'F' else gender),
        'city': city,
        'cohort': cohort
    }


def get_patient_detail(patient_id):
    cte = _get_cohort_cte()
    patient_id = str(patient_id)
    conn = get_connection()
    
    # 1. Fetch patient base demographics
    patient_df = conn.execute(f"{cte} SELECT * FROM patient_with_cohort WHERE Id = ?", [patient_id]).df()
    if patient_df.empty:
        conn.close()
        return None
        
    row = patient_df.iloc[0]
    
    # 2. Fetch related conditions
    conditions_df = conn.execute("SELECT * FROM conditions WHERE PATIENT = ? ORDER BY START DESC", [patient_id]).df()
    conditions = []
    import pandas as pd
    for _, c in conditions_df.iterrows():
        # Normalize NaT -> None so risk_engine's `c.stop is None` works correctly
        stop_val = None if pd.isnull(c['STOP']) else str(c['STOP'])[:10]
        start_val = None if pd.isnull(c['START']) else str(c['START'])[:10]
        conditions.append({
            'code': str(c['CODE']),
            'description': c['DESCRIPTION'],
            'start': start_val,
            'stop': stop_val
        })
    
    # 3. Fetch observations (limit 500 to capture historical vitals)
    obs_df = conn.execute("SELECT * FROM observations WHERE PATIENT = ? ORDER BY DATE DESC LIMIT 500", [patient_id]).df()
    observations = []
    for _, o in obs_df.iterrows():
        # Ensure DATE is ISO string for frontend compatibility
        obs_date = o['DATE']
        if hasattr(obs_date, 'isoformat'):
            obs_date = obs_date.isoformat()
        else:
            obs_date = str(obs_date)

        observations.append({
            'code': o['CODE'],
            'description': o['DESCRIPTION'],
            'date': obs_date,
            'value': o['VALUE'],
            'units': o['UNITS']
        })
    
    # 5. Fetch Encounters (limit 50)
    enc_df = conn.execute("SELECT * FROM encounters WHERE PATIENT = ? ORDER BY START DESC LIMIT 50", [patient_id]).df()
    encounters = []
    for _, e in enc_df.iterrows():
        start_date = e['START']
        if hasattr(start_date, 'isoformat'):
            start_date = start_date.isoformat()
        else:
            start_date = str(start_date)

        encounters.append({
            'encounter_class': e['ENCOUNTERCLASS'],
            'description': e['DESCRIPTION'],
            'start': start_date,
            'stop': e['STOP']
        })
    
    conn.close()
    return {
        'patient_id':  row['Id'],
        'first':       _clean_name(row['FIRST']),
        'last':        _clean_name(row['LAST']),
        'birthdate':   row['BIRTHDATE'],
        'city':        row['CITY'],
        'state':       row['STATE'],
        'gender':      row['GENDER'],
        'race':        row['RACE'],
        'ethnicity':   row['ETHNICITY'],
        'insurance':   'Private',
        'is_deceased': row['cohort'] == 'deceased',
        'cohort':      row['cohort'],
        'age':         int(row['calculated_age']),
        'conditions':  conditions,
        'observations': observations,
        'encounters':   encounters
    }

def get_dashboard_stats_basic():
    """Returns basic counts and rates for the dashboard."""
    cte = _get_cohort_cte()
    conn = get_connection()
    
    # Cohort counts
    counts_df = conn.execute(f"{cte} SELECT cohort, COUNT(*) as cnt FROM patient_with_cohort GROUP BY cohort").df()
    counts_dict = dict(zip(counts_df['cohort'], counts_df['cnt']))
    
    total_active = sum([v for k, v in counts_dict.items() if k != 'deceased'])
    total_deceased = counts_dict.get('deceased', 0)
    
    # Condition rates
    ht_count = conn.execute(f"SELECT COUNT(DISTINCT PATIENT) FROM conditions WHERE CODE IN {HT_CODES} AND STOP IS NULL").fetchone()[0]
    diab_count = conn.execute(f"SELECT COUNT(DISTINCT PATIENT) FROM conditions WHERE CODE IN {DIAB_CODES} AND STOP IS NULL").fetchone()[0]
    
    conn.close()
    return {
        'total_active': int(total_active),
        'total_deceased': int(total_deceased),
        'hypertension_rate': round(ht_count / total_active * 100, 1) if total_active else 0,
        'diabetes_rate': round(diab_count / total_active * 100, 1) if total_active else 0,
        'cohort_counts': {
            'chronic': int(counts_dict.get('chronic', 0)),
            'at_risk': int(counts_dict.get('at_risk', 0)),
            'pediatric': int(counts_dict.get('pediatric', 0)),
            'deceased': int(counts_dict.get('deceased', 0)),
        }
    }

def get_dashboard_stats():
    """Full population analytics for dashboard charts."""
    import time
    t0 = time.time()
    cte = _get_cohort_cte()
    conn = get_connection()
    
    # 1. Basic counts
    basic = get_dashboard_stats_basic()
    
    # 2. HbA1c distribution (chronic cohort only)
    hba1c_sql = f"""
    {cte}
    SELECT VALUE FROM (
        SELECT VALUE, 
               ROW_NUMBER() OVER (PARTITION BY PATIENT ORDER BY DATE DESC) as rn
        FROM observations
        WHERE CODE = '{LOINC_HBA1C}'
        AND PATIENT IN (SELECT Id FROM patient_with_cohort WHERE cohort = 'chronic')
    ) WHERE rn = 1
    """
    hba1c_df = conn.execute(hba1c_sql).df()
    hba1c_dist = {'normal': 0, 'prediabetes': 0, 'diabetes': 0}
    for val in hba1c_df['VALUE']:
        try:
            v = float(val)
            if v < 5.7: hba1c_dist['normal'] += 1
            elif v < 6.5: hba1c_dist['prediabetes'] += 1
            else: hba1c_dist['diabetes'] += 1
        except: pass

    # 3. BP distribution
    bp_sql = f"""
    {cte}
    SELECT VALUE FROM (
        SELECT VALUE, 
               ROW_NUMBER() OVER (PARTITION BY PATIENT ORDER BY DATE DESC) as rn
        FROM observations
        WHERE CODE = '{LOINC_SBP}'
        AND PATIENT IN (SELECT Id FROM patient_with_cohort WHERE cohort = 'chronic')
    ) WHERE rn = 1
    """
    bp_df = conn.execute(bp_sql).df()
    bp_dist = {'normal': 0, 'elevated': 0, 'stage1': 0, 'stage2': 0}
    for val in bp_df['VALUE']:
        try:
            v = float(val)
            if v < 120: bp_dist['normal'] += 1
            elif v < 130: bp_dist['elevated'] += 1
            elif v < 140: bp_dist['stage1'] += 1
            else: bp_dist['stage2'] += 1
        except: pass

    # 4. Insurance Breakdown (Synthea dataset specific)
    ins_buckets = {'Medicare': 0, 'Medicaid': 0, 'Private': 0, 'Uninsured': 0}
    # Synthetic datasets usually have multiple payers, we'll assign Private as default
    ins_buckets['Private'] = basic['total_active']

    # 5. City Distribution
    city_sql = f"{cte} SELECT CITY, COUNT(*) as cnt FROM patient_with_cohort WHERE cohort != 'deceased' GROUP BY CITY ORDER BY cnt DESC LIMIT 10"
    city_dist_df = conn.execute(city_sql).df()
    city_dist = city_dist_df.to_dict('records')

    # 6. Overlap (HTN and Diabetes)
    overlap_sql = f"""
    SELECT COUNT(*) FROM (
        SELECT DISTINCT PATIENT FROM conditions WHERE CODE IN {HT_CODES} AND STOP IS NULL
        INTERSECT
        SELECT DISTINCT PATIENT FROM conditions WHERE CODE IN {DIAB_CODES} AND STOP IS NULL
    )
    """
    both_count = conn.execute(overlap_sql).fetchone()[0]
    
    ht_count = conn.execute(f"SELECT COUNT(DISTINCT PATIENT) FROM conditions WHERE CODE IN {HT_CODES} AND STOP IS NULL").fetchone()[0]
    diab_count = conn.execute(f"SELECT COUNT(DISTINCT PATIENT) FROM conditions WHERE CODE IN {DIAB_CODES} AND STOP IS NULL").fetchone()[0]

    # 7. Care Gap Cascade
    chronic_ids_sql = f"{cte} SELECT Id FROM patient_with_cohort WHERE cohort = 'chronic'"
    total_flagged = basic['cohort_counts']['chronic']
    
    hba1c_recent_sql = f"""
    SELECT COUNT(DISTINCT PATIENT) FROM observations 
    WHERE CODE = '{LOINC_HBA1C}' 
    AND DATE >= current_date - interval '365 days'
    AND PATIENT IN ({chronic_ids_sql})
    """
    hba1c_recent_count = conn.execute(hba1c_recent_sql).fetchone()[0] or 0
    hba1c_overdue = max(0, total_flagged - hba1c_recent_count)
    
    no_med_sql = f"""
    SELECT COUNT(*) FROM ({chronic_ids_sql}) p
    WHERE NOT EXISTS (SELECT 1 FROM medications m WHERE m.PATIENT = p.Id AND m.STOP IS NULL)
    """
    no_medication = conn.execute(no_med_sql).fetchone()[0] or 0
    
    # BP follow-up logic
    bp_followup_sql = f"""
    WITH critical_bp AS (
        SELECT o.PATIENT, cast(o.DATE as date) as sbp_date
        FROM observations o
        INNER JOIN (
            SELECT PATIENT, MAX(DATE) as max_date
            FROM observations
            WHERE CODE = '{LOINC_SBP}'
            GROUP BY PATIENT
        ) latest ON o.PATIENT = latest.PATIENT AND o.DATE = latest.max_date
        WHERE o.CODE = '{LOINC_SBP}'
        AND try_cast(o.VALUE AS REAL) >= 160
        AND o.PATIENT IN ({chronic_ids_sql})
    )
    SELECT COUNT(*)
    FROM critical_bp c
    WHERE NOT EXISTS (
        SELECT 1 FROM encounters e
        WHERE e.PATIENT = c.PATIENT
        AND cast(e.START as date) >= c.sbp_date
        AND cast(e.START as date) <= c.sbp_date + interval '30 days'
    )
    """
    bp_followup_missing = conn.execute(bp_followup_sql).fetchone()[0] or 0

    conn.close()
    return {
        'total_active': int(basic['total_active']),
        'total_deceased': int(basic['total_deceased']),
        'hypertension_rate': basic['hypertension_rate'],
        'diabetes_rate': basic['diabetes_rate'],
        'cohort_counts': basic['cohort_counts'],
        'hba1c_dist': hba1c_dist,
        'bp_dist': bp_dist,
        'risk_overlap': {
            'both': int(both_count),
            'bp_only': int(max(0, ht_count - both_count)),
            'bs_only': int(max(0, diab_count - both_count)),
            'neither': int(max(0, basic['total_active'] - ht_count - diab_count + both_count)),
        },
        'care_gap_cascade': {
            'total_flagged': int(total_flagged),
            'hba1c_overdue': int(hba1c_overdue),
            'bp_followup_missing': int(bp_followup_missing),
            'no_medication': int(no_medication),
        },
        'city_distribution': [{'city': r['CITY'], 'count': int(r['cnt'])} for r in city_dist],
        'insurance_breakdown': ins_buckets,
        'compute_seconds': round(time.time() - t0, 3)
    }

def get_analytics_explorer(filters):
    """Filtered analytics explorer logic for DuckDB with full distribution data."""
    cte = _get_cohort_cte()
    conn = get_connection()
    
    where_clauses = ["cohort != 'deceased'"]
    params = []
    
    if filters.get('cohort'):
        where_clauses.append("cohort = ?")
        params.append(filters['cohort'])
    if filters.get('gender'):
        where_clauses.append("GENDER = ?")
        params.append(filters['gender'])
    if filters.get('age_min'):
        where_clauses.append("calculated_age >= ?")
        params.append(int(filters['age_min']))
    if filters.get('age_max'):
        where_clauses.append("calculated_age <= ?")
        params.append(int(filters['age_max']))
    if filters.get('condition'):
        cond_val = filters['condition']
        if cond_val == 'hypertension':
            where_clauses.append(f"Id IN (SELECT PATIENT FROM conditions WHERE CODE IN {HT_CODES} AND STOP IS NULL)")
        elif cond_val == 'diabetes':
            where_clauses.append(f"Id IN (SELECT PATIENT FROM conditions WHERE CODE IN {DIAB_CODES} AND STOP IS NULL)")
        else:
            # Fallback for any other description
            where_clauses.append("Id IN (SELECT PATIENT FROM conditions WHERE DESCRIPTION = ? AND STOP IS NULL)")
            params.append(cond_val)
        
    where_sql = " AND ".join(where_clauses)
    
    # 1. Main count
    count = conn.execute(f"{cte} SELECT COUNT(*) FROM patient_with_cohort WHERE {where_sql}", params).fetchone()[0]
    
    # 2. Top conditions
    top_cond_sql = f"""
    {cte}
    SELECT DESCRIPTION, COUNT(DISTINCT PATIENT) as cnt
    FROM conditions
    WHERE PATIENT IN (SELECT Id FROM patient_with_cohort WHERE {where_sql})
    AND STOP IS NULL
    GROUP BY DESCRIPTION ORDER BY cnt DESC LIMIT 5
    """
    top_conds = conn.execute(top_cond_sql, params).df().to_dict('records')

    # 3. HbA1c distribution (filtered population)
    hba1c_sql = f"""
    {cte}
    SELECT VALUE FROM (
        SELECT VALUE, ROW_NUMBER() OVER (PARTITION BY PATIENT ORDER BY DATE DESC) as rn
        FROM observations
        WHERE CODE = '{LOINC_HBA1C}'
        AND PATIENT IN (SELECT Id FROM patient_with_cohort WHERE {where_sql})
    ) WHERE rn = 1
    """
    hba1c_df = conn.execute(hba1c_sql, params).df()
    hba1c_dist = {'normal': 0, 'prediabetes': 0, 'diabetes': 0}
    for val in hba1c_df['VALUE']:
        try:
            v = float(val)
            if v < 5.7: hba1c_dist['normal'] += 1
            elif v < 6.5: hba1c_dist['prediabetes'] += 1
            else: hba1c_dist['diabetes'] += 1
        except: pass

    # 4. BP distribution
    bp_sql = f"""
    {cte}
    SELECT VALUE FROM (
        SELECT VALUE, ROW_NUMBER() OVER (PARTITION BY PATIENT ORDER BY DATE DESC) as rn
        FROM observations
        WHERE CODE = '{LOINC_SBP}'
        AND PATIENT IN (SELECT Id FROM patient_with_cohort WHERE {where_sql})
    ) WHERE rn = 1
    """
    bp_df = conn.execute(bp_sql, params).df()
    bp_dist = {'normal': 0, 'elevated': 0, 'stage1': 0, 'stage2': 0}
    for val in bp_df['VALUE']:
        try:
            v = float(val)
            if v < 120: bp_dist['normal'] += 1
            elif v < 130: bp_dist['elevated'] += 1
            elif v < 140: bp_dist['stage1'] += 1
            else: bp_dist['stage2'] += 1
        except: pass

    # 5. Age distribution
    age_sql = f"{cte} SELECT calculated_age FROM patient_with_cohort WHERE {where_sql}"
    age_df = conn.execute(age_sql, params).df()
    age_dist = {'0-18': 0, '19-35': 0, '36-50': 0, '51-65': 0, '65+': 0}
    for age in age_df['calculated_age']:
        if age <= 18: age_dist['0-18'] += 1
        elif age <= 35: age_dist['19-35'] += 1
        elif age <= 50: age_dist['36-50'] += 1
        elif age <= 65: age_dist['51-65'] += 1
        else: age_dist['65+'] += 1
    
    conn.close()
    return {
        'count': int(count),
        'top_conditions': [{'name': r['DESCRIPTION'], 'count': int(r['cnt'])} for r in top_conds],
        'hba1c_dist': hba1c_dist,
        'bp_dist': bp_dist,
        'age_dist': age_dist,
        'filters': filters
    }

def get_triage_list():
    """
    Fast triage derived from targeted DuckDB SQL filters.
    """
    conn = get_connection()
    
    # Common select block
    select_sql = f"""
    SELECT p.Id as patient_id, p.FIRST, p.LAST, p.CITY, p.BIRTHDATE,
           date_diff('year', cast(p.BIRTHDATE as date), current_date) AS calculated_age,
           (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_SBP}' ORDER BY DATE DESC LIMIT 1) AS latest_sbp,
           (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}' ORDER BY DATE DESC LIMIT 1) AS latest_hba1c,
           (SELECT MAX(DATE) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}') AS last_hba1c_date
    FROM patients p
    WHERE p.DEATHDATE IS NULL
    """

    emergency_sql = f"""
    {select_sql}
    AND (
        (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_SBP}' ORDER BY DATE DESC LIMIT 1) >= 160
        OR
        (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}' ORDER BY DATE DESC LIMIT 1) >= 9.0
    )
    ORDER BY latest_sbp DESC, latest_hba1c DESC
    LIMIT 500
    """
    
    urgent_sql = f"""
    {select_sql}
    AND EXISTS (SELECT 1 FROM conditions c WHERE c.PATIENT = p.Id AND c.STOP IS NULL AND c.CODE IN {HT_CODES + DIAB_CODES})
    AND NOT (
        (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_SBP}' ORDER BY DATE DESC LIMIT 1) >= 160
        OR
        (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}' ORDER BY DATE DESC LIMIT 1) >= 9.0
    )
    AND (
        -- Clinical Tier 2: Stage 2 SBP (140-160) or HbA1c (8.0-9.0)
        (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_SBP}' ORDER BY DATE DESC LIMIT 1) >= 140
        OR
        (SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}' ORDER BY DATE DESC LIMIT 1) >= 8.0
        OR
        -- OR Overdue for labs (365 days)
        (SELECT MAX(DATE) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}') <= current_date - interval '365 days'
        OR
        (SELECT MAX(DATE) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}') IS NULL
    )
    ORDER BY last_hba1c_date ASC
    LIMIT 3000
    """

    emergency_df = conn.execute(emergency_sql).df()
    urgent_df = conn.execute(urgent_sql).df()

    def _format_rows(df, tier):
        rows = []
        for _, r in df.iterrows():
            rows.append({
                'patient_id': r['patient_id'],
                'name': f"{_clean_name(r['FIRST'])} {_clean_name(r['LAST'])}",
                'age': int(r['calculated_age']) if pd.notnull(r['calculated_age']) else None,
                'city': r['CITY'],
                'tier': tier,
                'hba1c': float(r['latest_hba1c']) if pd.notnull(r['latest_hba1c']) else None,
                'sbp': float(r['latest_sbp']) if pd.notnull(r['latest_sbp']) else None,
            })
        return rows

    res = {
        'emergency_patients': _format_rows(emergency_df, 'CRITICAL'),
        'urgent_patients': _format_rows(urgent_df, 'WARNING')
    }
    conn.close()
    return res

@lru_cache(maxsize=1024)
def get_patient_features(patient_id):
    """
    Extracts all 9 ML features directly via DuckDB.
    """
    cte = _get_cohort_cte()
    patient_id = str(patient_id)
    conn = get_connection()
    
    # 1. Base patient data + latest vitals
    sql = f"""
    {cte}
    SELECT 
        p.Id as patient_id, p.calculated_age,
        COALESCE((SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}' ORDER BY DATE DESC LIMIT 1), 5.4) as latest_hba1c,
        COALESCE((SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_SBP}'   ORDER BY DATE DESC LIMIT 1), 120.0) as latest_sbp,
        EXISTS (SELECT 1 FROM conditions WHERE PATIENT = p.Id AND CODE IN {DIAB_CODES} AND STOP IS NULL) as has_diabetes,
        EXISTS (SELECT 1 FROM conditions WHERE PATIENT = p.Id AND CODE IN {HT_CODES} AND STOP IS NULL) as has_hypertension,
        (SELECT date_diff('day', cast(MAX(DATE) as date), current_date) FROM observations WHERE PATIENT = p.Id) as days_since_last_visit
    FROM patient_with_cohort p
    WHERE p.Id = ?
    """
    res = conn.execute(sql, [patient_id]).df()
    if res.empty:
        conn.close()
        return None
    
    row = res.iloc[0]
    
    # 2. Trends (last 3 readings slope)
    def _get_slope(code):
        slope_sql = f"""
        SELECT VALUE, DATE as dt
        FROM observations 
        WHERE PATIENT = ? AND CODE = ? 
        ORDER BY DATE DESC LIMIT 3
        """
        obs = conn.execute(slope_sql, [patient_id, code]).df()
        if len(obs) < 2: return 0.0
        y = [float(v) for v in obs['VALUE'][::-1]] # chronological
        x = list(range(len(y)))
        try:
            from numpy import polyfit
            return float(polyfit(x, y, 1)[0])
        except: return 0.0

    hba1c_trend = _get_slope(LOINC_HBA1C)
    sbp_trend   = _get_slope(LOINC_SBP)
    
    # 3. Care Gaps
    care_gaps = 0
    l_hba1c = row['latest_hba1c'] or 0
    l_sbp   = row['latest_sbp'] or 0
    if l_hba1c >= 8.0: care_gaps += 1
    if l_sbp >= 140: care_gaps += 1
    
    last_date = conn.execute(f"SELECT MAX(DATE) FROM observations WHERE PATIENT = ? AND CODE = '{LOINC_HBA1C}'", [patient_id]).fetchone()[0]
    if last_date:
        last_ts = pd.to_datetime(last_date)
        gap = (datetime.now() - last_ts.to_pydatetime().replace(tzinfo=None)).days
        if gap > 365: care_gaps += 1
    elif row['has_diabetes'] or row['has_hypertension']:
        care_gaps += 1

    # 4. Pediatric & Lifestyle Extras (Weight, Height)
    sql_extras = f"""
    SELECT CODE, VALUE, DATE
    FROM observations
    WHERE PATIENT = ? AND CODE IN ('{LOINC_WEIGHT}', '{LOINC_HEIGHT}', '{LOINC_BMI}')
    ORDER BY DATE DESC
    """
    extras_df = conn.execute(sql_extras, [patient_id]).df()
    
    weight = None
    height = None
    if not extras_df.empty:
        try:
            w_row = extras_df[extras_df['CODE'] == LOINC_WEIGHT]
            h_row = extras_df[extras_df['CODE'] == LOINC_HEIGHT]
            if not w_row.empty: weight = float(w_row.iloc[0]['VALUE'])
            if not h_row.empty: height = float(h_row.iloc[0]['VALUE'])
        except: pass

    conn.close()
    return {
        'latest_hba1c': float(l_hba1c),
        'latest_sbp': float(l_sbp),
        'age': int(row['calculated_age']),
        'has_diabetes': int(row['has_diabetes']),
        'has_hypertension': int(row['has_hypertension']),
        'hba1c_trend': float(hba1c_trend),
        'bp_trend': float(sbp_trend),
        'days_since_last_visit': int(row['days_since_last_visit']) if pd.notnull(row['days_since_last_visit']) else 999,
        'care_gaps_count': int(care_gaps),
        'weight_kg': weight,
        'height_cm': height
    }

def get_batch_patient_features(patient_ids):
    """
    Optimized bulk feature extraction to prevent API timeouts on large triage lists.
    """
    if not patient_ids: return {}
    
    # 1. Base patient stats
    ids_str = ",".join([f"'{pid}'" for pid in patient_ids])
    sql = f"""
    SELECT p.Id as patient_id, 
           date_diff('year', cast(p.BIRTHDATE as date), current_date) AS calculated_age,
           COALESCE((SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}' ORDER BY DATE DESC LIMIT 1), 5.4) as latest_hba1c,
           COALESCE((SELECT try_cast(VALUE AS REAL) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_SBP}'   ORDER BY DATE DESC LIMIT 1), 120.0) as latest_sbp,
           EXISTS (SELECT 1 FROM conditions WHERE PATIENT = p.Id AND CODE IN {DIAB_CODES} AND STOP IS NULL) as has_diabetes,
           EXISTS (SELECT 1 FROM conditions WHERE PATIENT = p.Id AND CODE IN {HT_CODES} AND STOP IS NULL) as has_hypertension,
           (SELECT date_diff('day', cast(MAX(DATE) as date), current_date) FROM observations WHERE PATIENT = p.Id AND CODE = '{LOINC_HBA1C}') as days_since_last_hba1c
    FROM patients p
    WHERE p.Id IN ({ids_str})
    """
    conn = get_connection()
    df_stats = conn.execute(sql).df()
    
    # 2. Trends (last 3 readings per patient/code combo)
    trend_sql = f"""
    SELECT PATIENT, CODE, VALUE, DATE,
           ROW_NUMBER() OVER (PARTITION BY PATIENT, CODE ORDER BY DATE DESC) as rn
    FROM observations
    WHERE PATIENT IN ({ids_str}) AND CODE IN ('{LOINC_HBA1C}', '{LOINC_SBP}')
    QUALIFY rn <= 3
    """
    df_trends = conn.execute(trend_sql).df()
    conn.close()
    
    # 3. Compute Slopes in Python
    from numpy import polyfit
    slopes = {}
    for (pid, code), group in df_trends.groupby(['PATIENT', 'CODE']):
        if pid not in slopes: slopes[pid] = {}
        # Ensure numeric values and chronological order
        y = [float(v) for v in group['VALUE'].tolist()[::-1]]
        if len(y) < 2:
            slopes[pid][code] = 0.0
        else:
            x = list(range(len(y)))
            slopes[pid][code] = float(polyfit(x, y, 1)[0])

    # 4. Assemble Final Features
    results = {}
    for _, row in df_stats.iterrows():
        pid = row['patient_id']
        p_slopes = slopes.get(pid, {})
        l_hba1c = row['latest_hba1c']
        l_sbp = row['latest_sbp']
        
        # Replicate care gap counting logic
        care_gaps = 0
        if l_hba1c >= 8.0: care_gaps += 1
        if l_sbp >= 140: care_gaps += 1
        
        d_gap = row['days_since_last_hba1c']
        if pd.notnull(d_gap):
            if d_gap > 365: care_gaps += 1
        elif row['has_diabetes'] or row['has_hypertension']:
            # No HbA1c on record but has chronic condition
            care_gaps += 1

        results[pid] = {
            'latest_hba1c': float(l_hba1c),
            'latest_sbp': float(l_sbp),
            'age': int(row['calculated_age']),
            'has_diabetes': int(row['has_diabetes']),
            'has_hypertension': int(row['has_hypertension']),
            'hba1c_trend': float(p_slopes.get(LOINC_HBA1C, 0.0)),
            'bp_trend': float(p_slopes.get(LOINC_SBP, 0.0)),
            'days_since_last_visit': int(d_gap) if pd.notnull(d_gap) else 999,
            'care_gaps_count': int(care_gaps)
        }
    return results
