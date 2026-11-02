from django.urls import path
from . import views

app_name = 'app-home'

urlpatterns = [
    path('', views.pagina_inicial, name='buscar_endereco')
]