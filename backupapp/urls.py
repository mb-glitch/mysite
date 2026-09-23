from django.urls import path
from . import views

urlpatterns = [
    # 1. Dashboard z listą użytkowników, tokenami i statusem
    path('', views.backup_dashboard, name='backup_dashboard'),
    path('dashboard/', views.backup_dashboard, name='backup_dashboard_alias'),

    # 2. POPRAWIONE: <str:token> zamiast <uuid:link_id>
    path('setup/<str:token>/', views.setup_phone, name='setup_phone'),

    # 3. Potwierdzenie instalacji (oznacza token jako skonfigurowany)
    path('claim/<str:token>/', views.claim_invitation, name='claim_invitation'),

    # 4. API dla skryptu 10_backup.sh
    path('info/', views.backup_script_info, name='backup-info'),
    path('script/', views.get_backup_script, name='backup-core'),
    path('rclone/', views.get_rclone_conf, name='backup-rclone'),
    path('report/', views.backup_report, name='backup-report'),
]
