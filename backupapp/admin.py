from django.contrib import admin
from .models import BackupInvitation, BackupAgent


@admin.register(BackupInvitation)
class BackupInvitationAdmin(admin.ModelAdmin):
    # Wyświetla Token użytkownika oraz status czy jest już użyty
    list_display = ('user', 'token_link', 'is_used', 'created_at')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__username', 'token_link')
    readonly_fields = ('created_at',)

    # Pozwala w 1 kliknięcie odznaczyć lub zaznaczyć is_used
    list_editable = ('is_used',)


@admin.register(BackupAgent)
class BackupAgentAdmin(admin.ModelAdmin):
    list_display = ('user', 'last_status_code', 'last_seen', 'created_at')
    list_filter = ('last_status_code', 'last_seen')
    search_fields = ('user__username',)
    readonly_fields = ('created_at', 'last_seen')

