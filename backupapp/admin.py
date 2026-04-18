from django.contrib import admin
from .models import BackupInvitation
from django.utils import timezone
from datetime import timedelta


@admin.register(BackupInvitation)
class BackupInvitationAdmin(admin.ModelAdmin):
    # To sprawi, że w tabeli będziesz widział od razu ważne informacje
    list_display = ('user', 'token_link', 'is_used', 'created_at')
    # Dodanie filtrów po prawej stronie
    list_filter = ('is_used', 'user')
    # Możliwość wyszukiwania po nazwie użytkownika
    search_fields = ('user__username',)
    # Tylko do odczytu dla kodu UUID (żeby go nie zmienić przez przypadek)
    readonly_fields = ('token_link',)
