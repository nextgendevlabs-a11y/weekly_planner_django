"""Page views for the weekly planner project."""

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.urls import reverse_lazy
from django.views.generic import DetailView
from django.views.generic import FormView
from django.views.generic import TemplateView

from projects.forms import FeedbackCycleCreateForm
from projects.models import FeedbackCycle, Project
from projects.permissions import can_facilitate_project, facilitatable_projects_for
from projects.permissions import viewable_projects_for


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
    """List projects available to the signed-in user."""

    template_name = "projects/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["projects"] = viewable_projects_for(self.request.user)
        return context


class ProjectDashboardView(LoginRequiredMixin, DetailView):
    """Membership-scoped dashboard shell for one retrospective project."""

    context_object_name = "project"
    model = Project
    pk_url_kwarg = "project_id"
    template_name = "projects/dashboard.html"

    def get_queryset(self):
        return viewable_projects_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_cycle = (
            self.object.feedback_cycles.exclude(status=FeedbackCycle.Status.COMPLETED)
            .order_by("-opens_at", "-id")
            .first()
        )
        context["active_cycle"] = active_cycle
        context["can_create_feedback_cycle"] = (
            active_cycle is None and can_facilitate_project(self.request.user, self.object)
        )
        return context


class FeedbackCycleCreateView(LoginRequiredMixin, FormView):
    """Facilitator-only form for starting a project's weekly feedback cycle."""

    form_class = FeedbackCycleCreateForm
    template_name = "projects/feedback_cycle_form.html"

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                facilitatable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.get_project()
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.get_project()
        kwargs["facilitator"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "project_dashboard",
            kwargs={"project_id": self.get_project().pk},
        )
