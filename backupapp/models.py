import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


class BackupInvitation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token_link = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Zaproszenie dla {self.user.username} ({'Zużyte'
        if self.is_used else 'Aktywne'})"
        

class BackupAgent(models.Model):

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    log = models.TextField()

    def __str__(self):
        return f"{self.user} {self.status} {self.created}"       
        
