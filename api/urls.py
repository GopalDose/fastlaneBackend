from django.urls import path
from services.views import login, registration, validate_address, get_shipping_rate, bulk_shipping_rate_calculation, all_details


urlpatterns = [
    path("login/", login),
    path("register/", registration),
    path("validate_address/", validate_address),
    path("get_shipping/", get_shipping_rate),
    path("bulk_calculate/", bulk_shipping_rate_calculation),
    path("all_details/", all_details),
]
