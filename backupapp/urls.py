# ==============================================================================
# backupapp/urls.py
# Aplikacja: BACKUPAPP
# ==============================================================================
from django.urls import path
from django.contrib.auth.views import LoginView
from . import views

urlpatterns = [
    path('login/', LoginView.as_view(template_name='backup_login.html', next_page='backup_dashboard'), name='backup_login'),
    path('', views.backup_dashboard, name='backup_dashboard'),
    path('dashboard/', views.backup_dashboard, name='backup_dashboard_alias'),
    path('invitations/<int:invitation_id>/mobile-token/', views.create_mobile_token, name='create_mobile_token'),
    path('setup/<str:token>/', views.setup_phone, name='setup_phone'),
    path('claim/<str:token>/', views.claim_invitation, name='claim_invitation'),
    path('api/info/', views.backup_script_info, name='backup-info'),
    path('api/script/', views.get_backup_script, name='backup-core'),
]

