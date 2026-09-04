from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import Storage
from django.db import transaction
from django.utils.module_loading import import_string

from projects.models import (
    FeedbackCluster,
    FeedbackCycle,
    MeetingMaterial,
    MeetingMaterialDraftActionItem,
    MeetingMaterialDraftDecision,
    MeetingMaterialExtractionDraft,
    MeetingMaterialTranscript,
    Membership,
)


GENERIC_FAILURE_MESSAGE = "Meeting material processing failed. Check worker setup and retry."
MISCONFIGURED_FAILURE_MESSAGE = (
    "Meeting material processing is misconfigured. Check service settings and retry."
)
SOURCE_READ_FAILURE_MESSAGE = (
    "Stored meeting material could not be read. Re-upload the source or retry later."
)


@dataclass(frozen=True)
class TranscriptSource:
    source_type: str
    label: str
    content_type: str
    byte_size: int | None
    file_name: str
    storage: Storage


@dataclass(frozen=True)
class ContextMember:
    id: int
    username: str


@dataclass(frozen=True)
class ContextTopic:
    id: int
    name: str


@dataclass(frozen=True)
class ExtractionContext:
    cycle_id: int
    project_id: int
    active_members: tuple[ContextMember, ...]
    topics: tuple[ContextTopic, ...]


@dataclass(frozen=True)
class DraftDecisionSuggestion:
    text: str
    topic_candidate: str = ""
    matched_topic_id: int | None = None


@dataclass(frozen=True)
class DraftActionItemSuggestion:
    description: str
    owner_candidate: str = ""
    matched_owner_id: int | None = None
    due_date: date | None = None
    topic_candidate: str = ""
    matched_topic_id: int | None = None


@dataclass(frozen=True)
class ExtractionResult:
    summary_text: str = ""
    draft_decisions: tuple[DraftDecisionSuggestion, ...] = ()
    draft_action_items: tuple[DraftActionItemSuggestion, ...] = ()


@dataclass(frozen=True)
class ProcessingResult:
    material_id: int
    status: str
    processed: bool
    message: str = ""


class TranscriptionService(Protocol):
    def transcribe(self, source: TranscriptSource) -> str:
        """Return transcript text for one audio or video meeting material source."""


class ExtractionService(Protocol):
    def extract(self, transcript_text: str, context: ExtractionContext) -> ExtractionResult:
        """Return draft retrospective outcomes from one processed transcript."""


class LocalDeterministicTranscriptionService:
    """Stable local baseline for development and tests."""

    def transcribe(self, source: TranscriptSource) -> str:
        label = source.label or source.source_type.replace("_", " ")
        size = "unknown size" if source.byte_size is None else f"{source.byte_size} bytes"
        return f"Local transcript for {label} ({size})."


class LocalDeterministicExtractionService:
    """Extract simple, stable drafts from line-oriented transcript markers."""

    def extract(self, transcript_text: str, context: ExtractionContext) -> ExtractionResult:
        decisions = []
        actions = []
        summary = ""

        for raw_line in transcript_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            marker, _, value = line.partition(":")
            marker = marker.strip().lower()
            value = value.strip()
            if marker == "summary" and value:
                summary = value
            elif marker == "decision" and value:
                topic = _matched_topic_for(value, context)
                decisions.append(
                    DraftDecisionSuggestion(
                        text=value,
                        topic_candidate=topic.name if topic is not None else "",
                        matched_topic_id=topic.id if topic is not None else None,
                    )
                )
            elif marker == "action" and value:
                owner = _matched_owner_for(value, context)
                topic = _matched_topic_for(value, context)
                actions.append(
                    DraftActionItemSuggestion(
                        description=value,
                        owner_candidate=owner.username if owner is not None else "",
                        matched_owner_id=owner.id if owner is not None else None,
                        due_date=_first_iso_date(value),
                        topic_candidate=topic.name if topic is not None else "",
                        matched_topic_id=topic.id if topic is not None else None,
                    )
                )

        if not summary:
            compact = " ".join(transcript_text.split())
            summary = compact[:240]

        return ExtractionResult(
            summary_text=summary,
            draft_decisions=tuple(decisions),
            draft_action_items=tuple(actions),
        )


def get_transcription_service() -> TranscriptionService:
    service_path = getattr(settings, "PROJECTS_TRANSCRIPTION_SERVICE", "")
    if not service_path:
        return LocalDeterministicTranscriptionService()

    service_factory = import_string(service_path)
    service = service_factory() if isinstance(service_factory, type) else service_factory
    if not hasattr(service, "transcribe"):
        raise ImproperlyConfigured(
            "PROJECTS_TRANSCRIPTION_SERVICE must provide transcribe(source)."
        )
    return service


