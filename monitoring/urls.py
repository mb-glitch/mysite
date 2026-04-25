from django.urls import path
from .views import MonitoringReceiverView

urlpatterns = [
    path('monitoring/', MonitoringReceiverView.as_view(), name='receive_monitoring'),
]
