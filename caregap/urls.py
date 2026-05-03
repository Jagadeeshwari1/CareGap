from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/patients/', include('patients.urls')),
    path('api/rag/',      include('rag.urls')),
    # Serve the dashboard SPA for any non-API route
    path('', TemplateView.as_view(template_name='dashboard.html'), name='home'),
]
