from django.shortcuts import render

# Create your views here.
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.authentication import TokenAuthentication
from .models import MonitoredService
from .serializers import LogEntrySerializer

class MonitoringReceiverView(APIView):
    # Wymagamy tokena i autentykacji
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # 1. Znajdź usługę przypisaną do zalogowanego użytkownika (bota)
        try:
            service = request.user.monitoredservice
        except MonitoredService.DoesNotExist:
            return Response(
                {"error": "Użytkownik nie ma przypisanej usługi monitorowanej."},
                status=status.HTTP_403_FORBIDDEN
            )

        # 2. Zwaliduj dane z JSON-a
        serializer = LogEntrySerializer(data=request.data)
        if serializer.is_valid():
            # 3. Zapisz log, ręcznie dodając powiązanie z usługą
            serializer.save(service=service)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
