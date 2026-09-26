# ==============================================================================
# monitoring/urls.py
# Aplikacja: MONITORING
# ==============================================================================
from django.urls import path
from .views import MonitoringReceiverView

app_name = 'monitoring'

urlpatterns = [
    # Główny endpoint DRF przyjmujący meldunki urządzeń po Tokenie Konta Maszynowego:
    path('', MonitoringReceiverView.as_view(), name='receive_monitoring'),
    path('monitoring/', MonitoringReceiverView.as_view(), name='receive_monitoring_alias'),
]

