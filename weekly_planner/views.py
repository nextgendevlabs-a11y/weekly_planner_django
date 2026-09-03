"""Page views for the weekly planner project."""

from django.views.generic import TemplateView


class HomeView(TemplateView):
    """Public landing page for the retrospective workflow."""

    template_name = "home.html"
