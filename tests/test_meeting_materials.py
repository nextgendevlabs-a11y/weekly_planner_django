from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.forms import MeetingMaterialForm
from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackClusterVote,
    FeedbackCycle,
    MeetingMaterial,
    Membership,
    Project,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


class BoardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.hrefs = []

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
            self.hrefs.append(attributes["href"])


def create_user(username="member", *, is_active=True, is_staff=False, is_superuser=False):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
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
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=timezone.now(),
    )


def create_cluster(cycle, name="Release readiness", **kwargs):
    return FeedbackCluster.objects.create(cycle=cycle, name=name, **kwargs)


def create_card(cycle, author, *, text="Keep this card", cluster=None):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=FeedbackCard.Category.START,
        text=text,
        cluster=cluster,
    )


def create_vote(cycle, voter, cluster, vote_count=3):
    return FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=voter,
        cluster=cluster,
        vote_count=vote_count,
    )


def create_action_item(cycle, owner, topic, description="Keep action unchanged"):
    return ActionItem.objects.create(
        cycle=cycle,
        owner=owner,
        topic=topic,
        description=description,
    )


def create_decision(cycle, topic, text="Keep decision unchanged"):
    return RetrospectiveDecision.objects.create(
        cycle=cycle,
        topic=topic,
        text=text,
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def material_create_path(project, cycle):
    return reverse(
        "meeting_material_create",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def upload(name, content=b"meeting bytes", content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def parser_from(response):
    parser = BoardParser()
    parser.feed(response.content.decode())
    return parser


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def test_meeting_material_model_fields_constraints_and_cycle_project_relationship():
    facilitator = create_user("facilitator")
    project = create_project("Meeting Material Model Project")
    cycle = create_cycle(project, facilitator)

    material = MeetingMaterial(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        pasted_transcript_text="  We agreed to tighten release notes.  ",
    )
    material.full_clean()
    material.save()

    assert material.cycle == cycle
    assert material.cycle.project == project
    assert material.submitted_by == facilitator
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert material.created_at is not None
    assert material.updated_at is not None
    assert material.pasted_transcript_text == "We agreed to tighten release notes."
    assert material.text_character_count == len("We agreed to tighten release notes.")
    assert material.source_label == "Pasted transcript"

    for source_type in MeetingMaterial.SourceType.values:
        source = MeetingMaterial(
            cycle=cycle,
            submitted_by=facilitator,
            source_type=source_type,
            processing_status=MeetingMaterial.ProcessingStatus.PROCESSING,
        )
        if source_type == MeetingMaterial.SourceType.PASTED_TRANSCRIPT:
            source.pasted_transcript_text = "Transcript text"
        else:
            source.source_file = f"meeting_materials/{cycle.pk}/source.txt"
            source.original_filename = "source.txt"
            source.byte_size = 12
        source.full_clean()

    invalid_source = MeetingMaterial(
        cycle=cycle,
        submitted_by=facilitator,
        source_type="meeting_notes",
        processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
    )
    with pytest.raises(ValidationError) as source_error:
        invalid_source.full_clean()
    assert "source_type" in source_error.value.message_dict
    with pytest.raises(IntegrityError), transaction.atomic():
        MeetingMaterial.objects.create(
            cycle=cycle,
            submitted_by=facilitator,
            source_type="meeting_notes",
            processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
        )

    invalid_status = MeetingMaterial(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status="done-ish",
        pasted_transcript_text="Transcript text",
    )
    with pytest.raises(ValidationError) as status_error:
        invalid_status.full_clean()
    assert "processing_status" in status_error.value.message_dict
    with pytest.raises(IntegrityError), transaction.atomic():
        MeetingMaterial.objects.create(
            cycle=cycle,
            submitted_by=facilitator,
            source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
            processing_status="done-ish",
            pasted_transcript_text="Transcript text",
        )


@pytest.mark.parametrize(
    ("field_name", "filename", "content_type", "source_type"),
    [
        ("audio_file", "retro-audio.mp3", "audio/mpeg", "audio_upload"),
        ("video_file", "retro-video.mp4", "video/mp4", "video_upload"),
        ("transcript_file", "retro-transcript.vtt", "text/vtt", "transcript_file"),
    ],
)
def test_facilitator_can_create_meeting_material_from_each_upload_source(
    client,
    tmp_path,
    field_name,
    filename,
    content_type,
    source_type,
):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Upload Source Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    file_content = b"stored source bytes"
    client.force_login(facilitator)

    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(
            material_create_path(project, cycle),
            {field_name: upload(filename, file_content, content_type)},
        )

        assert response.status_code == 302
        assert response["Location"] == board_path(project, cycle)
        material = MeetingMaterial.objects.get()
        assert material.cycle == cycle
        assert material.submitted_by == facilitator
        assert material.source_type == source_type
        assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
        assert material.source_file.name.startswith(f"meeting_materials/{cycle.pk}/")
        assert material.source_file.storage.exists(material.source_file.name)
        assert material.original_filename == filename
        assert material.content_type == content_type
        assert material.byte_size == len(file_content)
        assert material.pasted_transcript_text == ""
        assert material.text_character_count == 0


def test_facilitator_can_create_meeting_material_from_pasted_transcript(client):
    facilitator = create_user("facilitator")
    project = create_project("Pasted Source Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    client.force_login(facilitator)

    response = client.post(
        material_create_path(project, cycle),
        {"pasted_transcript": "  Decision: keep Friday demos.  "},
    )

    assert response.status_code == 302
    material = MeetingMaterial.objects.get()
    assert material.source_type == MeetingMaterial.SourceType.PASTED_TRANSCRIPT
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert material.source_file.name == ""
    assert material.original_filename == ""
    assert material.content_type == ""
    assert material.byte_size is None
    assert material.pasted_transcript_text == "Decision: keep Friday demos."
    assert material.text_character_count == 28


def test_meeting_material_form_rejects_none_whitespace_multiple_bad_type_and_limits(
    client,
    tmp_path,
):
    facilitator = create_user("facilitator")
    project = create_project("Validation Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    client.force_login(facilitator)
    path = material_create_path(project, cycle)

    responses = [
        client.post(path, {}),
        client.post(path, {"pasted_transcript": "   \n\t  "}),
        client.post(
            path,
            {
                "audio_file": upload("retro.mp3", b"audio", "audio/mpeg"),
                "pasted_transcript": "Transcript too",
            },
        ),
        client.post(
            path,
            {"audio_file": upload("slides.pdf", b"%PDF", "application/pdf")},
        ),
        client.post(
            path,
            {"video_file": upload("notes.txt", b"plain", "text/plain")},
        ),
        client.post(
            path,
            {
                "transcript_file": upload(
                    "recording.mp4",
                    b"video",
                    "video/mp4",
                )
            },
        ),
        client.post(
            path,
            {
                "source_type": "meeting_notes",
                "pasted_transcript": "Tampered source type",
            },
        ),
        client.post(
            path,
            {
                "processing_status": "complete",
                "pasted_transcript": "Tampered status",
            },
        ),
    ]

    with override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=3, MEDIA_ROOT=tmp_path):
        file_limit_response = client.post(
            path,
            {"audio_file": upload("oversize.mp3", b"1234", "audio/mpeg")},
        )
    with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=5):
        text_limit_response = client.post(path, {"pasted_transcript": "123456"})

    for response in [*responses, file_limit_response, text_limit_response]:
        assert response.status_code == 200

    content = "\n".join(response.content.decode() for response in responses)
    assert "Add one audio file, video file, transcript file, or pasted transcript." in content
    assert "Submit exactly one meeting material source at a time." in content
    assert "Choose a clearly supported audio file." in content
    assert "Choose a clearly supported video file." in content
    assert "Choose a clearly supported transcript file." in content
    assert "Choose a valid meeting material source type." in content
    assert "Choose a valid processing status." in content
    assert "Uploaded files must be 3 bytes or smaller." in (
        file_limit_response.content.decode()
    )
    assert (
        "Pasted transcript must be 5 bytes or smaller."
        in text_limit_response.content.decode()
        or "Pasted transcript exceeds the configured upload limit."
        in text_limit_response.content.decode()
    )
    assert MeetingMaterial.objects.count() == 0


def test_meeting_material_board_lists_only_requested_cycle_and_all_status_labels(client):
    facilitator = create_user("facilitator")
    other_facilitator = create_user("other-facilitator")
    project = create_project("Visible Material Project")
    other_project = create_project("Other Material Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(other_facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Visible Week")
    other_cycle = create_cycle(other_project, other_facilitator, label="Other Week")
    statuses = [
        MeetingMaterial.ProcessingStatus.QUEUED,
        MeetingMaterial.ProcessingStatus.PROCESSING,
        MeetingMaterial.ProcessingStatus.SUCCEEDED,
        MeetingMaterial.ProcessingStatus.FAILED,
    ]
    for status in statuses:
        MeetingMaterial.objects.create(
            cycle=cycle,
            submitted_by=facilitator,
            source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
            processing_status=status,
            pasted_transcript_text=f"{status} transcript body",
            text_character_count=len(f"{status} transcript body"),
            failure_message="Visible failure detail" if status == "failed" else "",
        )
    MeetingMaterial.objects.create(
        cycle=other_cycle,
        submitted_by=other_facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        pasted_transcript_text="Hidden other transcript",
        text_character_count=23,
        failure_message="Hidden other failure",
    )
    client.force_login(facilitator)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Meeting material" in content
    assert "Pasted transcript" in content
    assert "Submitted by: facilitator" in content
    assert "Submitted:" in content
    assert "Status: Queued" in content
    assert "Status: Processing" in content
    assert "Status: Succeeded" in content
    assert "Status: Failed" in content
    assert "Failure: Visible failure detail" in content
    assert "Hidden other transcript" not in content
    assert "Hidden other failure" not in content
    assert "Other Week" not in content


def test_facilitator_full_source_visibility_and_member_read_only_summary(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Source Visibility Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    file_material = MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.AUDIO_UPLOAD,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        source_file=f"meeting_materials/{cycle.pk}/secret-audio.mp3",
        original_filename="secret-audio.mp3",
        content_type="audio/mpeg",
        byte_size=123,
        failure_message="Transcript provider unavailable",
    )
    MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        pasted_transcript_text="Secret pasted transcript body",
        text_character_count=29,
    )

    client.force_login(facilitator)
    facilitator_response = client.get(board_path(project, cycle))
    facilitator_content = facilitator_response.content.decode()
    facilitator_parser = parser_from(facilitator_response)
    assert facilitator_response.status_code == 200
    assert {"action": material_create_path(project, cycle), "method": "post"} in (
        facilitator_parser.forms
    )
    assert "Submit meeting material" in facilitator_content
    assert file_material.source_file.url in facilitator_parser.hrefs
    assert "secret-audio.mp3" in facilitator_content
    assert "audio/mpeg" in facilitator_content
    assert "123 bytes" in facilitator_content
    assert "Secret pasted transcript body" in facilitator_content
    assert "29 characters pasted." in facilitator_content
    assert "Failure: Transcript provider unavailable" in facilitator_content

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    member_parser = parser_from(member_response)
    assert member_response.status_code == 200
    assert "Meeting material" in member_content
    assert "Audio upload" in member_content
    assert "Pasted transcript" in member_content
    assert "secret-audio.mp3" in member_content
    assert "Submitted by: facilitator" in member_content
    assert "Status: Failed" in member_content
    assert "Failure: Transcript provider unavailable" in member_content
    assert "Submit meeting material" not in member_content
    assert material_create_path(project, cycle) not in [
        form["action"] for form in member_parser.forms
    ]
    assert file_material.source_file.url not in member_parser.hrefs
    assert file_material.source_file.url not in member_content
    assert "Secret pasted transcript body" not in member_content
    assert "audio/mpeg" not in member_content
    assert "123 bytes" not in member_content


def test_members_and_protected_users_cannot_create_or_view_without_leakage(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    inactive = create_user("inactive", is_active=False)
    project = create_project("Secret Material Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Secret Material Week")
    topic = create_cluster(cycle, "Secret material topic", discussion_notes="Secret notes")
    card = create_card(cycle, member, text="Secret feedback card", cluster=topic)
    vote = create_vote(cycle, member, topic)
    action = create_action_item(cycle, member, topic, "Secret action item")
    decision = create_decision(cycle, topic, "Secret decision")
    MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.AUDIO_UPLOAD,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        source_file=f"meeting_materials/{cycle.pk}/secret-recording.mp3",
        original_filename="secret-recording.mp3",
        content_type="audio/mpeg",
        byte_size=321,
        failure_message="Secret failure message",
    )
    secrets = [
        "Secret Material Project",
        "Secret Material Week",
        "Secret material topic",
        "Secret notes",
        "Secret feedback card",
        "3 votes",
        "Secret action item",
        "Secret decision",
        "secret-recording.mp3",
        "audio/mpeg",
        "/media/meeting_materials",
        "facilitator",
        "Secret failure message",
    ]
    path = material_create_path(project, cycle)

    response = client.post(path)
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"

    client.force_login(member)
    member_response = client.post(
        path,
        {"pasted_transcript": "Member cannot submit"},
    )
    assert member_response.status_code == 404
    assert_no_secret_leak(member_response, secrets)

    for user in [outsider, admin]:
        client.force_login(user)
        view_response = client.get(board_path(project, cycle))
        create_response = client.post(
            path,
            {"pasted_transcript": "Unauthorized transcript"},
        )
        for response in [view_response, create_response]:
            assert response.status_code == 404
            assert_no_secret_leak(response, secrets)

    client.force_login(inactive)
    inactive_view_response = client.get(board_path(project, cycle))
    inactive_create_response = client.post(path, {"pasted_transcript": "Inactive"})
    assert inactive_view_response.status_code == 302
    assert inactive_view_response["Location"] == (
        f"{reverse('login')}?next={board_path(project, cycle)}"
    )
    assert inactive_create_response.status_code == 302
    assert inactive_create_response["Location"] == f"{reverse('login')}?next={path}"

    assert MeetingMaterial.objects.count() == 1
    card.refresh_from_db()
    vote.refresh_from_db()
    action.refresh_from_db()
    decision.refresh_from_db()
    cycle.refresh_from_db()
    assert card.text == "Secret feedback card"
    assert vote.vote_count == 3
    assert action.description == "Secret action item"
    assert decision.text == "Secret decision"
    assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED


@pytest.mark.parametrize(
    ("status", "voting_status"),
    [
        (FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.VotingStatus.CLOSED),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.CLUSTERING),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.OPEN),
        (FeedbackCycle.Status.COMPLETED, FeedbackCycle.VotingStatus.CLOSED),
    ],
)
def test_meeting_material_is_gated_to_closed_voting_retrospective_cycles(
    client,
    status,
    voting_status,
):
    facilitator = create_user(f"facilitator-{status}-{voting_status}")
    project = create_project(f"Gated Material {status} {voting_status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, status=status, voting_status=voting_status)
    existing = MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
        pasted_transcript_text="Hidden gated transcript",
        text_character_count=23,
    )
    client.force_login(facilitator)

    board_response = client.get(board_path(project, cycle))
    create_response = client.post(
        material_create_path(project, cycle),
        {"pasted_transcript": "Late meeting material"},
    )

    if status == FeedbackCycle.Status.RETROSPECTIVE:
        assert board_response.status_code == 200
        content = board_response.content.decode()
        assert "Meeting material" not in content
        assert "Hidden gated transcript" not in content
    else:
        assert board_response.status_code == 404
    assert create_response.status_code == 404
    existing.refresh_from_db()
    assert existing.pasted_transcript_text == "Hidden gated transcript"
    assert MeetingMaterial.objects.filter(
        pasted_transcript_text="Late meeting material"
    ).exists() is False


