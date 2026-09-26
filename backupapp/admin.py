# ==============================================================================
# backupapp/admin.py
# Aplikacja: BACKUPAPP
# ==============================================================================
from django.contrib import admin
from .models import BackupInvitation


@admin.register(BackupInvitation)
class BackupInvitationAdmin(admin.ModelAdmin):
    list_display = ('user', 'token_link', 'is_used', 'created_at')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__username', 'token_link')
    list_editable = ('is_used',)
    readonly_fields = ('created_at',)
    ordering = ('is_used', 'user__username')

