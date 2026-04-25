from rest_framework import serializers
from .models import LogEntry

class LogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LogEntry
        # Użytkownik przesyła tylko te dane:
        fields = ['status', 'status_code', 'execute_time_ms', 'message']
