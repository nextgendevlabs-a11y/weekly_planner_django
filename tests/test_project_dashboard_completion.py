from datetime import date
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackClusterVote,
    FeedbackCycle,
    MeetingMaterial,
    MeetingMaterialExtractionDraft,
    MeetingMaterialDraftActionItem,
    MeetingMaterialDraftDecision,
    MeetingMaterialTranscript,
    Membership,
    Project,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "href" in attributes:
            self.links.append(attributes["href"])
        if tag == "form" and "action" in attributes:
            self.forms.append(
                {
                    "action": attributes["action"],
                    "method": attributes.get("method", "get").lower(),
                }
            )


def create_user(
    username="member",
    *,
    is_active=True,
    is_staff=False,
    is_superuser=False,
    first_name="",
    last_name="",
    email="",
):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )


def create_project(name="Weekly Ops"):
    return Project.objects.create(name=name)


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def create_cycle(
    project,
    facilitator,
    *,
    label="Week 34 Retrospective",
    status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
    voting_status=FeedbackCycle.VotingStatus.CLUSTERING,
    opens_at=None,
    summary_text="",
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=opens_at or timezone.now(),
        approved_retrospective_summary_text=summary_text,
    )


def create_card(
    cycle,
    author,
    *,
    text="Private feedback text",
    category=FeedbackCard.Category.START,
    is_anonymous=False,
    cluster=None,
):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        text=text,
        category=category,
        is_anonymous=is_anonymous,
        cluster=cluster,
    )


def create_cluster(cycle, name="Release readiness", **kwargs):
    return FeedbackCluster.objects.create(cycle=cycle, name=name, **kwargs)


def create_action_item(
    cycle,
    owner,
    topic,
    *,
    description="Follow up action",
    status=ActionItem.Status.OPEN,
    due_date=None,
):
    return ActionItem.objects.create(
        cycle=cycle,
        owner=owner,
        topic=topic,
        description=description,
        status=status,
        due_date=due_date,
    )


def create_material(cycle, submitter, *, text="Secret pasted transcript"):
    material = MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=submitter,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
        pasted_transcript_text=text,
        text_character_count=len(text),
    )
    MeetingMaterialTranscript.objects.create(
        meeting_material=material,
        text=f"Processed {text}",
        character_count=len(f"Processed {text}"),
    )
    return material


def dashboard_path(project):
    return reverse("project_dashboard", kwargs={"project_id": project.pk})


