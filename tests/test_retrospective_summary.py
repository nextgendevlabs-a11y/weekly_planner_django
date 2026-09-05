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
    MeetingMaterialTranscript,
    Membership,
    Project,
    RetrospectiveAttendance,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


class FormAndLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.links = []
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and "action" in attributes:
            self.forms.append(
                {
                    "action": attributes["action"],
                    "method": attributes.get("method", "get").lower(),
                }
            )
        if tag == "a" and "href" in attributes:
            self.links.append(attributes["href"])
        if tag == "input":
            self.inputs.append(attributes)


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
    status=FeedbackCycle.Status.RETROSPECTIVE,
    voting_status=FeedbackCycle.VotingStatus.CLOSED,
    summary_text="",
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=timezone.now(),
        approved_retrospective_summary_text=summary_text,
    )


def create_cluster(cycle, name="Release readiness", **kwargs):
    return FeedbackCluster.objects.create(cycle=cycle, name=name, **kwargs)


def create_card(
    cycle,
    author,
    *,
    text="Keep this card",
    category=FeedbackCard.Category.START,
    cluster=None,
    is_anonymous=False,
):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=category,
        text=text,
        cluster=cluster,
        is_anonymous=is_anonymous,
    )


def create_vote(cycle, voter, cluster, vote_count=3):
    return FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=voter,
        cluster=cluster,
        vote_count=vote_count,
    )


def create_action_item(
    cycle,
    owner,
    topic,
    *,
    description="Update the release checklist",
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


def create_decision(cycle, *, text="Keep release reviews", topic=None):
    return RetrospectiveDecision.objects.create(cycle=cycle, text=text, topic=topic)


def create_material(
    cycle,
    submitter,
    *,
    processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
    pasted_transcript_text="Secret pasted transcript",
    failure_message="",
):
    material = MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=submitter,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status=processing_status,
        pasted_transcript_text=pasted_transcript_text,
        text_character_count=len(pasted_transcript_text),
        failure_message=failure_message,
    )
    if processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED:
        MeetingMaterialTranscript.objects.create(
            meeting_material=material,
            text=f"Processed {pasted_transcript_text}",
            character_count=len(f"Processed {pasted_transcript_text}"),
        )
    return material