def test_cross_project_and_cross_cycle_meeting_material_tampering_is_rejected(client):
    facilitator = create_user("facilitator")
    project = create_project("Secret Tamper Material Project")
    other_project = create_project("Other Tamper Material Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Secret Tamper Material Week")
    other_cycle = create_cycle(other_project, facilitator, label="Other Tamper Week")
    MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        pasted_transcript_text="Secret tamper transcript",
        text_character_count=24,
    )
    MeetingMaterial.objects.create(
        cycle=other_cycle,
        submitted_by=facilitator,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        pasted_transcript_text="Other tamper transcript",
        text_character_count=23,
    )
    secrets = [
        "Secret Tamper Material Project",
        "Secret Tamper Material Week",
        "Secret tamper transcript",
        "Other Tamper Material Project",
        "Other Tamper Week",
        "Other tamper transcript",
    ]
    client.force_login(facilitator)

    responses = [
        client.get(board_path(project, other_cycle)),
        client.get(board_path(other_project, cycle)),
        client.post(
            material_create_path(project, other_cycle),
            {"pasted_transcript": "Wrong cycle write"},
        ),
        client.post(
            material_create_path(other_project, cycle),
            {"pasted_transcript": "Wrong project write"},
        ),
    ]

    for response in responses:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)
    assert MeetingMaterial.objects.count() == 2
    assert MeetingMaterial.objects.filter(
        pasted_transcript_text__in=["Wrong cycle write", "Wrong project write"]
    ).exists() is False


