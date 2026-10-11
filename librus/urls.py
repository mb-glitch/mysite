from django.urls import path

from . import views


app_name = 'librus'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('dziecko/<int:child_id>/oceny/', views.grades, name='grades'),
]