def feedback_path(project, cycle):
    return reverse(
        "feedback_submission",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def reveal_path(project, cycle):
    return reverse(
        "feedback_cycle_reveal",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def summary_path(project, cycle):
    return reverse(
        "retrospective_summary",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def action_owner_complete_path(project, cycle, action_item):
    return reverse(
        "action_item_owner_complete",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "action_item_id": action_item.pk,
        },
    )


def parse(response):
    parser = DashboardParser()
    parser.feed(response.content.decode())
    return parser


def assert_not_rendered(response, hidden_values):
    content = response.content.decode()
    for value in hidden_values:
        assert value not in content


def test_dashboard_access_requires_active_project_membership_and_never_leaks_to_protected_users(
    client,
):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    other_project_member = create_user("other-project-member")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    inactive_member = create_user("inactive-member", is_active=False)
    project = create_project("Protected Dashboard Project")
    other_project = create_project("Other Membership Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive_member, project)
    add_membership(other_project_member, other_project)
    cycle = create_cycle(project, facilitator, label="Protected Dashboard Week")
    create_card(cycle, member, text="Protected unrevealed feedback")
    path = dashboard_path(project)

    anonymous_response = client.get(path)
    assert anonymous_response.status_code == 302
    assert anonymous_response["Location"] == f"{reverse('login')}?next={path}"

    for allowed_user in [facilitator, member]:
        client.force_login(allowed_user)
        response = client.get(path)
        assert response.status_code == 200
        assert "Protected Dashboard Project" in response.content.decode()

    hidden_values = [
        "Protected Dashboard Project",
        "Protected Dashboard Week",
        "Protected unrevealed feedback",
        "member",
        "facilitator",
    ]
    for denied_user in [outsider, other_project_member, admin]:
        client.force_login(denied_user)
        response = client.get(path)
        assert response.status_code == 404
        assert_not_rendered(response, hidden_values)

    client.force_login(inactive_member)
    inactive_response = client.get(path)
    assert inactive_response.status_code == 302
    assert inactive_response["Location"] == f"{reverse('login')}?next={path}"


def test_dashboard_no_active_cycle_empty_states_and_create_cycle_entry_point_are_scoped(
    client,
):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("No Active Cycle Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)

    client.force_login(facilitator)
    facilitator_response = client.get(dashboard_path(project))
    facilitator_content = facilitator_response.content.decode()
    facilitator_parser = parse(facilitator_response)

    assert "Current feedback cycle" in facilitator_content
    assert "No feedback cycle has been started for this project yet." in facilitator_content
    assert "Your submission status" in facilitator_content
    assert "There is no open Start, Stop, and Continue submission yet." in (
        facilitator_content
    )
    assert "Retrospective" in facilitator_content
    assert "No retrospective is ready to open yet." in facilitator_content
    assert "Previous retrospectives" in facilitator_content
    assert "No completed retrospectives yet." in facilitator_content
    assert "Open action items" in facilitator_content
    assert "No open action items yet." in facilitator_content
    assert "Create feedback cycle" in facilitator_content
    assert reverse("feedback_cycle_create", kwargs={"project_id": project.pk}) in (
        facilitator_parser.links
    )

    client.force_login(member)
    member_response = client.get(dashboard_path(project))
    member_content = member_response.content.decode()
    assert "No feedback cycle has been started for this project yet." in member_content
    assert "Create feedback cycle" not in member_content
    assert reverse("feedback_cycle_create", kwargs={"project_id": project.pk}) not in (
        parse(member_response).links
    )


def test_collecting_dashboard_shows_own_submission_and_facilitator_progress_without_card_leakage(
    client,
):
    facilitator = create_user("facilitator")
    submitted_member = create_user("submitted-member")
    waiting_member = create_user("waiting-member")
    project = create_project("Collecting Dashboard Project")
    other_project = create_project("Other Collecting Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(submitted_member, project)
    add_membership(waiting_member, project)
    active_cycle = create_cycle(project, facilitator, label="Current Collecting Week")
    completed_cycle = create_cycle(
        project,
        facilitator,
        label="Completed Historical Week",
        status=FeedbackCycle.Status.COMPLETED,
    )
    other_cycle = create_cycle(other_project, facilitator, label="Other Project Week")
    create_card(
        active_cycle,
        submitted_member,
        text="Sensitive anonymous current-cycle text",
        category=FeedbackCard.Category.STOP,
        is_anonymous=True,
    )
    create_card(completed_cycle, waiting_member, text="Old card should not count")
    create_card(other_cycle, waiting_member, text="Other project card should not count")

    client.force_login(facilitator)
    facilitator_response = client.get(dashboard_path(project))
    facilitator_content = facilitator_response.content.decode()
    facilitator_parser = parse(facilitator_response)

    assert "Current Collecting Week" in facilitator_content
    assert "Collecting feedback" in facilitator_content
    assert "Not submitted yet for Current Collecting Week." in facilitator_content
    assert feedback_path(project, active_cycle) in facilitator_parser.links
    assert {"action": reveal_path(project, active_cycle), "method": "post"} in (
        facilitator_parser.forms
    )
    assert facilitator_response.context["team_submission_progress"] == [
        {"user_label": "facilitator", "has_submitted_feedback": False},
        {"user_label": "submitted-member", "has_submitted_feedback": True},
        {"user_label": "waiting-member", "has_submitted_feedback": False},
    ]
    assert "Team submission progress" in facilitator_content
    assert facilitator_content.index("facilitator") < facilitator_content.index(
        "submitted-member"
    )
    assert facilitator_content.index("submitted-member") < facilitator_content.index(
        "waiting-member"
    )
    assert_not_rendered(
        facilitator_response,
        [
            "Sensitive anonymous current-cycle text",
            "Old card should not count",
            "Other project card should not count",
            "Stop",
            "Anonymous",
            "anonymous",
            "card count",
        ],
    )
    assert set(facilitator_response.context["active_cycle"]) == {
        "pk",
        "label",
        "status",
        "status_label",
    }

    client.force_login(submitted_member)
    member_response = client.get(dashboard_path(project))
    member_content = member_response.content.decode()
    assert "Submitted for Current Collecting Week." in member_content
    assert feedback_path(project, active_cycle) in parse(member_response).links
    assert "Team submission progress" not in member_content
    assert "waiting-member" not in member_content
    assert "Reveal feedback" not in member_content


@pytest.mark.parametrize(
    ("voting_status", "expected_stage"),
    [
        (FeedbackCycle.VotingStatus.CLUSTERING, "Clustering / not yet voting"),
        (FeedbackCycle.VotingStatus.OPEN, "Voting open"),
        (
            FeedbackCycle.VotingStatus.CLOSED,
            "Voting closed / discussion and publish-ready",
        ),
    ],
)
def test_retrospective_dashboard_stage_entry_points_do_not_duplicate_board_workflows(
    client,
    voting_status,
    expected_stage,
):
    facilitator = create_user(f"facilitator-{voting_status}")
    member = create_user(f"member-{voting_status}")
    project = create_project(f"Retrospective Dashboard {voting_status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(
        project,
        facilitator,
        label=f"Retrospective {voting_status} Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=voting_status,
        summary_text="Approved text must wait for publication",
    )
    create_cluster(cycle, "Dashboard board topic")
    create_card(cycle, member, text="Revealed board card text")

    client.force_login(member)
    response = client.get(dashboard_path(project))
    content = response.content.decode()
    parser = parse(response)

    assert response.status_code == 200
    assert f"Retrospective {voting_status} Week" in content
    assert expected_stage in content
    assert board_path(project, cycle) in parser.links
    assert "Open retrospective board" in content
    assert feedback_path(project, cycle) not in parser.links
    assert "Open feedback form" not in content
    assert "Your submission status" in content
    assert "There is no open Start, Stop, and Continue submission yet." in content
    assert "Team submission progress" not in content
    assert "Reveal feedback" not in content
    for forbidden in [
        "Open voting",
        "Save votes",
        "Close voting",
        "Save discussion topic",
        "Create action item",
        "Upload meeting material",
        "Approve extracted outcomes",
        "Discard extracted outcomes",
        "Publish summary",
        "Approved text must wait for publication",
        "Revealed board card text",
    ]:
        assert forbidden not in content
    assert set(response.context["active_cycle"]) == {
        "pk",
        "label",
        "status",
        "status_label",
        "retrospective_stage_label",
    }


def test_completed_cycles_move_to_previous_retrospectives_only_and_are_project_scoped(
    client,
):
    facilitator = create_user("facilitator")
    project = create_project("Completed Dashboard Project")
    other_project = create_project("Other Completed Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    old_cycle = create_cycle(
        project,
        facilitator,
        label="Older Published Week",
        status=FeedbackCycle.Status.COMPLETED,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
        opens_at=timezone.now() - timezone.timedelta(days=14),
        summary_text="Older summary text",
    )
    new_cycle = create_cycle(
        project,
        facilitator,
        label="Newest Published Week",
        status=FeedbackCycle.Status.COMPLETED,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
        opens_at=timezone.now() - timezone.timedelta(days=7),
        summary_text="Newest summary text",
    )
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Project Published Week",
        status=FeedbackCycle.Status.COMPLETED,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    client.force_login(facilitator)

    response = client.get(dashboard_path(project))
    content = response.content.decode()
    parser = parse(response)

    assert "No feedback cycle has been started for this project yet." in content
    assert "Create feedback cycle" in content
    assert "Newest Published Week" in content
    assert "Older Published Week" in content
    assert content.index("Newest Published Week") < content.index("Older Published Week")
    assert summary_path(project, new_cycle) in parser.links
    assert summary_path(project, old_cycle) in parser.links
    assert board_path(project, new_cycle) not in parser.links
    assert board_path(project, old_cycle) not in parser.links
    assert "Open retrospective board" not in content
    assert "Other Project Published Week" not in content
    assert summary_path(other_project, other_cycle) not in parser.links
    assert "Newest summary text" not in content
    assert set(response.context["completed_cycles"][0]) == {
        "pk",
        "label",
        "status",
        "status_label",
    }

    create_response = client.post(
        reverse("feedback_cycle_create", kwargs={"project_id": project.pk}),
        {
            "label": "Later Collecting Week",
            "opens_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "closes_at": "",
        },
    )
    assert create_response.status_code == 302
    assert FeedbackCycle.objects.filter(project=project, label="Later Collecting Week").exists()


def test_dashboard_open_action_items_and_owner_completion_are_scoped_and_status_only(
    client,
):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    coworker = create_user("coworker")
    other_member = create_user("other-member")
    project = create_project("Action Dashboard Project")
    other_project = create_project("Other Action Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(coworker, project)
    add_membership(other_member, other_project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Published Action Week",
        status=FeedbackCycle.Status.COMPLETED,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    topic = create_cluster(cycle, "Follow-through topic")
    own_action = create_action_item(
        cycle,
        owner,
        topic,
        description="Owner completes this action",
        due_date=date(2026, 10, 1),
    )
    coworker_action = create_action_item(
        cycle,
        coworker,
        topic,
        description="Coworker keeps this action",
    )
    done_action = create_action_item(
        cycle,
        owner,
        topic,
        description="Already done hidden action",
        status=ActionItem.Status.DONE,
    )
    decision = RetrospectiveDecision.objects.create(
        cycle=cycle,
        topic=topic,
        text="Do not mutate decision",
    )
    card = create_card(cycle, owner, text="Do not mutate card", cluster=topic)
    vote = FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=owner,
        cluster=topic,
        vote_count=3,
    )
    other_cycle = create_cycle(other_project, facilitator, label="Other Action Week")
    other_topic = create_cluster(other_cycle, "Other topic")
    other_action = create_action_item(
        other_cycle,
        other_member,
        other_topic,
        description="Other project hidden action",
    )

    client.force_login(owner)
    response = client.get(dashboard_path(project))
    content = response.content.decode()
    forms = parse(response).forms

    assert "Owner completes this action" in content
    assert "Coworker keeps this action" in content
    assert "Owner: owner" in content
    assert "Owner: coworker" in content
    assert "Topic: Follow-through topic" in content
    assert "Cycle: Published Action Week" in content
    assert "Due: Oct. 1, 2026" in content
    assert "Due: No due date" in content
    assert "Already done hidden action" not in content
    assert "Other project hidden action" not in content
    assert action_owner_complete_path(project, cycle, own_action) in [
        form["action"] for form in forms
    ]
    assert action_owner_complete_path(project, cycle, coworker_action) not in [
        form["action"] for form in forms
    ]
    assert action_owner_complete_path(project, cycle, done_action) not in [
        form["action"] for form in forms
    ]
    assert set(response.context["open_action_items"][0]) == {
        "pk",
        "description",
        "owner_label",
        "topic_name",
        "cycle_pk",
        "cycle_label",
        "due_date",
        "can_owner_complete",
    }

    complete_response = client.post(
        action_owner_complete_path(project, cycle, own_action),
        {
            "description": "Tampered description",
            "owner": str(coworker.pk),
            "due_date": "2026-11-01",
            "topic": str(topic.pk),
            "cycle": str(other_cycle.pk),
            "project": str(other_project.pk),
            "status": ActionItem.Status.OPEN,
        },
    )
    assert complete_response.status_code == 302
    own_action.refresh_from_db()
    coworker_action.refresh_from_db()
    done_action.refresh_from_db()
    decision.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    other_action.refresh_from_db()
    assert own_action.status == ActionItem.Status.DONE
    assert own_action.description == "Owner completes this action"
    assert own_action.owner == owner
    assert own_action.due_date == date(2026, 10, 1)
    assert own_action.topic == topic
    assert own_action.cycle == cycle
    assert coworker_action.status == ActionItem.Status.OPEN
    assert done_action.status == ActionItem.Status.DONE
    assert decision.text == "Do not mutate decision"
    assert card.text == "Do not mutate card"
    assert vote.vote_count == 3
    assert other_action.status == ActionItem.Status.OPEN

    updated_dashboard = client.get(dashboard_path(project))
    updated_content = updated_dashboard.content.decode()
    assert "Owner completes this action" not in updated_content
    assert "Coworker keeps this action" in updated_content
    summary = client.get(summary_path(project, cycle))
    assert "Owner completes this action" in summary.content.decode()
    assert "Status: Done" in summary.content.decode()


def test_dashboard_hides_protected_workflow_data_and_review_surfaces(client):
    facilitator = create_user("facilitator")
    member = create_user(
        "member",
        first_name="Mira",
        last_name="Member",
        email="member@example.test",
    )
    anonymous_author = create_user(
        "secret-author",
        first_name="Secret",
        last_name="Author",
        email="secret-author@example.test",
    )
    project = create_project("Privacy Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(anonymous_author, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Privacy Dashboard Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
        summary_text="Approved summary hidden before publication",
    )
    topic = create_cluster(
        cycle,
        "Private topic",
        discussion_notes="Private discussion notes",
    )
    create_card(
        cycle,
        anonymous_author,
        text="Anonymous feedback hidden on dashboard",
        is_anonymous=True,
        cluster=topic,
    )
    FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=member,
        cluster=topic,
        vote_count=3,
    )
    RetrospectiveDecision.objects.create(
        cycle=cycle,
        topic=topic,
        text="Private decision hidden on dashboard",
    )
    create_action_item(
        cycle,
        member,
        topic,
        description="Private action hidden while done",
        status=ActionItem.Status.DONE,
        due_date=date(2026, 10, 1),
    )
    material = create_material(cycle, facilitator, text="Dashboard source transcript")
    draft = MeetingMaterialExtractionDraft.objects.create(
        meeting_material=material,
        retrospective_summary_text="Draft summary hidden on dashboard",
    )
    MeetingMaterialDraftDecision.objects.create(
        extraction_draft=draft,
        text="Draft decision hidden on dashboard",
        topic_candidate="Draft topic candidate hidden",
        matched_topic=topic,
    )
    MeetingMaterialDraftActionItem.objects.create(
        extraction_draft=draft,
        description="Draft action hidden on dashboard",
        owner_candidate="Draft owner candidate hidden",
        matched_owner=member,
        topic_candidate="Draft action topic candidate hidden",
        matched_topic=topic,
    )
    client.force_login(member)

    response = client.get(dashboard_path(project))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Open retrospective board" in content
    assert_not_rendered(
        response,
        [
            "Approved summary hidden before publication",
            "Private topic",
            "Private discussion notes",
            "Anonymous feedback hidden on dashboard",
            "Anonymous contributor",
            "secret-author",
            "Secret Author",
            "secret-author@example.test",
            "member@example.test",
            "3 votes",
            "Private decision hidden on dashboard",
            "Private action hidden while done",
            "Oct. 1, 2026",
            "Dashboard source transcript",
            "Processed Dashboard source transcript",
            "Draft summary hidden on dashboard",
            "Draft decision hidden on dashboard",
            "Draft action hidden on dashboard",
            "Draft topic candidate hidden",
            "Draft owner candidate hidden",
            "Draft action topic candidate hidden",
            "source_file",
            "pasted_transcript_text",
            "processed_transcript",
            "failure",
            "Approve extracted outcomes",
            "Discard extracted outcomes",
            "Review extracted outcomes",
        ],
    )


def test_dashboard_facilitator_actions_and_direct_routes_remain_facilitator_only(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Facilitator Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Revealable Week")

    client.force_login(member)
    member_dashboard = client.get(dashboard_path(project))
    member_content = member_dashboard.content.decode()
    assert "Reveal feedback" not in member_content
    assert "Create feedback cycle" not in member_content
    assert reveal_path(project, cycle) not in [
        form["action"] for form in parse(member_dashboard).forms
    ]
    assert client.post(reveal_path(project, cycle)).status_code == 404
    assert client.get(reverse("feedback_cycle_create", kwargs={"project_id": project.pk})).status_code == 404
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK

    client.force_login(facilitator)
    facilitator_dashboard = client.get(dashboard_path(project))
    assert "Reveal feedback" in facilitator_dashboard.content.decode()
    assert {"action": reveal_path(project, cycle), "method": "post"} in parse(
        facilitator_dashboard
    ).forms
    assert "Create feedback cycle" not in facilitator_dashboard.content.decode()


def test_dashboard_out_of_scope_surface_guards(client):
    facilitator = create_user("facilitator")
    project = create_project("Scope Guard Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(
        project,
        facilitator,
        label="Scope Guard Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    client.force_login(facilitator)

    response = client.get(dashboard_path(project))
    content = response.content.decode().lower()
    links = parse(response).links

    assert board_path(project, cycle) in links
    for forbidden in [
        "cross-project dashboard",
        "analytics",
        "export",
        "custom retrospective framework",
        "employee scoring",
        "survey reporting",
        "automated reminder",
        "notification workflow",
        "escalation",
        "slack",
        "email",
        "calendar",
        "integration",
        "priority",
        "subtask",
        "dependency",
        "general task board",
        "project-management",
    ]:
        assert forbidden not in content