def get_extraction_service() -> ExtractionService:
    service_path = getattr(settings, "PROJECTS_EXTRACTION_SERVICE", "")
    if not service_path:
        return LocalDeterministicExtractionService()

    service_factory = import_string(service_path)
    service = service_factory() if isinstance(service_factory, type) else service_factory
    if not hasattr(service, "extract"):
        raise ImproperlyConfigured(
            "PROJECTS_EXTRACTION_SERVICE must provide extract(transcript_text, context)."
        )
    return service


def enqueue_meeting_material_processing(material: MeetingMaterial) -> None:
    if material.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED:
        raise ValueError("Succeeded meeting material cannot be queued for processing.")

    material.processing_status = MeetingMaterial.ProcessingStatus.QUEUED
    material.failure_message = ""
    material.save(update_fields=["processing_status", "failure_message", "updated_at"])


def retry_meeting_material_processing(material: MeetingMaterial) -> None:
    if material.processing_status != MeetingMaterial.ProcessingStatus.FAILED:
        raise ValueError("Only failed meeting material can be retried.")
    enqueue_meeting_material_processing(material)


def process_next_queued_meeting_material() -> ProcessingResult | None:
    material_id = (
        MeetingMaterial.objects.filter(
            processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
            cycle__status=FeedbackCycle.Status.RETROSPECTIVE,
            cycle__voting_status=FeedbackCycle.VotingStatus.CLOSED,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)
        .first()
    )
    if material_id is None:
        return None
    return process_meeting_material(material_id)


def process_all_queued_meeting_materials(*, limit: int | None = None) -> list[ProcessingResult]:
    results = []
    while limit is None or len(results) < limit:
        result = process_next_queued_meeting_material()
        if result is None:
            break
        results.append(result)
    return results


def process_meeting_material(
    material_id: int,
    *,
    transcription_service: TranscriptionService | None = None,
    extraction_service: ExtractionService | None = None,
) -> ProcessingResult:
    claimed = _mark_processing_if_queued(material_id)
    if claimed is None:
        status = (
            MeetingMaterial.objects.filter(pk=material_id)
            .values_list("processing_status", flat=True)
            .first()
        )
        return ProcessingResult(
            material_id=material_id,
            status=status or "missing",
            processed=False,
            message="Meeting material is not queued for processing.",
        )

    try:
        material = _scoped_material(material_id)
        transcript_text = _transcript_text_for(
            material,
            transcription_service=transcription_service,
        )
        context = _extraction_context_for(material.cycle)
        extractor = extraction_service or get_extraction_service()
        extraction_result = extractor.extract(transcript_text, context)
        _persist_success(material, transcript_text, extraction_result)
    except Exception as exc:
        failure_message = sanitize_processing_error(exc)
        MeetingMaterial.objects.filter(pk=material_id).update(
            processing_status=MeetingMaterial.ProcessingStatus.FAILED,
            failure_message=failure_message,
        )
        return ProcessingResult(
            material_id=material_id,
            status=MeetingMaterial.ProcessingStatus.FAILED,
            processed=True,
            message=failure_message,
        )

    return ProcessingResult(
        material_id=material_id,
        status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
        processed=True,
    )


def sanitize_processing_error(exc: Exception) -> str:
    if isinstance(exc, ImproperlyConfigured):
        return MISCONFIGURED_FAILURE_MESSAGE
    if isinstance(exc, (OSError, UnicodeError)):
        return SOURCE_READ_FAILURE_MESSAGE
    return GENERIC_FAILURE_MESSAGE


def _mark_processing_if_queued(material_id: int) -> MeetingMaterial | None:
    with transaction.atomic():
        material = (
            MeetingMaterial.objects.select_for_update()
            .filter(
                pk=material_id,
                processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
                cycle__status=FeedbackCycle.Status.RETROSPECTIVE,
                cycle__voting_status=FeedbackCycle.VotingStatus.CLOSED,
            )
            .first()
        )
        if material is None:
            return None
        material.processing_status = MeetingMaterial.ProcessingStatus.PROCESSING
        material.failure_message = ""
        material.save(update_fields=["processing_status", "failure_message", "updated_at"])
        return material


def _scoped_material(material_id: int) -> MeetingMaterial:
    return MeetingMaterial.objects.select_related("cycle", "cycle__project").get(
        pk=material_id,
        processing_status=MeetingMaterial.ProcessingStatus.PROCESSING,
        cycle__status=FeedbackCycle.Status.RETROSPECTIVE,
        cycle__voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )


def _transcript_text_for(
    material: MeetingMaterial,
    *,
    transcription_service: TranscriptionService | None,
) -> str:
    if material.source_type == MeetingMaterial.SourceType.PASTED_TRANSCRIPT:
        return material.pasted_transcript_text.strip()

    if material.source_type == MeetingMaterial.SourceType.TRANSCRIPT_FILE:
        return _load_transcript_file(material)

    if material.source_type in {
        MeetingMaterial.SourceType.AUDIO_UPLOAD,
        MeetingMaterial.SourceType.VIDEO_UPLOAD,
    }:
        source = TranscriptSource(
            source_type=material.source_type,
            label=material.source_label,
            content_type=material.content_type,
            byte_size=material.byte_size,
            file_name=material.source_file.name,
            storage=material.source_file.storage,
        )
        transcriber = transcription_service or get_transcription_service()
        transcript_text = transcriber.transcribe(source)
        if not transcript_text or not transcript_text.strip():
            raise ValueError("Transcription service returned an empty transcript.")
        return transcript_text.strip()

    raise ValueError("Unsupported meeting material source type.")


