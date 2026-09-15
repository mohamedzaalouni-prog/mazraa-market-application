from django.urls import path

from .views import prediction_view

app_name = 'prediction'

urlpatterns = [
    path('', prediction_view, name='prediction_home'),
]