def create_draft(
    material,
    *,
    summary_text="Secret draft summary",
    review_status=MeetingMaterialExtractionDraft.ReviewStatus.PENDING,
):
    return MeetingMaterialExtractionDraft.objects.create(
        meeting_material=material,
        retrospective_summary_text=summary_text,
        review_status=review_status,
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def publish_path(project, cycle):
    return reverse(
        "retrospective_summary_publish",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def summary_path(project, cycle):
    return reverse(
        "retrospective_summary",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def dashboard_path(project):
    return reverse("project_dashboard", kwargs={"project_id": project.pk})


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
    parser = FormAndLinkParser()
    parser.feed(response.content.decode())
    return parser


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def publish_payload(*attendees, **extra):
    data = {"attendees": [str(user.pk) for user in attendees]}
    data.update({key: str(value) for key, value in extra.items()})
    return data


def test_publish_control_is_facilitator_only_and_only_for_closed_voting_retrospective(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Publish Gate Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    create_cluster(cycle)

    client.force_login(facilitator)
    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "Publish summary" in content
    assert publish_path(project, cycle) in parse(board_response).links

    client.force_login(member)
    member_board = client.get(board_path(project, cycle))
    assert "Publish summary" not in member_board.content.decode()
    assert client.get(publish_path(project, cycle)).status_code == 404
    member_publish = client.post(publish_path(project, cycle), publish_payload(member))
    assert member_publish.status_code == 404
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE

    for status, voting_status in [
        (FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.VotingStatus.CLOSED),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.CLUSTERING),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.OPEN),
        (FeedbackCycle.Status.COMPLETED, FeedbackCycle.VotingStatus.CLOSED),
    ]:
        gated_project = create_project(f"Gated {status} {voting_status}")
        gated_facilitator = create_user(f"facilitator-{status}-{voting_status}")
        add_membership(gated_facilitator, gated_project, Membership.Role.FACILITATOR)
        gated_cycle = create_cycle(
            gated_project,
            gated_facilitator,
            status=status,
            voting_status=voting_status,
        )
        create_cluster(gated_cycle, "Hidden publish topic")
        client.force_login(gated_facilitator)

        board = client.get(board_path(gated_project, gated_cycle))
        publish = client.post(
            publish_path(gated_project, gated_cycle),
            publish_payload(gated_facilitator),
        )
        if status == FeedbackCycle.Status.RETROSPECTIVE:
            assert board.status_code == 200
            assert "Publish summary" not in board.content.decode()
        else:
            assert board.status_code == 404
        assert publish.status_code == 404
        gated_cycle.refresh_from_db()
        assert gated_cycle.status == status
        assert RetrospectiveAttendance.objects.filter(cycle=gated_cycle).count() == 0


def test_processing_and_review_blockers_show_current_cycle_state_without_mutation(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Blocked Publish Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Blocked Publish Week",
        summary_text="Approved text stays private",
    )
    topic = create_cluster(cycle, "Blocked topic", discussion_notes="Keep notes")
    card = create_card(cycle, member, text="Do not mutate card", cluster=topic)
    vote = create_vote(cycle, member, topic)
    decision = create_decision(cycle, text="Do not mutate decision", topic=topic)
    action = create_action_item(cycle, member, topic, description="Do not mutate action")
    queued = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
        pasted_transcript_text="Queued secret source",
    )
    processing = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.PROCESSING,
        pasted_transcript_text="Processing secret source",
    )
    succeeded = create_material(cycle, facilitator, pasted_transcript_text="Pending source")
    draft = create_draft(succeeded, summary_text="Pending draft secret")
    client.force_login(facilitator)

    response = client.post(publish_path(project, cycle), publish_payload(member))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Meeting material is still processing." in content
    assert "Extracted meeting outcomes are pending review." in content
    for secret in [
        "Queued secret source",
        "Processing secret source",
        "Pending source",
        "Pending draft secret",
        "Do not mutate decision",
        "Do not mutate action",
    ]:
        assert secret not in content
    cycle.refresh_from_db()
    topic.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    decision.refresh_from_db()
    action.refresh_from_db()
    queued.refresh_from_db()
    processing.refresh_from_db()
    draft.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE
    assert cycle.approved_retrospective_summary_text == "Approved text stays private"
    assert RetrospectiveAttendance.objects.count() == 0
    assert topic.discussion_notes == "Keep notes"
    assert card.cluster == topic
    assert vote.vote_count == 3
    assert decision.text == "Do not mutate decision"
    assert action.description == "Do not mutate action"
    assert queued.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert processing.processing_status == MeetingMaterial.ProcessingStatus.PROCESSING
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING


