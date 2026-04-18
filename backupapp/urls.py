from django.urls import path

from . import views

urlpatterns = [
    path('scripts/backup_script_info/', views.backup_script_info, name='backup_script_info'),
    path('scripts/backup-core/', views.get_backup_script, name='backup-core'),
    path("setup/<uuid:link_id>/", views.claim_invitation, name="claim_invitation"),
    path('', views.backup_dashboard, name='backup_dashboard'),    
]

