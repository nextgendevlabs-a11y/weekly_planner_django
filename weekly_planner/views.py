"""Page views for the weekly planner project."""

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import FormView
from django.views.generic import TemplateView


class HomeView(TemplateView):
    """Public landing page for the retrospective workflow."""

    template_name = "home.html"


class SignUpView(FormView):
    """Create an account and start the authenticated workflow."""

    form_class = UserCreationForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("projects")

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return super().form_valid(form)


class ProjectsView(LoginRequiredMixin, TemplateView):
    """Authenticated starting point for future retrospective projects."""

    template_name = "projects/index.html"