def test_meeting_material_create_is_post_only_and_csrf_protected(client):
    facilitator = create_user("facilitator")
    project = create_project("Post Only Material Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    path = material_create_path(project, cycle)
    client.force_login(facilitator)

    get_response = client.get(path)
    assert get_response.status_code == 405

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(facilitator)
    post_response = csrf_client.post(path, {"pasted_transcript": "Blocked by CSRF"})

    assert post_response.status_code == 403
    assert MeetingMaterial.objects.count() == 0


def test_creating_meeting_material_does_not_mutate_unrelated_retrospective_data(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("No Mutation Material Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    membership = add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Keep topic", discussion_notes="Keep notes")
    card = create_card(cycle, member, text="Keep card", cluster=topic)
    vote = create_vote(cycle, member, topic)
    action = create_action_item(cycle, member, topic)
    decision = create_decision(cycle, topic)
    original = {
        "cycle_status": cycle.status,
        "cycle_voting_status": cycle.voting_status,
        "topic_name": topic.name,
        "topic_status": topic.discussion_status,
        "topic_notes": topic.discussion_notes,
        "card_text": card.text,
        "card_cluster_id": card.cluster_id,
        "vote_count": vote.vote_count,
        "action_description": action.description,
        "decision_text": decision.text,
        "membership_role": membership.role,
    }
    client.force_login(facilitator)

    response = client.post(
        material_create_path(project, cycle),
        {"pasted_transcript": "Meeting material only"},
    )

    assert response.status_code == 302
    assert MeetingMaterial.objects.count() == 1
    cycle.refresh_from_db()
    topic.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    action.refresh_from_db()
    decision.refresh_from_db()
    membership.refresh_from_db()
    assert cycle.status == original["cycle_status"]
    assert cycle.voting_status == original["cycle_voting_status"]
    assert topic.name == original["topic_name"]
    assert topic.discussion_status == original["topic_status"]
    assert topic.discussion_notes == original["topic_notes"]
    assert card.text == original["card_text"]
    assert card.cluster_id == original["card_cluster_id"]
    assert vote.vote_count == original["vote_count"]
    assert action.description == original["action_description"]
    assert decision.text == original["decision_text"]
    assert membership.role == original["membership_role"]


def test_form_accepts_common_extensions_without_provider_processing():
    facilitator = create_user("facilitator")
    project = create_project("Extension Validation Project")
    cycle = create_cycle(project, facilitator)

    audio_form = MeetingMaterialForm(
        {},
        {"audio_file": upload("voice-note.wav", b"audio", "")},
        cycle=cycle,
        submitter=facilitator,
    )
    video_form = MeetingMaterialForm(
        {},
        {"video_file": upload("meeting-recording.mov", b"video", "")},
        cycle=cycle,
        submitter=facilitator,
    )
    transcript_form = MeetingMaterialForm(
        {},
        {"transcript_file": upload("meeting-notes.srt", b"text", "")},
        cycle=cycle,
        submitter=facilitator,
    )

    assert audio_form.is_valid() is True
    assert video_form.is_valid() is True
    assert transcript_form.is_valid() is True
    assert MeetingMaterial.objects.count() == 0
