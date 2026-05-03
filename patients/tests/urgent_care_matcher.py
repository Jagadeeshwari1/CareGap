"""
Urgent Care Matcher
───────────────────
Matches a HIGH-risk patient to nearby urgent care facilities
based on:
  1. City / geographic proximity
  2. Insurance type compatibility
  3. Facility rating (sorted)

Uses the UrgentCare model populated from seed data.
In production, replace with a real API (e.g. Google Places, Yelp Fusion).
"""

import math
from patients.models import UrgentCare


# ── Insurance normalization ────────────────────────────────────────
INSURANCE_MAP = {
    # Synthea payer names → our internal bucket
    'medicaid':             'medicaid',
    'medicare':             'medicare',
    'blue cross blue shield': 'private',
    'aetna':                'private',
    'united healthcare':    'private',
    'humana':               'private',
    'cigna':                'private',
    'anthem':               'private',
    'no insurance':         'uninsured',
    'self pay':             'uninsured',
    'uninsured':            'uninsured',
}


def normalize_insurance(raw: str) -> str:
    """Map raw Synthea payer name to internal bucket."""
    if not raw:
        return 'unknown'
    key = raw.lower().strip()
    for fragment, bucket in INSURANCE_MAP.items():
        if fragment in key:
            return bucket
    return 'private'  # default assumption


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance between two lat/lon points in km."""
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_urgent_cares(patient, max_results: int = 5) -> list[dict]:
    """
    Return ranked list of urgent cares for a HIGH-risk patient.

    Strategy:
      1. Filter by insurance compatibility.
      2. If patient has coordinates, calculate distance to ALL facilities.
      3. Sort by Distance ASC, then Rating DESC.
      (Since there is a small number of facilities, we don't need to limit by city first)
    """
    insurance_type = normalize_insurance(patient.insurance)

    # Build insurance filter
    insurance_filter = {}
    if insurance_type == 'medicaid':
        insurance_filter['accepts_medicaid'] = True
    elif insurance_type == 'medicare':
        insurance_filter['accepts_medicare'] = True
    elif insurance_type == 'uninsured':
        insurance_filter['accepts_uninsured'] = True
    else:
        insurance_filter['accepts_private'] = True

    # Fetch all matching insurance first
    facilities = list(UrgentCare.objects.filter(**insurance_filter))

    # Fallback to all if somehow none match the strict insurance (rare, but just in case)
    if not facilities:
        facilities = list(UrgentCare.objects.all())

    # Enrich with distance and parse
    result = []
    for uc in facilities:
        distance_km = None
        if all([patient.lat, patient.lon, uc.lat, uc.lon]):
            try:
                distance_km = round(haversine_km(float(patient.lat), float(patient.lon), float(uc.lat), float(uc.lon)), 1)
            except (ValueError, TypeError):
                pass

        result.append({
            'id':              uc.id,
            'name':            uc.name,
            'address':         uc.address,
            'city':            uc.city,
            'phone':           uc.phone,
            'rating':          uc.rating,
            'open_24h':        uc.open_24h,
            'distance_km':     distance_km,
            'insurance_accepted': insurance_type,
            'accepts': {
                'medicaid':  uc.accepts_medicaid,
                'medicare':  uc.accepts_medicare,
                'private':   uc.accepts_private,
                'uninsured': uc.accepts_uninsured,
            }
        })

    # Sort: distance first if available, then by rating descending
    result.sort(key=lambda x: (
        x['distance_km'] if x['distance_km'] is not None else 99999,
        -x['rating'] if x['rating'] is not None else 0
    ))

    return result[:max_results]
