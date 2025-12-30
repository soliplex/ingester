"""
Factory functions for creating test data with sensible defaults.

Usage:
    from tests.factories import make_document, make_batch, make_workflow_run

    # Create a document with defaults
    doc = make_document()

    # Create a document with custom values
    doc = make_document(hash="custom-hash", mime_type="text/plain")

    # Create and persist to database
    async with db.session() as session:
        batch = make_batch()
        session.add(batch)
        await session.flush()
"""

import datetime
import uuid

from soliplex.ingester.lib import models


def make_document(
    hash: str | None = None,
    mime_type: str = "application/pdf",
    file_size: int = 100,
    doc_meta: dict | None = None,
    rag_id: str | None = None,
    batch_id: int | None = None,
    **kwargs,
) -> models.Document:
    """
    Create a Document instance with sensible defaults.

    Args:
        hash: Document hash. If None, generates a unique hash.
        mime_type: MIME type of the document.
        file_size: Size of the document in bytes.
        doc_meta: Document metadata dictionary.
        rag_id: RAG system ID.
        batch_id: Associated batch ID.
        **kwargs: Additional fields to pass to Document.

    Returns:
        A Document instance (not persisted to database).
    """
    if hash is None:
        hash = f"sha256-{uuid.uuid4().hex}"
    if doc_meta is None:
        doc_meta = {}

    return models.Document(
        hash=hash,
        mime_type=mime_type,
        file_size=file_size,
        doc_meta=doc_meta,
        rag_id=rag_id,
        batch_id=batch_id,
        **kwargs,
    )


def make_batch(
    name: str = "Test Batch",
    source: str = "pytest",
    start_date: datetime.datetime | None = None,
    completed_date: datetime.datetime | None = None,
    batch_meta: dict | None = None,
    **kwargs,
) -> models.DocumentBatch:
    """
    Create a DocumentBatch instance with sensible defaults.

    Args:
        name: Batch name.
        source: Source identifier.
        start_date: When the batch started. Defaults to now.
        completed_date: When the batch completed. None if still running.
        batch_meta: Batch metadata dictionary.
        **kwargs: Additional fields to pass to DocumentBatch.

    Returns:
        A DocumentBatch instance (not persisted to database).
    """
    if start_date is None:
        start_date = datetime.datetime.now()
    if batch_meta is None:
        batch_meta = {}

    return models.DocumentBatch(
        name=name,
        source=source,
        start_date=start_date,
        completed_date=completed_date,
        batch_meta=batch_meta,
        **kwargs,
    )


def make_document_uri(
    uri: str | None = None,
    source: str = "pytest",
    doc_hash: str | None = None,
    batch_id: int | None = None,
    version: int = 1,
    uri_meta: dict | None = None,
    **kwargs,
) -> models.DocumentURI:
    """
    Create a DocumentURI instance with sensible defaults.

    Args:
        uri: The URI path. If None, generates a unique path.
        source: Source identifier.
        doc_hash: Hash of the associated document.
        batch_id: Associated batch ID.
        version: Version number.
        uri_meta: URI metadata dictionary.
        **kwargs: Additional fields to pass to DocumentURI.

    Returns:
        A DocumentURI instance (not persisted to database).
    """
    if uri is None:
        uri = f"/tmp/test_{uuid.uuid4().hex[:8]}.pdf"
    if doc_hash is None:
        doc_hash = f"sha256-{uuid.uuid4().hex}"
    if uri_meta is None:
        uri_meta = {}

    return models.DocumentURI(
        uri=uri,
        source=source,
        doc_hash=doc_hash,
        batch_id=batch_id,
        version=version,
        uri_meta=uri_meta,
        **kwargs,
    )


def make_run_group(
    workflow_definition_id: str = "batch",
    batch_id: int = 1,
    param_definition_id: str = "default",
    name: str | None = None,
    start_date: datetime.datetime | None = None,
    created_date: datetime.datetime | None = None,
    **kwargs,
) -> models.RunGroup:
    """
    Create a RunGroup instance with sensible defaults.

    Args:
        workflow_definition_id: ID of the workflow definition.
        batch_id: Associated batch ID.
        param_definition_id: ID of the parameter definition.
        name: Run group name.
        start_date: When the run group started.
        created_date: When the run group was created.
        **kwargs: Additional fields to pass to RunGroup.

    Returns:
        A RunGroup instance (not persisted to database).
    """
    now = datetime.datetime.now()
    if start_date is None:
        start_date = now
    if created_date is None:
        created_date = now
    if name is None:
        name = f"Test Run Group {uuid.uuid4().hex[:8]}"

    return models.RunGroup(
        workflow_definition_id=workflow_definition_id,
        batch_id=batch_id,
        param_definition_id=param_definition_id,
        name=name,
        start_date=start_date,
        created_date=created_date,
        **kwargs,
    )


