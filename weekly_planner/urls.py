"""URL configuration for the weekly_planner project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from weekly_planner.views import (
    FeedbackCycleCreateView,
    HomeView,
    ProjectDashboardView,
    ProjectsView,
    SignUpView,
)


urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("accounts/signup/", SignUpView.as_view(), name="signup"),
    path(
        "accounts/login/",
        LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("projects/", ProjectsView.as_view(), name="projects"),
    path(
        "projects/<int:project_id>/",
        ProjectDashboardView.as_view(),
        name="project_dashboard",
    ),
    path(
        "projects/<int:project_id>/cycles/new/",
        FeedbackCycleCreateView.as_view(),
        name="feedback_cycle_create",
    ),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