@pytest.mark.parametrize(
    "material_state",
    ["none", "failed", "approved", "discarded", "succeeded_without_draft"],
)
def test_publish_allowed_material_states_and_no_attendees_empty_state(
    client,
    material_state,
):
    facilitator = create_user(f"facilitator-{material_state}")
    member = create_user(f"member-{material_state}")
    project = create_project(f"Allowed Publish {material_state}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, summary_text="")
    if material_state == "failed":
        create_material(
            cycle,
            facilitator,
            processing_status=MeetingMaterial.ProcessingStatus.FAILED,
            failure_message="Failed material details stay hidden",
        )
    elif material_state in {"approved", "discarded"}:
        material = create_material(cycle, facilitator)
        create_draft(
            material,
            review_status=(
                MeetingMaterialExtractionDraft.ReviewStatus.APPROVED
                if material_state == "approved"
                else MeetingMaterialExtractionDraft.ReviewStatus.DISCARDED
            ),
        )
    elif material_state == "succeeded_without_draft":
        create_material(cycle, facilitator)
    client.force_login(facilitator)

    response = client.post(publish_path(project, cycle), publish_payload())

    assert response.status_code == 302
    assert response["Location"] == summary_path(project, cycle)
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COMPLETED
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
    assert cycle.summary_active_member_count == 2
    assert RetrospectiveAttendance.objects.filter(cycle=cycle).count() == 0

    summary = client.get(summary_path(project, cycle))
    content = summary.content.decode()
    assert "No attendees selected." in content
    assert "No approved summary saved." in content
    assert "Failed material details stay hidden" not in content


def test_attendance_choices_are_active_same_project_members_and_tampering_is_rejected(
    client,
):
    facilitator = create_user("facilitator")
    member = create_user("member")
    inactive = create_user("inactive", is_active=False)
    non_member = create_user("non-member")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    other_member = create_user("other-member")
    project = create_project("Attendance Project")
    other_project = create_project("Other Attendance Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project)
    add_membership(other_member, other_project)
    cycle = create_cycle(project, facilitator)
    client.force_login(facilitator)

    response = client.get(publish_path(project, cycle))
    content = response.content.decode()
    assert response.status_code == 200
    assert "facilitator" in content
    assert "member" in content
    assert "inactive" not in content
    assert "non-member" not in content
    assert "admin" not in content
    assert "other-member" not in content

    for bad_user in [inactive, non_member, admin, other_member]:
        bad_response = client.post(
            publish_path(project, cycle),
            publish_payload(bad_user),
        )
        assert bad_response.status_code == 404
        assert_no_secret_leak(
            bad_response,
            [
                "Attendance Project",
                "Other Attendance Project",
                "inactive",
                "non-member",
                "admin",
                "other-member",
            ],
        )
        cycle.refresh_from_db()
        assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE
        assert RetrospectiveAttendance.objects.filter(cycle=cycle).count() == 0


def test_valid_publish_is_atomic_completes_cycle_and_updates_dashboard(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    attendee = create_user("attendee")
    project = create_project("Atomic Publish Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(attendee, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Atomic Publish Week",
        summary_text="Approved final summary",
    )
    topic = create_cluster(cycle, "Atomic topic", discussion_notes="Atomic notes")
    card = create_card(cycle, member, text="Atomic feedback card", cluster=topic)
    vote = create_vote(cycle, member, topic)
    decision = create_decision(cycle, text="Atomic decision", topic=topic)
    action = create_action_item(cycle, member, topic, description="Atomic action")
    reviewed_material = create_material(cycle, facilitator, pasted_transcript_text="Reviewed source")
    draft = create_draft(
        reviewed_material,
        summary_text="Reviewed draft",
        review_status=MeetingMaterialExtractionDraft.ReviewStatus.APPROVED,
    )
    client.force_login(facilitator)

    response = client.post(
        publish_path(project, cycle),
        publish_payload(facilitator, attendee),
    )

    assert response.status_code == 302
    cycle.refresh_from_db()
    topic.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    decision.refresh_from_db()
    action.refresh_from_db()
    reviewed_material.refresh_from_db()
    draft.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COMPLETED
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
    assert cycle.approved_retrospective_summary_text == "Approved final summary"
    assert cycle.summary_active_member_count == 3
    assert set(
        RetrospectiveAttendance.objects.filter(cycle=cycle).values_list(
            "user_id",
            flat=True,
        )
    ) == {facilitator.pk, attendee.pk}
    assert topic.name == "Atomic topic"
    assert topic.discussion_notes == "Atomic notes"
    assert card.text == "Atomic feedback card"
    assert card.cluster == topic
    assert vote.vote_count == 3
    assert decision.text == "Atomic decision"
    assert action.description == "Atomic action"
    assert reviewed_material.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.APPROVED

    assert client.get(board_path(project, cycle)).status_code == 404
    dashboard = client.get(dashboard_path(project))
    dashboard_content = dashboard.content.decode()
    dashboard_links = parse(dashboard).links
    assert "Atomic Publish Week" in dashboard_content
    assert summary_path(project, cycle) in dashboard_links
    assert board_path(project, cycle) not in dashboard_links
    assert "Open retrospective board" not in dashboard_content
    assert "Create feedback cycle" in dashboard_content

    create_response = client.post(
        reverse("feedback_cycle_create", kwargs={"project_id": project.pk}),
        {
            "label": "Next Week",
            "opens_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "closes_at": "",
        },
    )
    assert create_response.status_code == 302
    assert FeedbackCycle.objects.filter(project=project, label="Next Week").exists()


def test_duplicate_publish_is_protected_and_does_not_overwrite_summary_or_attendance(
    client,
):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Duplicate Publish Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, summary_text="Keep original summary")
    client.force_login(facilitator)

    assert client.post(publish_path(project, cycle), publish_payload(member)).status_code == 302
    cycle.approved_retrospective_summary_text = "Manual post-publish edit guard"
    cycle.save(update_fields=["approved_retrospective_summary_text", "updated_at"])
    duplicate = client.post(
        publish_path(project, cycle),
        publish_payload(facilitator, member),
    )

    assert duplicate.status_code == 404
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COMPLETED
    assert cycle.approved_retrospective_summary_text == "Manual post-publish edit guard"
    assert RetrospectiveAttendance.objects.filter(cycle=cycle).count() == 1


def test_published_summary_permissions_and_no_early_approved_summary_leakage(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    inactive = create_user("inactive", is_active=False)
    other_member = create_user("other-member")
    project = create_project("Secret Summary Project")
    other_project = create_project("Other Summary Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project)
    add_membership(other_member, other_project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Secret Summary Week",
        summary_text="Secret approved summary",
    )
    topic = create_cluster(cycle, "Secret summary topic")
    create_card(cycle, member, text="Secret feedback", cluster=topic)
    create_decision(cycle, text="Secret decision", topic=topic)
    create_action_item(cycle, member, topic, description="Secret action")
    material = create_material(cycle, facilitator, pasted_transcript_text="Secret source")
    create_draft(
        material,
        summary_text="Secret draft summary",
        review_status=MeetingMaterialExtractionDraft.ReviewStatus.APPROVED,
    )

    client.force_login(member)
    board_response = client.get(board_path(project, cycle))
    assert board_response.status_code == 200
    assert "Secret approved summary" not in board_response.content.decode()
    assert client.get(summary_path(project, cycle)).status_code == 404

    client.force_login(facilitator)
    assert client.post(publish_path(project, cycle), publish_payload(member)).status_code == 302

    for viewer in [facilitator, member]:
        client.force_login(viewer)
        response = client.get(summary_path(project, cycle))
        assert response.status_code == 200
        assert "Secret approved summary" in response.content.decode()

    secrets = [
        "Secret Summary Project",
        "Secret Summary Week",
        "Secret approved summary",
        "Secret summary topic",
        "Secret feedback",
        "Secret decision",
        "Secret action",
        "Secret source",
        "Secret draft summary",
        "member",
    ]
    for user in [outsider, admin, other_member]:
        client.force_login(user)
        for path in [summary_path(project, cycle), publish_path(project, cycle)]:
            response = client.get(path)
            assert response.status_code == 404
            assert_no_secret_leak(response, secrets)

    client.force_login(inactive)
    inactive_summary = client.get(summary_path(project, cycle))
    assert inactive_summary.status_code == 302
    assert inactive_summary["Location"] == (
        f"{reverse('login')}?next={summary_path(project, cycle)}"
    )

    anonymous = client
    anonymous.logout()
    for path in [summary_path(project, cycle), publish_path(project, cycle)]:
        response = anonymous.get(path)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"


def test_summary_page_sections_are_read_only_ranked_and_hide_protected_material_data(
    client,
):
    facilitator = create_user("facilitator", first_name="Fran", last_name="Lead")
    member = create_user("member", first_name="Mira", last_name="Member")
    anonymous_author = create_user(
        "secret-author",
        first_name="Hidden",
        last_name="Author",
        email="secret-author@example.com",
    )
    project = create_project("Summary Sections Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(anonymous_author, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Summary Sections Week",
        summary_text="Approved summary line one\nApproved summary line two",
    )
    tied_early = create_cluster(
        cycle,
        "Tied early",
        discussion_status=FeedbackCluster.DiscussionStatus.SKIPPED,
    )
    winner = create_cluster(
        cycle,
        "Winner topic",
        discussion_status=FeedbackCluster.DiscussionStatus.DISCUSSED,
        discussion_notes="Winner discussion notes",
    )
    zero = create_cluster(cycle, "Zero topic")
    tied_late = create_cluster(
        cycle,
        "Tied late",
        discussion_status=FeedbackCluster.DiscussionStatus.DEFERRED,
        discussion_notes="Deferred notes",
    )
    create_vote(cycle, member, winner, 3)
    create_vote(cycle, facilitator, winner, 1)
    create_vote(cycle, member, tied_early, 0)
    create_vote(cycle, facilitator, tied_early, 1)
    create_vote(cycle, member, tied_late, 0)
    create_vote(cycle, facilitator, tied_late, 1)
    create_card(
        cycle,
        member,
        text="Visible start card",
        category=FeedbackCard.Category.START,
        cluster=winner,
    )
    create_card(
        cycle,
        anonymous_author,
        text="Anonymous stop card",
        category=FeedbackCard.Category.STOP,
        cluster=None,
        is_anonymous=True,
    )
    create_decision(cycle, text="Decision with topic", topic=winner)
    create_decision(cycle, text="Decision without topic")
    action = create_action_item(
        cycle,
        member,
        winner,
        description="Action with date",
        due_date=date(2026, 9, 30),
    )
    create_action_item(
        cycle,
        facilitator,
        tied_late,
        description="Already done action",
        status=ActionItem.Status.DONE,
    )
    material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text="Source transcript must stay hidden",
    )
    create_draft(
        material,
        summary_text="Draft payload must stay hidden",
        review_status=MeetingMaterialExtractionDraft.ReviewStatus.APPROVED,
    )
    client.force_login(facilitator)
    assert client.post(
        publish_path(project, cycle),
        publish_payload(facilitator, member),
    ).status_code == 302

    client.force_login(member)
    assert client.post(action_owner_complete_path(project, cycle, action)).status_code == 302
    response = client.get(summary_path(project, cycle))
    content = response.content.decode()
    forms = parse(response).forms

    assert response.status_code == 200
    assert all(not form["action"].startswith(f"/projects/{project.pk}/") for form in forms)
    assert response.context["active_member_count_at_publication"] == 3
    assert response.context["submitted_member_count"] == 2
    assert response.context["completed_voter_count"] == 2
    assert "<h1 id=\"summary-heading\">Summary Sections Project</h1>" in content
    assert "Summary Sections Week completed retrospective summary" in content
    assert "Approved summary line one" in content
    assert content.index("Winner topic") < content.index("Tied early")
    assert content.index("Tied early") < content.index("Tied late")
    assert content.index("Tied late") < content.index("Zero topic")
    assert "4 votes" in content
    assert "1 vote" in content
    assert "0 votes" in content
    assert "Status: Discussed" in content
    assert "Status: Skipped" in content
    assert "Status: Deferred" in content
    assert "Status: Not started" in content
    assert "Winner discussion notes" in content
    assert "No discussion notes saved." in content
    assert "Decision with topic" in content
    assert "Topic: Winner topic" in content
    assert "Decision without topic" in content
    assert "No related topic" in content
    assert "Action with date" in content
    assert "Due: Sept. 30, 2026" in content
    assert "Status: Done" in content
    assert "Already done action" in content
    assert "Fran Lead" in content
    assert "Mira Member" in content
    assert "Active project members at publication" in content
    assert "Members who submitted feedback" in content
    assert "Eligible members who completed voting" in content
    assert "Visible start card" in content
    assert "Start" in content
    assert "Contributor: Mira Member" in content
    assert "Anonymous stop card" in content
    assert "Stop" in content
    assert "Anonymous contributor" in content
    assert "Ungrouped" in content
    for hidden in [
        "secret-author",
        "Hidden Author",
        "secret-author@example.com",
        "Source transcript must stay hidden",
        "Processed Source transcript must stay hidden",
        "Draft payload must stay hidden",
        "Meeting material",
        "Approve extracted outcomes",
        "Discard extracted outcomes",
        "Retry processing",
        "Owner candidate",
        "Topic candidate",
    ]:
        assert hidden not in content


def test_summary_empty_states_for_completed_cycle(client):
    facilitator = create_user("facilitator")
    project = create_project("Empty Summary Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(
        project,
        facilitator,
        status=FeedbackCycle.Status.COMPLETED,
        summary_text="",
    )
    client.force_login(facilitator)

    response = client.get(summary_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "No approved summary saved." in content
    assert "No discussion topics saved." in content
    assert "No confirmed decisions saved." in content
    assert "No confirmed action items saved." in content
    assert "No attendees selected." in content
    assert "No original feedback cards saved." in content


def test_publish_cross_scope_ids_are_rejected_without_mutation_or_leakage(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    other_member = create_user("other-member")
    project = create_project("Cross Scope Project")
    other_project = create_project("Other Cross Scope Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(other_member, other_project)
    cycle = create_cycle(project, facilitator, label="Cross Scope Week")
    topic = create_cluster(cycle, "Current topic")
    other_cycle = create_cycle(other_project, facilitator, label="Other Cross Scope Week")
    other_topic = create_cluster(other_cycle, "Other topic")
    other_card = create_card(other_cycle, other_member, text="Other card", cluster=other_topic)
    other_decision = create_decision(other_cycle, text="Other decision", topic=other_topic)
    other_action = create_action_item(
        other_cycle,
        other_member,
        other_topic,
        description="Other action",
    )
    other_material = create_material(other_cycle, other_member, pasted_transcript_text="Other source")
    other_draft = create_draft(other_material, summary_text="Other draft")
    client.force_login(facilitator)
    secrets = [
        "Cross Scope Project",
        "Other Cross Scope Project",
        "Cross Scope Week",
        "Other Cross Scope Week",
        "Current topic",
        "Other topic",
        "Other card",
        "Other decision",
        "Other action",
        "Other source",
        "Other draft",
        "other-member",
    ]

    tampered_payloads = [
        publish_payload(member, project_id=other_project.pk),
        publish_payload(member, cycle_id=other_cycle.pk),
        publish_payload(member, topic_id=other_topic.pk),
        publish_payload(member, feedback_card_id=other_card.pk),
        publish_payload(member, decision_id=other_decision.pk),
        publish_payload(member, action_item_id=other_action.pk),
        publish_payload(member, meeting_material_id=other_material.pk),
        publish_payload(member, extraction_draft_id=other_draft.pk),
    ]

    for payload in tampered_payloads:
        response = client.post(publish_path(project, cycle), payload)
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)
        cycle.refresh_from_db()
        assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE
        assert RetrospectiveAttendance.objects.filter(cycle=cycle).count() == 0


def test_summary_does_not_add_out_of_scope_surfaces(client):
    facilitator = create_user("facilitator")
    project = create_project("Scope Guard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, status=FeedbackCycle.Status.COMPLETED)
    client.force_login(facilitator)

    content = client.get(summary_path(project, cycle)).content.decode().lower()

    for forbidden in [
        "analytics",
        "export",
        "custom retrospective template",
        "employee score",
        "survey report",
        "record meeting",
        "slack",
        "email",
        "calendar",
        "automatic publishing",
        "approval bypass",
    ]:
        assert forbidden not in content