def _load_transcript_file(material: MeetingMaterial) -> str:
    with material.source_file.open("rb") as source:
        transcript_text = source.read().decode("utf-8-sig")
    if not transcript_text.strip():
        raise ValueError("Transcript file is empty.")
    return transcript_text.strip()


def _extraction_context_for(cycle: FeedbackCycle) -> ExtractionContext:
    members = tuple(
        ContextMember(id=user_id, username=username)
        for user_id, username in Membership.objects.filter(
            project=cycle.project,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("user__username", "user_id")
        .values_list("user_id", "user__username")
    )
    topics = tuple(
        ContextTopic(id=topic_id, name=name)
        for topic_id, name in FeedbackCluster.objects.filter(cycle=cycle)
        .order_by("created_at", "id")
        .values_list("id", "name")
    )
    return ExtractionContext(
        cycle_id=cycle.pk,
        project_id=cycle.project_id,
        active_members=members,
        topics=topics,
    )


def _persist_success(
    material: MeetingMaterial,
    transcript_text: str,
    extraction_result: ExtractionResult,
) -> None:
    with transaction.atomic():
        transcript, _created = MeetingMaterialTranscript.objects.update_or_create(
            meeting_material=material,
            defaults={
                "text": transcript_text.strip(),
                "character_count": len(transcript_text.strip()),
            },
        )
        transcript.full_clean()
        transcript.save()

        MeetingMaterialExtractionDraft.objects.filter(meeting_material=material).delete()
        draft = MeetingMaterialExtractionDraft(
            meeting_material=material,
            retrospective_summary_text=extraction_result.summary_text,
        )
        draft.full_clean()
        draft.save()

        _persist_draft_decisions(draft, extraction_result)
        _persist_draft_action_items(draft, extraction_result)

        material.processing_status = MeetingMaterial.ProcessingStatus.SUCCEEDED
        material.failure_message = ""
        material.save(update_fields=["processing_status", "failure_message", "updated_at"])


def _persist_draft_decisions(
    draft: MeetingMaterialExtractionDraft,
    extraction_result: ExtractionResult,
) -> None:
    valid_topic_ids = set(
        FeedbackCluster.objects.filter(cycle=draft.meeting_material.cycle).values_list(
            "id",
            flat=True,
        )
    )
    for suggestion in extraction_result.draft_decisions:
        matched_topic_id = (
            suggestion.matched_topic_id
            if suggestion.matched_topic_id in valid_topic_ids
            else None
        )
        draft_decision = MeetingMaterialDraftDecision(
            extraction_draft=draft,
            text=suggestion.text,
            topic_candidate=suggestion.topic_candidate,
            matched_topic_id=matched_topic_id,
        )
        draft_decision.full_clean()
        draft_decision.save()


def _persist_draft_action_items(
    draft: MeetingMaterialExtractionDraft,
    extraction_result: ExtractionResult,
) -> None:
    material = draft.meeting_material
    valid_topic_ids = set(
        FeedbackCluster.objects.filter(cycle=material.cycle).values_list("id", flat=True)
    )
    valid_owner_ids = set(
        Membership.objects.filter(
            project=material.cycle.project,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )
    for suggestion in extraction_result.draft_action_items:
        matched_topic_id = (
            suggestion.matched_topic_id
            if suggestion.matched_topic_id in valid_topic_ids
            else None
        )
        matched_owner_id = (
            suggestion.matched_owner_id
            if suggestion.matched_owner_id in valid_owner_ids
            else None
        )
        draft_action = MeetingMaterialDraftActionItem(
            extraction_draft=draft,
            description=suggestion.description,
            owner_candidate=suggestion.owner_candidate,
            matched_owner_id=matched_owner_id,
            due_date=suggestion.due_date,
            topic_candidate=suggestion.topic_candidate,
            matched_topic_id=matched_topic_id,
        )
        draft_action.full_clean()
        draft_action.save()


def _matched_owner_for(
    text: str,
    context: ExtractionContext,
) -> ContextMember | None:
    normalized = text.lower()
    for member in context.active_members:
        if member.username.lower() in normalized:
            return member
    return None


def _matched_topic_for(
    text: str,
    context: ExtractionContext,
) -> ContextTopic | None:
    normalized = text.lower()
    for topic in context.topics:
        if topic.name.lower() in normalized:
            return topic
    return None


def _first_iso_date(text: str) -> date | None:
    for token in text.replace(",", " ").split():
        try:
            return date.fromisoformat(token)
        except ValueError:
            continue
    return None

