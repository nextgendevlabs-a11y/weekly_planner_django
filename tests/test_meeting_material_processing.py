from datetime import date
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.meeting_processing import (
    DraftActionItemSuggestion,
    DraftDecisionSuggestion,
    ExtractionResult,
    MISCONFIGURED_FAILURE_MESSAGE,
    SOURCE_READ_FAILURE_MESSAGE,
    enqueue_meeting_material_processing,
    process_all_queued_meeting_materials,
    process_meeting_material,
    retry_meeting_material_processing,
)
from projects.models import (
    ActionItem,
    FeedbackCluster,
    FeedbackCycle,
    MeetingMaterial,
    MeetingMaterialDraftActionItem,
    MeetingMaterialDraftDecision,
    MeetingMaterialExtractionDraft,
    MeetingMaterialTranscript,
    Membership,
    Project,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


class RecordingTranscriptionService:
    def __init__(self, transcript_text="Transcribed provider text"):
        self.transcript_text = transcript_text
        self.calls = []
        self.raises = None

    def transcribe(self, source):
        self.calls.append(source)
        if self.raises is not None:
            raise self.raises
        return self.transcript_text


class RecordingExtractionService:
    def __init__(self, result=None, *, raises=None, observed_material_id=None):
        self.result = result or ExtractionResult(summary_text="Stable draft summary")
        self.raises = raises
        self.observed_material_id = observed_material_id
        self.status_seen = None
        self.calls = []

    def extract(self, transcript_text, context):
        self.calls.append((transcript_text, context))
        if self.observed_material_id is not None:
            self.status_seen = MeetingMaterial.objects.get(
                pk=self.observed_material_id
            ).processing_status
        if self.raises is not None:
            raise self.raises
        return self.result


class BadSettingsExtractionService:
    pass


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


def create_cluster(cycle, name="Release readiness"):
    return FeedbackCluster.objects.create(cycle=cycle, name=name)


def create_material(
    cycle,
    submitter,
    *,
    source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
    processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
    pasted_transcript_text="Decision: keep Friday demos",
):
    return MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=submitter,
        source_type=source_type,
        processing_status=processing_status,
        pasted_transcript_text=pasted_transcript_text,
        text_character_count=len(pasted_transcript_text),
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


def material_retry_path(project, cycle, material):
    return reverse(
        "meeting_material_retry",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "meeting_material_id": material.pk,
        },
    )


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def test_submission_schedules_background_attempt_without_inline_processing(
    client,
    monkeypatch,
):
    facilitator = create_user("facilitator")
    project = create_project("Background Submit Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    enqueued_ids = []

    def fake_enqueue(material):
        enqueued_ids.append(material.pk)

    monkeypatch.setattr("weekly_planner.views.enqueue_meeting_material_processing", fake_enqueue)
    client.force_login(facilitator)

    response = client.post(
        material_create_path(project, cycle),
        {"pasted_transcript": "  Decision: keep Friday demos.  "},
    )

    assert response.status_code == 302
    material = MeetingMaterial.objects.get()
    assert enqueued_ids == [material.pk]
    assert material.cycle == cycle
    assert material.pasted_transcript_text == "Decision: keep Friday demos."
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert MeetingMaterialTranscript.objects.count() == 0
    assert MeetingMaterialExtractionDraft.objects.count() == 0
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_enqueue_failure_marks_target_record_failed_with_sanitized_message(
    client,
    monkeypatch,
):
    facilitator = create_user("facilitator")
    project = create_project("Queue Failure Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)

    def fail_enqueue(material):
        raise RuntimeError("token=abc123 at C:\\secret\\provider.log")

    monkeypatch.setattr("weekly_planner.views.enqueue_meeting_material_processing", fail_enqueue)
    client.force_login(facilitator)

    response = client.post(
        material_create_path(project, cycle),
        {"pasted_transcript": "Decision: keep Friday demos."},
    )

    assert response.status_code == 302
    material = MeetingMaterial.objects.get()
    assert material.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert material.failure_message == (
        "Meeting material processing failed. Check worker setup and retry."
    )

    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert material.failure_message in content
    assert "abc123" not in content
    assert "C:\\secret" not in content


def test_pasted_transcript_processing_persists_transcript_and_extraction_drafts():
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Pasted Processing Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Release readiness")
    material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text=(
            "Summary: release was smoother.\n"
            "Decision: keep Release readiness reviews.\n"
            "Action: owner updates Release readiness checklist by 2026-09-30."
        ),
    )
    transcriber = RecordingTranscriptionService()
    extractor = RecordingExtractionService(
        ExtractionResult(
            summary_text="Stable summary",
            draft_decisions=(
                DraftDecisionSuggestion(
                    text="Keep release readiness reviews",
                    topic_candidate="Release readiness",
                    matched_topic_id=topic.pk,
                ),
            ),
            draft_action_items=(
                DraftActionItemSuggestion(
                    description="Owner updates the checklist",
                    owner_candidate="owner",
                    matched_owner_id=owner.pk,
                    due_date=date(2026, 9, 30),
                    topic_candidate="Release readiness",
                    matched_topic_id=topic.pk,
                ),
            ),
        ),
        observed_material_id=material.pk,
    )

    result = process_meeting_material(
        material.pk,
        transcription_service=transcriber,
        extraction_service=extractor,
    )

    material.refresh_from_db()
    assert result.processed is True
    assert result.status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert material.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert material.failure_message == ""
    assert extractor.status_seen == MeetingMaterial.ProcessingStatus.PROCESSING
    assert transcriber.calls == []
    assert extractor.calls[0][0] == material.pasted_transcript_text
    context = extractor.calls[0][1]
    assert context.cycle_id == cycle.pk
    assert context.project_id == project.pk
    assert [member.username for member in context.active_members] == [
        "facilitator",
        "owner",
    ]
    assert [context_topic.name for context_topic in context.topics] == [
        "Release readiness"
    ]

    transcript = material.processed_transcript
    assert transcript.text == material.pasted_transcript_text
    assert transcript.character_count == len(material.pasted_transcript_text)
    draft = material.extraction_draft
    assert draft.retrospective_summary_text == "Stable summary"
    decision = MeetingMaterialDraftDecision.objects.get()
    assert decision.text == "Keep release readiness reviews"
    assert decision.topic_candidate == "Release readiness"
    assert decision.matched_topic == topic
    action = MeetingMaterialDraftActionItem.objects.get()
    assert action.description == "Owner updates the checklist"
    assert action.owner_candidate == "owner"
    assert action.matched_owner == owner
    assert action.due_date == date(2026, 9, 30)
    assert action.topic_candidate == "Release readiness"
    assert action.matched_topic == topic
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_transcript_file_processing_loads_file_without_transcription_service(tmp_path):
    facilitator = create_user("facilitator")
    project = create_project("Transcript File Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    transcriber = RecordingTranscriptionService()
    extractor = RecordingExtractionService()

    with override_settings(MEDIA_ROOT=tmp_path):
        material = MeetingMaterial(
            cycle=cycle,
            submitted_by=facilitator,
            source_type=MeetingMaterial.SourceType.TRANSCRIPT_FILE,
            processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
            source_file=SimpleUploadedFile(
                "retro.txt",
                b"Decision: use the rollout checklist.",
                content_type="text/plain",
            ),
            original_filename="retro.txt",
            content_type="text/plain",
            byte_size=len(b"Decision: use the rollout checklist."),
        )
        material.full_clean()
        material.save()

        result = process_meeting_material(
            material.pk,
            transcription_service=transcriber,
            extraction_service=extractor,
        )

    material.refresh_from_db()
    assert result.status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert transcriber.calls == []
    assert extractor.calls[0][0] == "Decision: use the rollout checklist."
    assert material.processed_transcript.text == "Decision: use the rollout checklist."


def test_audio_and_video_processing_use_replaceable_transcription_service(tmp_path):
    facilitator = create_user("facilitator")
    project = create_project("Provider Interface Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    extractor = RecordingExtractionService()

    with override_settings(MEDIA_ROOT=tmp_path):
        for source_type, filename, content_type in [
            (MeetingMaterial.SourceType.AUDIO_UPLOAD, "retro.mp3", "audio/mpeg"),
            (MeetingMaterial.SourceType.VIDEO_UPLOAD, "retro.mp4", "video/mp4"),
        ]:
            transcriber = RecordingTranscriptionService(f"Transcript for {filename}")
            material = MeetingMaterial(
                cycle=cycle,
                submitted_by=facilitator,
                source_type=source_type,
                processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
                source_file=SimpleUploadedFile(filename, b"media", content_type=content_type),
                original_filename=filename,
                content_type=content_type,
                byte_size=5,
            )
            material.full_clean()
            material.save()

            result = process_meeting_material(
                material.pk,
                transcription_service=transcriber,
                extraction_service=extractor,
            )

            material.refresh_from_db()
            assert result.status == MeetingMaterial.ProcessingStatus.SUCCEEDED
            assert len(transcriber.calls) == 1
            assert transcriber.calls[0].label == filename
            assert transcriber.calls[0].content_type == content_type
            assert material.processed_transcript.text == f"Transcript for {filename}"


def test_local_worker_command_processes_only_eligible_queued_records():
    facilitator = create_user("facilitator")
    project = create_project("Worker Project")
    other_project = create_project("Worker Gated Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    closed_cycle = create_cycle(project, facilitator, label="Closed")
    open_cycle = create_cycle(
        other_project,
        facilitator,
        label="Open Voting",
        voting_status=FeedbackCycle.VotingStatus.OPEN,
    )
    eligible = create_material(
        closed_cycle,
        facilitator,
        pasted_transcript_text="Summary: stable local worker output.",
    )
    gated = create_material(
        open_cycle,
        facilitator,
        pasted_transcript_text="Summary: should stay queued.",
    )
    output = StringIO()

    call_command("process_meeting_materials", stdout=output)

    eligible.refresh_from_db()
    gated.refresh_from_db()
    assert f"MeetingMaterial {eligible.pk}: succeeded" in output.getvalue()
    assert eligible.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert eligible.processed_transcript.text == "Summary: stable local worker output."
    assert gated.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert not MeetingMaterialTranscript.objects.filter(meeting_material=gated).exists()


def test_processing_failure_retry_and_idempotence_do_not_duplicate_drafts():
    facilitator = create_user("facilitator")
    project = create_project("Retry Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    material = create_material(cycle, facilitator, pasted_transcript_text="Decision: retry")
    failing_extractor = RecordingExtractionService(
        raises=RuntimeError("provider traceback token=secret")
    )

    failed = process_meeting_material(material.pk, extraction_service=failing_extractor)

    material.refresh_from_db()
    assert failed.status == MeetingMaterial.ProcessingStatus.FAILED
    assert material.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert "secret" not in material.failure_message
    assert MeetingMaterialTranscript.objects.count() == 0
    assert MeetingMaterialExtractionDraft.objects.count() == 0

    retry_meeting_material_processing(material)
    material.refresh_from_db()
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert material.failure_message == ""

    successful = process_meeting_material(
        material.pk,
        extraction_service=RecordingExtractionService(
            ExtractionResult(
                summary_text="Retry succeeded",
                draft_decisions=(DraftDecisionSuggestion(text="Retry decision"),),
            )
        ),
    )
    second = process_meeting_material(
        material.pk,
        extraction_service=RecordingExtractionService(
            ExtractionResult(summary_text="Should not append"),
        ),
    )

    material.refresh_from_db()
    assert successful.status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert second.processed is False
    assert second.status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert material.failure_message == ""
    assert MeetingMaterial.objects.count() == 1
    assert MeetingMaterialTranscript.objects.count() == 1
    assert MeetingMaterialExtractionDraft.objects.count() == 1
    assert MeetingMaterialDraftDecision.objects.count() == 1
    assert MeetingMaterialDraftActionItem.objects.count() == 0
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_duplicate_enqueue_keeps_one_record_queued_without_side_effects():
    facilitator = create_user("facilitator")
    project = create_project("Duplicate Enqueue Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    material = create_material(cycle, facilitator)

    enqueue_meeting_material_processing(material)
    enqueue_meeting_material_processing(material)

    material.refresh_from_db()
    assert MeetingMaterial.objects.count() == 1
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert material.failure_message == ""
    assert MeetingMaterialTranscript.objects.count() == 0
    assert MeetingMaterialExtractionDraft.objects.count() == 0


def test_transcription_loading_and_persistence_failures_mark_target_failed(tmp_path):
    facilitator = create_user("facilitator")
    project = create_project("Failure Source Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    other_material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text="Other material should stay queued",
    )

    with override_settings(MEDIA_ROOT=tmp_path):
        audio = MeetingMaterial(
            cycle=cycle,
            submitted_by=facilitator,
            source_type=MeetingMaterial.SourceType.AUDIO_UPLOAD,
            processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
            source_file=SimpleUploadedFile("retro.mp3", b"media", content_type="audio/mpeg"),
            original_filename="retro.mp3",
            content_type="audio/mpeg",
            byte_size=5,
        )
        audio.full_clean()
        audio.save()
        transcriber = RecordingTranscriptionService()
        transcriber.raises = RuntimeError("provider token=secret")

        transcription_result = process_meeting_material(
            audio.pk,
            transcription_service=transcriber,
            extraction_service=RecordingExtractionService(),
        )

        missing_file = MeetingMaterial.objects.create(
            cycle=cycle,
            submitted_by=facilitator,
            source_type=MeetingMaterial.SourceType.TRANSCRIPT_FILE,
            processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
            source_file="meeting_materials/missing/transcript.txt",
            original_filename="transcript.txt",
            content_type="text/plain",
            byte_size=12,
        )
        loading_result = process_meeting_material(
            missing_file.pk,
            extraction_service=RecordingExtractionService(),
        )

    persistence_material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text="Decision: blank persistence draft",
    )
    persistence_result = process_meeting_material(
        persistence_material.pk,
        extraction_service=RecordingExtractionService(
            ExtractionResult(
                summary_text="Persistence should fail",
                draft_decisions=(DraftDecisionSuggestion(text="   "),),
            )
        ),
    )

    audio.refresh_from_db()
    missing_file.refresh_from_db()
    persistence_material.refresh_from_db()
    other_material.refresh_from_db()
    assert transcription_result.status == MeetingMaterial.ProcessingStatus.FAILED
    assert audio.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert "secret" not in audio.failure_message
    assert loading_result.status == MeetingMaterial.ProcessingStatus.FAILED
    assert missing_file.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert missing_file.failure_message == SOURCE_READ_FAILURE_MESSAGE
    assert persistence_result.status == MeetingMaterial.ProcessingStatus.FAILED
    assert persistence_material.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert MeetingMaterialTranscript.objects.filter(
        meeting_material=persistence_material
    ).count() == 0
    assert not MeetingMaterialExtractionDraft.objects.filter(
        meeting_material=persistence_material
    ).exists()
    assert other_material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_misconfigured_services_fail_target_record_without_raw_content_leakage():
    facilitator = create_user("facilitator")
    project = create_project("Misconfigured Service Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text="Protected transcript body",
    )

    with override_settings(
        PROJECTS_EXTRACTION_SERVICE=(
            "tests.test_meeting_material_processing.BadSettingsExtractionService"
        )
    ):
        result = process_meeting_material(material.pk)

    material.refresh_from_db()
    assert result.status == MeetingMaterial.ProcessingStatus.FAILED
    assert material.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert material.failure_message == MISCONFIGURED_FAILURE_MESSAGE
    assert "Protected transcript body" not in material.failure_message


def test_facilitator_sees_processed_details_and_member_sees_only_status_summary(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project = create_project("Visibility Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Visibility topic")
    material = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
        pasted_transcript_text="Secret pasted source body",
    )
    MeetingMaterialTranscript.objects.create(
        meeting_material=material,
        text="Secret processed transcript",
        character_count=len("Secret processed transcript"),
    )
    draft = MeetingMaterialExtractionDraft.objects.create(
        meeting_material=material,
        retrospective_summary_text="Secret draft summary",
    )
    MeetingMaterialDraftDecision.objects.create(
        extraction_draft=draft,
        text="Secret draft decision",
        matched_topic=topic,
    )
    MeetingMaterialDraftActionItem.objects.create(
        extraction_draft=draft,
        description="Secret draft action",
        owner_candidate="member",
        matched_owner=member,
        matched_topic=topic,
    )
    failed = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        pasted_transcript_text="Secret failed source",
    )
    failed.failure_message = "Retryable failure"
    failed.save(update_fields=["failure_message", "updated_at"])

    client.force_login(facilitator)
    facilitator_response = client.get(board_path(project, cycle))
    facilitator_content = facilitator_response.content.decode()
    assert facilitator_response.status_code == 200
    assert "Secret processed transcript" in facilitator_content
    assert "Secret draft summary" in facilitator_content
    assert "Secret draft decision" in facilitator_content
    assert "Secret draft action" in facilitator_content
    assert material_retry_path(project, cycle, failed) in facilitator_content

    secrets = [
        "Visibility Project",
        "Secret pasted source body",
        "Secret processed transcript",
        "Secret draft summary",
        "Secret draft decision",
        "Secret draft action",
        "Visibility topic",
    ]
    for user in [create_user("outsider"), create_user("admin", is_staff=True, is_superuser=True)]:
        client.force_login(user)
        response = client.get(board_path(project, cycle))
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    assert member_response.status_code == 200
    assert "Meeting material" in member_content
    assert "Status: Succeeded" in member_content
    assert "Status: Failed" in member_content
    assert "Secret pasted source body" not in member_content
    assert "Secret processed transcript" not in member_content
    assert "Secret draft summary" not in member_content
    assert "Secret draft decision" not in member_content
    assert "Secret draft action" not in member_content
    assert material_retry_path(project, cycle, failed) not in member_content


def test_retry_route_is_facilitator_only_post_only_csrf_protected_and_gated(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    project = create_project("Retry Route Project")
    other_project = create_project("Other Retry Route Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Retry Week")
    other_cycle = create_cycle(other_project, facilitator, label="Other Retry Week")
    failed = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        pasted_transcript_text="Secret failed transcript",
    )
    failed.failure_message = "Secret failure detail"
    failed.save(update_fields=["failure_message", "updated_at"])
    succeeded = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
        pasted_transcript_text="Secret succeeded transcript",
    )
    other_material = create_material(
        other_cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        pasted_transcript_text="Secret other transcript",
    )
    path = material_retry_path(project, cycle, failed)
    secrets = [
        "Retry Route Project",
        "Other Retry Route Project",
        "Retry Week",
        "Other Retry Week",
        "Secret failed transcript",
        "Secret succeeded transcript",
        "Secret other transcript",
        "Secret failure detail",
    ]

    response = client.post(path)
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"

    client.force_login(facilitator)
    get_response = client.get(path)
    assert get_response.status_code == 405
    succeeded_response = client.post(material_retry_path(project, cycle, succeeded))
    cross_cycle_response = client.post(material_retry_path(project, other_cycle, failed))
    cross_project_response = client.post(
        material_retry_path(other_project, cycle, other_material)
    )
    for response in [succeeded_response, cross_cycle_response, cross_project_response]:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(facilitator)
    csrf_response = csrf_client.post(path)
    assert csrf_response.status_code == 403

    for user in [member, outsider, admin]:
        client.force_login(user)
        response = client.post(path)
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    client.force_login(facilitator)
    retry_response = client.post(path)
    assert retry_response.status_code == 302
    failed.refresh_from_db()
    succeeded.refresh_from_db()
    other_material.refresh_from_db()
    assert failed.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert failed.failure_message == ""
    assert succeeded.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert other_material.processing_status == MeetingMaterial.ProcessingStatus.FAILED


@pytest.mark.parametrize(
    ("status", "voting_status"),
    [
        (FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.VotingStatus.CLOSED),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.CLUSTERING),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.OPEN),
        (FeedbackCycle.Status.COMPLETED, FeedbackCycle.VotingStatus.CLOSED),
    ],
)
def test_processing_and_retry_are_gated_to_closed_voting_retrospectives(
    client,
    status,
    voting_status,
):
    facilitator = create_user(f"facilitator-{status}-{voting_status}")
    project = create_project(f"Gated Processing {status} {voting_status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, status=status, voting_status=voting_status)
    material = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
        pasted_transcript_text="Hidden gated processing transcript",
    )

    result = process_meeting_material(material.pk)
    client.force_login(facilitator)
    retry_response = client.post(material_retry_path(project, cycle, material))

    assert result.processed is False
    material.refresh_from_db()
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED
    assert retry_response.status_code == 404
    assert MeetingMaterialTranscript.objects.count() == 0
    assert MeetingMaterialExtractionDraft.objects.count() == 0


def test_bulk_worker_does_not_read_or_mutate_other_cycle_confirmed_outcomes():
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Scoped Worker Project")
    other_project = create_project("Other Scoped Worker Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Current topic")
    other_cycle = create_cycle(other_project, facilitator)
    other_topic = create_cluster(other_cycle, "Other topic")
    ActionItem.objects.create(
        cycle=other_cycle,
        owner=facilitator,
        topic=other_topic,
        description="Other project action",
    )
    RetrospectiveDecision.objects.create(
        cycle=other_cycle,
        topic=other_topic,
        text="Other project decision",
    )
    material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text=(
            "Decision: keep Current topic.\n"
            "Action: owner follows up on Current topic."
        ),
    )

    results = process_all_queued_meeting_materials()

    assert [result.material_id for result in results] == [material.pk]
    assert ActionItem.objects.filter(description="Other project action").count() == 1
    assert RetrospectiveDecision.objects.filter(text="Other project decision").count() == 1
    assert ActionItem.objects.filter(cycle=cycle).count() == 0
    assert RetrospectiveDecision.objects.filter(cycle=cycle).count() == 0
    assert material.extraction_draft.draft_decisions.count() == 1
    assert material.extraction_draft.draft_action_items.count() == 1
    assert material.extraction_draft.draft_action_items.get().matched_topic == topic
