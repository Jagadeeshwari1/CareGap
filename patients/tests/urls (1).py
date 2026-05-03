from django.urls import path
from . import views

urlpatterns = [
    path('search/',                        views.patient_search,        name='patient-search'),
    path('stats/basic/',                   views.dashboard_stats_basic, name='dashboard-stats-basic'),
    path('stats/',                         views.dashboard_stats,       name='dashboard-stats'),
    path('analytics/',                     views.analytics,             name='analytics'),
    path('triage/',                        views.triage_list,           name='triage-list'),
    path('resources/forecast/',            views.resource_forecast,     name='resource-forecast'),
    path('<str:patient_id>/',              views.patient_detail,        name='patient-detail'),
    path('<str:patient_id>/risk/',         views.patient_risk,          name='patient-risk'),
    path('<str:patient_id>/urgent-care/',  views.patient_urgent_cares,  name='patient-urgent-care'),
    path('<str:patient_id>/predict/',      views.patient_predict,       name='patient-predict'),
]