def make_workflow_run(
    doc_id: str | None = None,
    workflow_definition_id: str = "batch",
    run_group_id: int = 1,
    batch_id: int = 1,
    status: models.RunStatus = models.RunStatus.PENDING,
    priority: int = 0,
    start_date: datetime.datetime | None = None,
    created_date: datetime.datetime | None = None,
    run_params: dict | None = None,
    **kwargs,
) -> models.WorkflowRun:
    """
    Create a WorkflowRun instance with sensible defaults.

    Args:
        doc_id: Document hash. If None, generates a unique hash.
        workflow_definition_id: ID of the workflow definition.
        run_group_id: Associated run group ID.
        batch_id: Associated batch ID.
        status: Current run status.
        priority: Run priority (higher = more important).
        start_date: When the run started.
        created_date: When the run was created.
        run_params: Run parameters dictionary.
        **kwargs: Additional fields to pass to WorkflowRun.

    Returns:
        A WorkflowRun instance (not persisted to database).
    """
    now = datetime.datetime.now()
    if doc_id is None:
        doc_id = f"sha256-{uuid.uuid4().hex}"
    if start_date is None:
        start_date = now
    if created_date is None:
        created_date = now
    if run_params is None:
        run_params = {}

    return models.WorkflowRun(
        doc_id=doc_id,
        workflow_definition_id=workflow_definition_id,
        run_group_id=run_group_id,
        batch_id=batch_id,
        status=status,
        priority=priority,
        start_date=start_date,
        created_date=created_date,
        run_params=run_params,
        **kwargs,
    )


def make_run_step(
    workflow_run_id: int = 1,
    step_type: models.WorkflowStepType = models.WorkflowStepType.PARSE,
    step_config_id: int = 1,
    status: models.RunStatus = models.RunStatus.PENDING,
    step_order: int = 0,
    created_date: datetime.datetime | None = None,
    **kwargs,
) -> models.RunStep:
    """
    Create a RunStep instance with sensible defaults.

    Args:
        workflow_run_id: Associated workflow run ID.
        step_type: Type of workflow step.
        step_config_id: Associated step config ID.
        status: Current step status.
        step_order: Order of this step in the workflow.
        created_date: When the step was created.
        **kwargs: Additional fields to pass to RunStep.

    Returns:
        A RunStep instance (not persisted to database).
    """
    if created_date is None:
        created_date = datetime.datetime.now()

    return models.RunStep(
        workflow_run_id=workflow_run_id,
        step_type=step_type,
        step_config_id=step_config_id,
        status=status,
        step_order=step_order,
        created_date=created_date,
        **kwargs,
    )


def make_step_config(
    step_type: models.WorkflowStepType = models.WorkflowStepType.PARSE,
    config_hash: str | None = None,
    parameters: dict | None = None,
    **kwargs,
) -> models.StepConfig:
    """
    Create a StepConfig instance with sensible defaults.

    Args:
        step_type: Type of workflow step.
        config_hash: Hash of the configuration. If None, generates a unique hash.
        parameters: Configuration parameters dictionary.
        **kwargs: Additional fields to pass to StepConfig.

    Returns:
        A StepConfig instance (not persisted to database).
    """
    if config_hash is None:
        config_hash = f"config-{uuid.uuid4().hex}"
    if parameters is None:
        parameters = {}

    return models.StepConfig(
        step_type=step_type,
        config_hash=config_hash,
        parameters=parameters,
        **kwargs,
    )


def make_document_bytes(
    hash: str | None = None,
    artifact_type: models.ArtifactType = models.ArtifactType.DOC,
    file_bytes: bytes = b"test content",
    **kwargs,
) -> models.DocumentBytes:
    """
    Create a DocumentBytes instance with sensible defaults.

    Args:
        hash: Document hash. If None, generates a unique hash.
        artifact_type: Type of artifact.
        file_bytes: The actual file content.
        **kwargs: Additional fields to pass to DocumentBytes.

    Returns:
        A DocumentBytes instance (not persisted to database).
    """
    if hash is None:
        hash = f"sha256-{uuid.uuid4().hex}"

    return models.DocumentBytes(
        hash=hash,
        artifact_type=artifact_type,
        file_bytes=file_bytes,
        **kwargs,
    )
