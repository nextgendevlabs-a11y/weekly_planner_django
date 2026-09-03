"""URL configuration for the weekly_planner project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from weekly_planner.views import HomeView


urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
