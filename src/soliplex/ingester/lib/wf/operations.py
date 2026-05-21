import datetime
import json
import logging

import opendal
import yaml
from sqlalchemy import Integer
from sqlalchemy import and_
from sqlalchemy import cast
from sqlalchemy import delete
from sqlalchemy import extract
from sqlalchemy import func
from sqlalchemy import literal_column
from sqlalchemy import or_
from sqlalchemy import tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel import update

from soliplex.ingester.lib.dal import get_storage_operator
from soliplex.ingester.lib.docling import get_docling_schema_version
from soliplex.ingester.lib.models import ArtifactType
from soliplex.ingester.lib.models import ConfigSet
from soliplex.ingester.lib.models import ConfigSetItem
from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.models import DocumentBatch
from soliplex.ingester.lib.models import DocumentInfo
from soliplex.ingester.lib.models import DocumentURI
from soliplex.ingester.lib.models import LifeCycleEvent
from soliplex.ingester.lib.models import LifecycleHistory
from soliplex.ingester.lib.models import ResourceLock
from soliplex.ingester.lib.models import ResourceLockKind
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import StepConfig
from soliplex.ingester.lib.models import WorkerCheckin
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowRunWithDetails
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.models import get_session
from soliplex.ingester.lib.operations import DocumentNotFoundError
from soliplex.ingester.lib.operations import get_batch
from soliplex.ingester.lib.operations import get_document
from soliplex.ingester.lib.operations import get_document_uris_by_hash
from soliplex.ingester.lib.operations import get_documents_in_batch

from .registry import get_param_set
from .registry import get_workflow_definition

logger = logging.getLogger(__name__)


class NotFoundError(Exception):
    pass


async def _rag_resource_key_for_param(param_id: str | None) -> str | None:
    """Resolve the cross-subsystem ``resource_key`` for the RAG DB
    targeted by *param_id*. Returns ``None`` when the param set does
    not declare a store path (no STORE step or no ``data_dir``)."""
    if param_id is None:
        return None
    from soliplex.ingester.lib.rag import resolve_lancedb_path_from_param_config
    from soliplex.ingester.lib.rag import resource_key_for

    param_set = await get_param_set(param_id)
    store_cfg = param_set.config.get(WorkflowStepType.STORE, {})
    if "data_dir" not in store_cfg:
        return None
    # Below: requires a real LanceDB path. Exercised by integration
    # tests, not unit coverage.
    db_path = resolve_lancedb_path_from_param_config(store_cfg)  # pragma: no cover
    return resource_key_for(db_path)  # pragma: no cover


async def _filter_existing_in_rag(  # pragma: no cover
    documents: list[Document],
    param_id: str,
) -> list[Document]:
    """Remove documents that already exist in the target RAG DB.

    Resolves the RAG database path from the param set's store config
    and checks each document's hash against the database metadata.

    Exercised by integration tests with a real LanceDB store; excluded
    from unit-test coverage.
    """
    from soliplex.ingester.lib.rag import check_rag_existence

    param_set = await get_param_set(param_id)
    store_cfg = param_set.config.get(WorkflowStepType.STORE, {})
    embed_cfg = param_set.config.get(WorkflowStepType.EMBED, {})
    if "data_dir" not in store_cfg:
        logger.warning("skip_existing: no store.data_dir in param set, skipping RAG pre-check")
        return documents

    all_hashes = [doc.hash for doc in documents]
    existing = await check_rag_existence(all_hashes, store_cfg, embed_cfg)
    if existing:
        filtered = [d for d in documents if d.hash not in existing]
        logger.info(f"skip_existing: {len(existing)} already in RAG, {len(filtered)} to process")
        return filtered
    return documents


async def create_workflow_runs_for_batch(
    batch_id: int,
    workflow_definition_id: str,
    priority: int = 0,
    param_id: str | None = None,
    only_unparsed: bool = False,
    skip_existing: bool = True,
) -> tuple[RunGroup, list[WorkflowRun]]:
    """
    Creates a workflow run for each document in a batch

    Args:
        batch_id: Batch to create runs for.
        workflow_definition_id: Workflow to use.
        priority: Run priority.
        param_id: Parameter set ID.
        only_unparsed: Only create runs for unparsed documents.
        skip_existing: Skip documents already present in the
            target RAG database (default True).
    """
    if only_unparsed:  # pragma: no cover
        # Requires a populated param registry and a real RAG store to
        # exercise the de-dup logic. Covered by integration tests.
        if param_id is None:
            raise ValueError("param_id must be specified when only_unparsed is True")
        if workflow_definition_id is None:
            raise ValueError("workflow_definition_id must be specified when only_unparsed is True")
        run_groups = await get_run_groups_for_batch(batch_id)
        run_group = None
        for r in run_groups:
            if r.workflow_definition_id == workflow_definition_id and r.param_definition_id == param_id:
                run_group = r
                break
        if run_group is None:
            run_group = await create_run_group(
                workflow_definition_id=workflow_definition_id,
                batch_id=batch_id,
                param_id=param_id,
            )
        batch_documents = await get_documents_in_batch(batch_id)
        if skip_existing and param_id is not None:
            batch_documents = await _filter_existing_in_rag(batch_documents, param_id)

        existing_runs = await get_workflow_runs_for_group(run_group.id)
        existing_ids = set([run.doc_id for run in existing_runs])
        runs = []
        for doc in batch_documents:
            if doc.hash not in existing_ids:
                run, steps = await create_workflow_run(
                    run_group=run_group,
                    doc_id=doc.hash,
                    priority=priority,
                )
                runs.append(run)
        return run_group, runs
    else:
        run_group = await create_run_group(
            workflow_definition_id=workflow_definition_id,
            batch_id=batch_id,
            param_id=param_id,
        )
        batch_documents = await get_documents_in_batch(batch_id)
        if skip_existing and param_id is not None:
            batch_documents = await _filter_existing_in_rag(batch_documents, param_id)
        runs = []
        for doc in batch_documents:
            run, steps = await create_workflow_run(
                run_group=run_group,
                doc_id=doc.hash,
                priority=priority,
            )
            runs.append(run)
        return run_group, runs


# @alru_cache(maxsize=1024)
async def get_step_config_ids(param_id: str) -> dict[WorkflowStepType, int]:
    """
    Returns a map of step type to step config ID for a given
    parameter set.  this config id is potentially shared across
    multiple parameter configurations if the parameters are the
    same up to that step (e.g. step 1 and 2 are identical
    but 3 is different then 1 and 2 get shared)
    """
    # TODO: if param sets become dynamic then this needs to be updated
    param_set = await get_param_set(param_id)
    js = param_set.model_dump_json()
    yaml_str = yaml.dump(yaml.safe_load(js))

    id_map = {}
    typelist = list(WorkflowStepType)
    async with get_session() as session:
        with session.no_autoflush:
            setq = select(ConfigSet).where(ConfigSet.yaml_contents == yaml_str)
            setrs = await session.exec(setq)
            exist_set = setrs.first()
            if exist_set:
                # Get step configs for this config set using SQLModel ORM
                q = (
                    select(StepConfig)
                    .join(ConfigSetItem, ConfigSetItem.config_id == StepConfig.id)
                    .where(ConfigSetItem.config_set_id == exist_set.id)
                )
                result = await session.exec(q)
                step_configs = result.all()

                for step_config in step_configs:
                    id_map[step_config.step_type] = step_config.id
                return id_map
            configset = ConfigSet(
                yaml_id=param_set.id,
                yaml_contents=yaml_str,
                created_date=datetime.datetime.now(datetime.UTC),
            )
            session.add(configset)
            await session.flush()
            await session.refresh(configset)
            cuml_cfg = {}
            cuml_str = json.dumps(cuml_cfg, indent=4)
            # create step configs for each step to help matches when steps are missing
            for st in typelist:
                if st in param_set.config:
                    step_config = param_set.config[st]
                    if st == WorkflowStepType.PARSE:
                        step_config["docling_schema"] = get_docling_schema_version()
                else:
                    step_config = {}
                cuml_cfg = cuml_cfg.copy()
                cuml_cfg.update({st.value: step_config})
                cuml_str = json.dumps(cuml_cfg, indent=4)
                existq = select(StepConfig).where(StepConfig.step_type == st).where(StepConfig.cuml_config_json == cuml_str)
                rs = await session.exec(existq)
                exist = rs.first()
                if exist:
                    id_map[st] = exist.id
                else:
                    step_config = StepConfig(
                        step_type=st,
                        config_json=step_config,
                        cuml_config_json=cuml_str,
                    )
                    session.add(step_config)
                    await session.flush()
                    await session.refresh(step_config)
                    id_map[st] = step_config.id
                step_id = id_map[st]
                set_id = configset.id
                logger.info(f"created step config {step_id} config_set={set_id}")
                setitem = ConfigSetItem(config_set_id=set_id, config_id=step_id)
                session.add(setitem)
                await session.flush()
            await session.commit()
    return id_map


async def get_run_groups_for_batch(batch_id: int | None = None) -> list[RunGroup]:
    async with get_session() as session:
        q = select(RunGroup)
        if batch_id is not None:
            q = q.where(RunGroup.batch_id == batch_id)
        q = q.order_by(RunGroup.created_date.desc())
        rs = await session.exec(q)
        groups = rs.all()
        for x in groups:
            session.expunge(x)
        return groups


async def get_run_group(run_group_id: int) -> RunGroup:
    async with get_session() as session:
        q = select(RunGroup).where(RunGroup.id == run_group_id)
        rs = await session.exec(q)
        res = rs.first()
        if res:
            session.expunge(res)
            return res
        raise NotFoundError(f"run group {run_group_id} not found")


async def get_run_group_stats(run_group_id: int) -> dict[RunStatus, int]:
    """
    Get statistics on workflow run statuses for a run group.

    Returns count of distinct workflow runs per status.
    Uses SQLModel ORM for cross-database compatibility.

    Parameters
    ----------
    run_group_id : int
        The run group ID to get stats for

    Returns
    -------
    dict[RunStatus, int]
        Dictionary mapping status to count of workflow runs
    """
    async with get_session() as session:
        # Query with explicit join and group by
        q = (
            select(
                RunStep.status,
                func.count(RunStep.workflow_run_id.distinct()).label("count"),
            )
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .where(WorkflowRun.run_group_id == run_group_id)
            .group_by(RunStep.status)
        )

        result = await session.exec(q)
        rows = result.all()

        # Initialize all statuses to 0
        stats = {status.value: 0 for status in RunStatus}

        # Populate with actual counts
        for row in rows:
            stats[row[0]] = row[1]

        return stats


async def complete_run_group(
    run_group_id: int,
    status: RunStatus,
    status_message: str | None = None,
) -> None:
    """
    Mark a run group as completed.

    Parameters
    ----------
    run_group_id : int
        The run group ID to complete
    status : RunStatus
        Final status (e.g. COMPLETED or FAILED)
    status_message : str | None
        Optional status message
    """
    try:
        dt = datetime.datetime.now(datetime.UTC)
        async with get_session() as session:
            q = select(RunGroup).where(RunGroup.id == run_group_id)
            result = await session.exec(q)
            rg = result.first()
            if rg is None:
                logger.error(f"complete_run_group: run group {run_group_id} not found")
                return
            rg.status = status
            rg.status_date = dt
            rg.completed_date = dt
            if status_message is not None:
                rg.status_message = status_message
            session.add(rg)
            await session.commit()
    except Exception:
        logger.exception("error in complete_run_group:")


async def delete_run_group(run_group_id: int) -> dict[str, int]:
    """
    Delete a RunGroup and all dependent records using Python/SQLModel.

    Works with both SQLite and PostgreSQL databases.

    This function performs cascading deletion of:
    - RunStep records (via WorkflowRun)
    - LifecycleHistory records (for both RunGroup and WorkflowRuns)
    - WorkflowRun records
    - RunGroup record

    All deletions occur within a single transaction to ensure atomicity.

    Parameters
    ----------
    run_group_id : int
        The ID of the RunGroup to delete

    Returns
    -------
    dict[str, int]
        A dictionary containing deletion statistics:
        - deleted_runsteps: Number of RunStep records deleted
        - deleted_lifecyclehistory: Number of LifecycleHistory records deleted
        - deleted_workflowruns: Number of WorkflowRun records deleted
        - deleted_rungroups: Number of RunGroup records deleted (should be 1)
        - total_deleted: Total number of records deleted

    Raises
    ------
    NotFoundError
        If the RunGroup with the specified ID does not exist
    """
    async with get_session() as session:
        # Step 1: Verify RunGroup exists
        q = select(RunGroup).where(RunGroup.id == run_group_id)
        result = await session.exec(q)
        run_group = result.first()

        if not run_group:
            raise NotFoundError(f"RunGroup with id {run_group_id} does not exist")

        # Step 2: Get all workflow run IDs in this group (needed for cascading deletes)
        workflow_run_q = select(WorkflowRun.id).where(WorkflowRun.run_group_id == run_group_id)
        workflow_run_ids_result = await session.exec(workflow_run_q)
        workflow_run_ids = list(workflow_run_ids_result.all())

        # Step 3: Delete RunSteps for all WorkflowRuns in this group
        deleted_runsteps = 0
        if workflow_run_ids:
            runstep_delete_q = delete(RunStep).where(RunStep.workflow_run_id.in_(workflow_run_ids))
            runstep_result = await session.exec(runstep_delete_q)
            deleted_runsteps = runstep_result.rowcount  # type: ignore

        # Step 4: Delete LifecycleHistory records (for both RunGroup and WorkflowRuns)
        if workflow_run_ids:
            lifecycle_delete_q = delete(LifecycleHistory).where(
                or_(
                    LifecycleHistory.run_group_id == run_group_id,
                    LifecycleHistory.workflow_run_id.in_(workflow_run_ids),
                )
            )
        else:
            # No workflow runs, just delete lifecycle history for the run group
            lifecycle_delete_q = delete(LifecycleHistory).where(LifecycleHistory.run_group_id == run_group_id)

        lifecycle_result = await session.exec(lifecycle_delete_q)
        deleted_lifecyclehistory = lifecycle_result.rowcount  # type: ignore

        # Step 5: Delete WorkflowRuns
        workflowrun_delete_q = delete(WorkflowRun).where(WorkflowRun.run_group_id == run_group_id)
        workflowrun_result = await session.exec(workflowrun_delete_q)
        deleted_workflowruns = workflowrun_result.rowcount  # type: ignore

        # Step 6: Delete the RunGroup itself
        rungroup_delete_q = delete(RunGroup).where(RunGroup.id == run_group_id)
        rungroup_result = await session.exec(rungroup_delete_q)
        deleted_rungroups = rungroup_result.rowcount  # type: ignore

        # Step 7: Commit the transaction
        await session.commit()

        # Return statistics
        return {
            "deleted_runsteps": deleted_runsteps,
            "deleted_lifecyclehistory": deleted_lifecyclehistory,
            "deleted_workflowruns": deleted_workflowruns,
            "deleted_rungroups": deleted_rungroups,
            "total_deleted": deleted_runsteps + deleted_lifecyclehistory + deleted_workflowruns + deleted_rungroups,
        }


async def create_run_group(
    workflow_definition_id: str | None,
    batch_id: int | None = None,
    param_id: str | None = None,
    name: str | None = None,
) -> RunGroup:
    batch = await get_batch(batch_id)
    if batch is None:
        raise NotFoundError(f"Batch {batch_id} not found")

    # pull definitions from registry so defaults can be used and check if ids are invalid
    workflow_def = await get_workflow_definition(workflow_definition_id)
    param_set = await get_param_set(param_id)
    dt = datetime.datetime.now(datetime.UTC)

    async with get_session() as session:
        run_group = RunGroup(
            workflow_definition_id=workflow_def.id,
            batch_id=batch_id,
            name=name,
            param_definition_id=param_set.id,
            created_date=dt,
            start_date=dt,
        )

        session.add(run_group)

        await session.flush()
        await session.refresh(run_group)
        session.expunge(run_group)
        await session.commit()
        return run_group


async def create_lifecycle_history(
    run_group_id: int,
    workflow_run_id: int,
    event: LifeCycleEvent,
    status: RunStatus,
    step_id: int | None = None,
    handler_name: str | None = None,
    status_message: str | None = None,
    status_meta: dict[str, str] | None = None,
) -> LifecycleHistory:
    dt = datetime.datetime.now(datetime.UTC)
    async with get_session() as session:
        run_group_history = LifecycleHistory(
            run_group_id=run_group_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            event=event,
            handler_name=handler_name,
            status=status,
            status_date=dt,
            start_date=dt,
            status_message=status_message,
            status_meta=status_meta,
        )

        session.add(run_group_history)

        await session.flush()
        await session.refresh(run_group_history)
        session.expunge(run_group_history)
        await session.commit()
        return run_group_history


async def update_lifecycle_history(
    hist_id: int,
    status: RunStatus,
    status_message: str | None = None,
    status_meta: dict[str, str] | None = None,
) -> None:
    dt = datetime.datetime.now(datetime.UTC)
    end_date = None
    if status == RunStatus.COMPLETED or status == RunStatus.FAILED:
        end_date = dt
    async with get_session() as session:
        q = (
            update(LifecycleHistory)
            .where(LifecycleHistory.id == hist_id)
            .values(
                status=status,
                status_date=dt,
                status_message=status_message,
                status_meta=status_meta,
                completed_date=end_date,
            )
        )
        await session.exec(q)
        await session.commit()


async def get_lifecycle_history(
    workflow_run_id: int | None = None,
    run_group_id: int | None = None,
) -> list[LifecycleHistory]:
    """
    Get lifecycle history records for a workflow run or run group.

    Parameters
    ----------
    workflow_run_id : int | None
        Filter by workflow run ID
    run_group_id : int | None
        Filter by run group ID

    Returns
    -------
    list[LifecycleHistory]
        List of LifecycleHistory records ordered by start_date

    Raises
    ------
    ValueError
        If neither workflow_run_id nor run_group_id is provided
    """
    async with get_session() as session:
        q = select(LifecycleHistory)

        if workflow_run_id:
            q = q.where(LifecycleHistory.workflow_run_id == workflow_run_id)
        elif run_group_id:
            q = q.where(LifecycleHistory.run_group_id == run_group_id)
        else:
            raise ValueError("Must provide either workflow_run_id or run_group_id")

        q = q.order_by(LifecycleHistory.start_date)
        rs = await session.exec(q)
        history = rs.all()

        for record in history:
            session.expunge(record)

        return history


async def create_single_workflow_run(
    workflow_definition_id: str,
    doc_id: str,
    priority: int = 0,
    param_id: str | None = None,
) -> tuple[WorkflowRun, list[RunStep]]:
    doc = await get_document(doc_id)
    if doc:
        uris = await get_document_uris_by_hash(doc.hash)
        batch_id = uris[0].batch_id
        run_group = await create_run_group(
            workflow_definition_id=workflow_definition_id,
            batch_id=batch_id,
            name=f"single run {doc_id} ",
            param_id=param_id,
        )
        return await create_workflow_run(run_group, doc_id, priority=priority)
    else:  # pragma: no cover - defensive; get_document already raises
        raise DocumentNotFoundError(doc_id)


async def create_workflow_run(
    run_group: RunGroup,
    doc_id: str,
    priority: int = 0,
) -> tuple[WorkflowRun, list[RunStep]]:
    """
    Creates a new workflow run.

    Args:
        workflow_definitinon_id (str | None): the ID of the workflow
            definition. if None, the default workflow will be used
        batch_id (int): the ID of the batch containing the document
        doc_id (str): the ID of the document being processed
        priority (int): the priority of the workflow run
        param_id (str | None): the ID of the parameter set

    Returns:
        A tuple containing the newly created workflow run and a list
        of newly created run steps
    """
    batch_id = run_group.batch_id
    workflow_definition_id = run_group.workflow_definition_id
    param_id = run_group.param_definition_id
    batch = await get_batch(batch_id)
    if batch is None:
        raise NotFoundError(f"Batch {batch_id} not found")
    workflow_def = await get_workflow_definition(workflow_definition_id)
    parameter_ids = await get_step_config_ids(param_id)
    created = datetime.datetime.now(datetime.UTC)
    args = {
        "param_id": param_id,
        "workflow_id": workflow_definition_id,
        "source": batch.source,
    }
    # Resolve the RAG-DB resource_key once per run-group/param so we
    # can stamp it on every ``save_to_rag``-style step. The claim
    # layer uses this to skip steps whose lock is held by another
    # subsystem (web vacuum, lifecycle, CLI).
    rag_resource_key = await _rag_resource_key_for_param(param_id)

    async with get_session() as session:
        workflow_run = WorkflowRun(
            run_group_id=run_group.id,
            workflow_definition_id=workflow_def.id,
            batch_id=batch_id,
            doc_id=doc_id,
            start_date=datetime.datetime.now(datetime.UTC),
            priority=priority,
            created_date=created,
            run_params=args,
        )
        session.add(workflow_run)
        await session.flush()
        await session.refresh(workflow_run)

        new_steps = []
        idx = 0
        for step_type, evt_handler in workflow_def.item_steps.items():
            resource_key = rag_resource_key if step_type == WorkflowStepType.STORE else None
            run_step = RunStep(
                workflow_run_id=workflow_run.id,
                workflow_step_number=idx + 1,
                workflow_step_name=evt_handler.name,
                retries=evt_handler.retries,
                priority=priority,
                created_date=created,
                status_date=created,
                step_type=step_type,
                step_config_id=parameter_ids[step_type],
                is_last_step=idx == len(workflow_def.item_steps) - 1,
                resource_key=resource_key,
            )
            session.add(run_step)
            new_steps.append(run_step)
            idx += 1
        await session.flush()
        session.expunge(workflow_run)
        for step in new_steps:
            session.expunge(step)
        await session.commit()

        return workflow_run, new_steps


async def get_document_info_for_workflow_runs(
    workflow_runs: list[WorkflowRun],
) -> dict[str, DocumentInfo]:
    """
    Fetch Document and DocumentURI info for a list of workflow runs.

    Args:
        workflow_runs: List of WorkflowRun objects

    Returns:
        Dict mapping doc_id -> DocumentInfo
    """
    if not workflow_runs:
        return {}

    # Collect unique doc_ids
    doc_ids = list({run.doc_id for run in workflow_runs})

    # Build result within session to avoid detached instance errors
    result: dict[str, DocumentInfo] = {}

    async with get_session() as session:
        # Fetch all Documents in one query
        doc_q = select(Document).where(Document.hash.in_(doc_ids))
        doc_rs = await session.exec(doc_q)
        documents = {doc.hash: doc for doc in doc_rs.all()}

        # Fetch all DocumentURIs matching doc_hash
        doc_uri_q = select(DocumentURI).where(DocumentURI.doc_hash.in_(doc_ids))
        doc_uri_rs = await session.exec(doc_uri_q)
        all_doc_uris = doc_uri_rs.all()

        # Build a lookup by (batch_id, doc_hash)
        doc_uris_by_batch_hash: dict[tuple[int, str], DocumentURI] = {}
        for uri in all_doc_uris:
            key = (uri.batch_id, uri.doc_hash)
            doc_uris_by_batch_hash[key] = uri

        # Build DocumentInfo for each workflow run's doc_id within the session
        for run in workflow_runs:
            doc = documents.get(run.doc_id)
            doc_uri = doc_uris_by_batch_hash.get((run.batch_id, run.doc_id))

            result[run.doc_id] = DocumentInfo(
                uri=doc_uri.uri if doc_uri else None,
                source=doc_uri.source if doc_uri else None,
                file_size=doc.file_size if doc else None,
                mime_type=doc.mime_type if doc else None,
            )

    return result


async def get_workflows(
    batch_id: int | None,
    include_steps: bool = False,
    include_doc_info: bool = False,
    page: int | None = None,
    rows_per_page: int | None = None,
) -> tuple[list[WorkflowRun] | list[WorkflowRunWithDetails], int]:
    """
    Get workflow runs, optionally with their associated steps and document info.

    Args:
        batch_id: Optional batch ID filter
        include_steps: If True, include associated RunSteps for each workflow run
        include_doc_info: If True, include document info (uri, source, file_size, mime_type)
        page: Page number (1-indexed). If None, returns all rows.
        rows_per_page: Number of rows per page. If None, returns all rows.

    Returns:
        Tuple of (list of workflow runs, total count)
    """
    async with get_session() as session:
        # Build base query
        q = select(WorkflowRun)
        if batch_id is not None:
            q = q.where(WorkflowRun.batch_id == batch_id)

        # Add consistent ordering (newest first)
        q = q.order_by(WorkflowRun.created_date.desc())

        # Get total count before pagination
        count_q = select(func.count()).select_from(WorkflowRun)
        if batch_id is not None:
            count_q = count_q.where(WorkflowRun.batch_id == batch_id)
        count_rs = await session.exec(count_q)
        total = count_rs.one()

        # Apply pagination if parameters provided
        if page is not None and rows_per_page is not None:
            offset = (page - 1) * rows_per_page
            q = q.offset(offset).limit(rows_per_page)

        # Execute query
        rs = await session.exec(q)
        res = rs.all()
        for x in res:
            session.expunge(x)

        # If neither steps nor doc_info requested, return raw workflow runs
        if not include_steps and not include_doc_info:
            return res, total

        # Load optional data
        steps_by_run_id = {}
        doc_info_by_doc_id = {}

        if include_steps:
            workflow_run_ids = [run.id for run in res]
            steps_by_run_id = await get_steps_for_workflow_runs(workflow_run_ids)

        if include_doc_info:
            doc_info_by_doc_id = await get_document_info_for_workflow_runs(res)

        # Combine workflow runs with their details
        result = []
        for run in res:
            steps = steps_by_run_id.get(run.id, []) if include_steps else None
            doc_info = doc_info_by_doc_id.get(run.doc_id) if include_doc_info else None
            result.append(
                WorkflowRunWithDetails(
                    workflow_run=run,
                    steps=steps,
                    document_info=doc_info,
                )
            )

        return result, total


async def get_workflows_for_status(
    status: RunStatus,
    batch_id: int | None = None,
    include_doc_info: bool = False,
    page: int | None = None,
    rows_per_page: int | None = None,
) -> tuple[list[WorkflowRun] | list[WorkflowRunWithDetails], int]:
    """
    Get workflow runs filtered by status, optionally paginated.

    Args:
        status: Filter by run status
        batch_id: Optional batch ID filter
        include_doc_info: If True, include document info (uri, source, file_size, mime_type)
        page: Page number (1-indexed). If None, returns all rows.
        rows_per_page: Number of rows per page. If None, returns all rows.

    Returns:
        Tuple of (list of workflow runs, total count)
    """
    async with get_session() as session:
        # Build base query
        q = select(WorkflowRun).where(WorkflowRun.status == status)
        if batch_id is not None:
            q = q.where(WorkflowRun.batch_id == batch_id)

        # Add consistent ordering (newest first)
        q = q.order_by(WorkflowRun.created_date.desc())

        # Get total count before pagination
        count_q = select(func.count()).select_from(WorkflowRun).where(WorkflowRun.status == status)
        if batch_id is not None:
            count_q = count_q.where(WorkflowRun.batch_id == batch_id)

        count_result = await session.exec(count_q)
        total = count_result.one()

        # Apply pagination if requested
        if page is not None and rows_per_page is not None:
            offset = (page - 1) * rows_per_page
            q = q.offset(offset).limit(rows_per_page)

        # Execute query
        rs = await session.exec(q)
        res = rs.all()
        for x in res:
            session.expunge(x)

        if not include_doc_info:
            return res, total

        # Load document info
        doc_info_by_doc_id = await get_document_info_for_workflow_runs(res)

        # Combine workflow runs with their document info
        result = []
        for run in res:
            doc_info = doc_info_by_doc_id.get(run.doc_id)
            result.append(
                WorkflowRunWithDetails(
                    workflow_run=run,
                    steps=None,
                    document_info=doc_info,
                )
            )

        return result, total


async def get_workflow_run(
    workflow_run_id: int, include_steps: bool = False
) -> WorkflowRun | tuple[WorkflowRun, list[RunStep]]:
    async with get_session() as session:
        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
        rs = await session.exec(q)
        run = rs.first()

        if run:
            session.expunge(run)
            if include_steps:
                q = select(RunStep).where(RunStep.workflow_run_id == workflow_run_id)
                rs = await session.exec(q)
                steps = rs.all()
                for step in steps:
                    session.expunge(step)
                return run, steps

            return run
        raise NotFoundError(f"workflow run {workflow_run_id} not found")


async def get_workflow_runs(batch_id: int) -> WorkflowRun:
    async with get_session() as session:
        q = select(WorkflowRun).where(WorkflowRun.batch_id == batch_id)
        rs = await session.exec(q)
        res = rs.first()
        if res:
            session.expunge(res)
            return res
        raise NotFoundError(f"workflow run {batch_id} not found")


async def get_workflow_runs_for_group(run_group_id: int) -> list[WorkflowRun]:
    async with get_session() as session:
        q = select(WorkflowRun).where(WorkflowRun.run_group_id == run_group_id)
        rs = await session.exec(q)
        res = rs.all()
        if res:
            session.expunge_all()
            return res
        return []


async def get_run_step(run_step_id: int) -> RunStep:
    async with get_session() as session:
        q = select(RunStep).where(RunStep.id == run_step_id)
        rs = await session.exec(q)
        res = rs.first()
        if res:
            session.expunge(res)
            return res
        raise NotFoundError(f"run step {run_step_id} not found")


async def get_step_config_by_id(step_config_id: int) -> StepConfig:
    async with get_session() as session:
        q = select(StepConfig).where(StepConfig.id == step_config_id)
        rs = await session.exec(q)
        res = rs.first()
        if res:
            session.expunge(res)
            return res
        raise NotFoundError(f"step config {step_config_id} not found")


async def find_operator_for_workflow_run(
    workflow_run_id: int,
    step_type: WorkflowStepType,
    artifact_type: ArtifactType,
) -> opendal.AsyncOperator:
    step_config = await get_step_config_for_workflow_run(workflow_run_id, step_type)
    return get_storage_operator(artifact_type, step_config)


async def get_step_config_for_workflow_run(workflow_run_id: int, step_type: WorkflowStepType) -> StepConfig:
    """
    Get the step configuration for a specific workflow run and step type.

    Uses SQLModel ORM with explicit JOIN for cross-database compatibility.
    Replaces raw SQL to prevent SQL injection vulnerabilities.

    Parameters
    ----------
    workflow_run_id : int
        The workflow run ID
    step_type : WorkflowStepType
        The step type to find

    Returns
    -------
    StepConfig
        The step configuration

    Raises
    ------
    NotFoundError
        If no step config found for the given workflow run and step type
    """
    async with get_session() as session:
        # Build query with explicit join and type-safe enum comparison
        q = (
            select(StepConfig)
            .join(RunStep, RunStep.step_config_id == StepConfig.id)
            .where(RunStep.workflow_run_id == workflow_run_id)
            .where(RunStep.step_type == step_type)  # Direct enum comparison, SQLAlchemy extracts NAME
        )

        result = await session.exec(q)
        step_config = result.first()

        if not step_config:
            raise NotFoundError(f"step config {step_type} not found")

        session.expunge(step_config)
        return step_config


async def update_run_status(
    workflow_run_id: int,
    is_last_step: bool,
    status: RunStatus,
    session,
    status_message: str | None = None,
) -> RunStatus:
    update_status = None
    if is_last_step and status == RunStatus.COMPLETED:
        update_status = RunStatus.COMPLETED
    elif status == RunStatus.FAILED:
        update_status = RunStatus.FAILED
    elif not is_last_step and status in (
        RunStatus.COMPLETED,
        RunStatus.RUNNING,
        RunStatus.ERROR,
    ):
        update_status = RunStatus.RUNNING
    logger.info(f"update run status {workflow_run_id} {update_status} {status}")
    if update_status is not None:
        dt = datetime.datetime.now(datetime.UTC)
        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run_id).with_for_update(nowait=True)
        results = await session.exec(q)
        wf = results.first()
        if wf is None:
            logger.error(f"update_run_status: workflow run {workflow_run_id} not found")
            return status
        wf.status_date = dt
        wf.status = update_status
        if status_message is not None:
            wf.status_message = status_message
        if status == RunStatus.COMPLETED or status == RunStatus.FAILED:
            wf.completed_date = dt

        session.add(wf)
        return update_status
    return status


async def cancel_pending_steps(
    workflow_run_id: int,
    session,
) -> int:
    """Cancel all PENDING steps for a workflow run.

    Used when a step transitions to FAILED so that remaining
    steps that can never execute are marked CANCELLED.

    Parameters
    ----------
    workflow_run_id : int
        The workflow run whose pending steps to cancel.
    session
        An active database session (caller manages the transaction).

    Returns
    -------
    int
        Number of steps cancelled.
    """
    stmt = (
        update(RunStep)
        .where(RunStep.workflow_run_id == workflow_run_id)
        .where(RunStep.status == RunStatus.PENDING)
        .values(
            status=RunStatus.CANCELLED,
            status_date=datetime.datetime.now(datetime.UTC),
            status_message="cancelled: prior step failed",
        )
    )
    result = await session.exec(stmt)
    return result.rowcount


async def get_steps_for_batch(batch_id: int) -> list[RunStep]:
    """
    Get all run steps for a specific batch.

    Uses SQLModel ORM with explicit JOIN for cross-database compatibility.

    Parameters
    ----------
    batch_id : int
        The batch ID to get steps for

    Returns
    -------
    list[RunStep]
        List of run steps for the batch
    """
    async with get_session() as session:
        q = (
            select(RunStep)
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .where(WorkflowRun.batch_id == batch_id)
        )

        result = await session.exec(q)
        steps = result.all()

        if steps:
            session.expunge_all()

        return list(steps)


async def get_steps_for_workflow_runs(workflow_run_ids: list[int]) -> dict[int, list[RunStep]]:
    """
    Load steps for multiple workflow runs efficiently.
    Returns dict mapping workflow_run_id -> list[RunStep]
    """
    if not workflow_run_ids:
        return {}

    async with get_session() as session:
        q = select(RunStep).where(RunStep.workflow_run_id.in_(workflow_run_ids))
        rs = await session.exec(q)
        all_steps = rs.all()
        for x in all_steps:
            session.expunge(x)

        # Group steps by workflow_run_id
        steps_by_run_id: dict[int, list[RunStep]] = {}
        for step in all_steps:
            if step.workflow_run_id not in steps_by_run_id:
                steps_by_run_id[step.workflow_run_id] = []
            steps_by_run_id[step.workflow_run_id].append(step)

        return steps_by_run_id


async def get_run_steps(status: RunStatus) -> list[RunStep]:
    async with get_session() as session:
        q = select(RunStep).where(RunStep.status == status)
        rs = await session.exec(q)
        res = rs.all()
        for x in res:
            session.expunge(x)
        return res


async def get_run_steps_for_run_group(
    run_group_id: int,
    status: RunStatus,
) -> list[dict]:
    """
    Get run step details with URI info for a run group,
    filtered by status.

    Parameters
    ----------
    run_group_id : int
        The run group ID to query
    status : RunStatus
        The step status to filter on (e.g. RunStatus.FAILED)

    Returns
    -------
    list[dict]
        Each dict contains batch_id, uri, status, step_type,
        and status_message.
    """
    async with get_session() as session:
        q = (
            select(
                DocumentURI.batch_id,
                DocumentURI.uri,
                RunStep.status,
                RunStep.step_type,
                RunStep.status_message,
            )
            .join(
                WorkflowRun,
                WorkflowRun.id == RunStep.workflow_run_id,
            )
            .join(
                DocumentURI,
                (DocumentURI.doc_hash == WorkflowRun.doc_id) & (DocumentURI.batch_id == WorkflowRun.batch_id),
            )
            .where(WorkflowRun.run_group_id == run_group_id)
            .where(RunStep.status == status)
        )
        rs = await session.exec(q)
        return [
            {
                "batch_id": row.batch_id,
                "uri": row.uri,
                "status": row.status,
                "step_type": row.step_type,
                "status_message": row.status_message,
            }
            for row in rs.all()
        ]


async def get_run_group_durations(run_group_id: int) -> list[dict]:
    """
    Get duration statistics for a run group.

    **PostgreSQL only** - Uses PostgreSQL-specific date/time and JSON functions
    via SQLAlchemy for type safety and ORM benefits.

    Parameters
    ----------
    run_group_id : int
        The run group ID to get durations for

    Returns
    -------
    list[dict]
        Duration statistics per step type

    Raises
    ------
    RuntimeError
        If database is not PostgreSQL
    """
    from soliplex.ingester.lib.config import get_settings

    settings = get_settings()
    doc_db_url = (
        settings.doc_db_url.get_secret_value() if hasattr(settings.doc_db_url, "get_secret_value") else settings.doc_db_url
    )
    if "postgresql" not in doc_db_url:
        raise RuntimeError("get_run_group_durations requires PostgreSQL (uses PostgreSQL-specific functions)")

    async with get_session() as session:
        # Subquery for calculating durations and extracting page counts
        subq = (
            select(
                RunStep.workflow_step_name.label("step_type"),
                extract("epoch", RunStep.completed_date - RunStep.start_date).label("duration"),
                RunStep.start_date,
                RunStep.completed_date,
                # PostgreSQL-specific JSONB extraction
                cast(func.jsonb_extract_path_text(cast(Document.doc_meta, JSONB), "page_count"), Integer).label("pages"),
            )
            .select_from(RunStep)
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .join(DocumentBatch, DocumentBatch.id == WorkflowRun.batch_id)
            .join(Document, Document.hash == WorkflowRun.doc_id)
            .join(RunGroup, RunGroup.id == WorkflowRun.run_group_id)
            .where(RunGroup.id == run_group_id)
            .where(RunStep.status == RunStatus.COMPLETED)
        ).subquery()

        # Main query with aggregations
        q = select(
            subq.c.step_type,
            func.count(literal_column("1")).label("count"),
            func.round(func.max(subq.c.duration), 1).label("longest"),
            func.round(func.min(subq.c.duration), 1).label("shortest"),
            func.round(func.avg(subq.c.duration), 1).label("average"),
            func.sum(subq.c.pages).label("pages"),
            func.round(func.sum(subq.c.pages) / func.sum(subq.c.duration), 0).label("pages_per_min"),
            func.sum(subq.c.duration).label("total_duration"),
            func.round(extract("epoch", func.max(subq.c.completed_date) - func.min(subq.c.start_date)), 0).label(
                "wall_clock_time"
            ),
        ).group_by(subq.c.step_type)

        result = await session.exec(q)
        return [dict(row._mapping) for row in result.all()]


async def get_step_stats(run_group_id: int) -> list[dict]:
    """
    Get step statistics for a run group.

    **PostgreSQL only** - Uses PostgreSQL-specific JSONB functions
    via SQLAlchemy for type safety and ORM benefits.

    Parameters
    ----------
    run_group_id : int
        The run group ID to get stats for

    Returns
    -------
    list[dict]
        Statistics per batch, param set, step type, and status

    Raises
    ------
    RuntimeError
        If database is not PostgreSQL
    """
    from soliplex.ingester.lib.config import get_settings

    settings = get_settings()
    doc_db_url = (
        settings.doc_db_url.get_secret_value() if hasattr(settings.doc_db_url, "get_secret_value") else settings.doc_db_url
    )
    if "postgresql" not in doc_db_url:
        raise RuntimeError("get_step_stats requires PostgreSQL (uses PostgreSQL-specific functions)")

    async with get_session() as session:
        # Query with PostgreSQL-specific JSONB extraction
        q = (
            select(
                DocumentBatch.name,
                RunGroup.param_definition_id,
                RunStep.workflow_step_name,
                RunStep.status,
                func.count(literal_column("1")).label("count"),
                func.sum(cast(func.jsonb_extract_path_text(cast(Document.doc_meta, JSONB), "page_count"), Integer)).label(
                    "pages"
                ),
            )
            .select_from(RunStep)
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .join(DocumentBatch, DocumentBatch.id == WorkflowRun.batch_id)
            .join(Document, Document.hash == WorkflowRun.doc_id)
            .join(RunGroup, RunGroup.id == WorkflowRun.run_group_id)
            .where(RunGroup.id == run_group_id)
            .group_by(
                DocumentBatch.name,
                RunGroup.param_definition_id,
                RunStep.workflow_step_name,
                RunStep.status,
            )
            .order_by(
                DocumentBatch.name,
                RunStep.workflow_step_name,
                RunStep.status,
            )
        )

        result = await session.exec(q)
        return [dict(row._mapping) for row in result.all()]


async def reset_failed_steps(run_group_id: int) -> None:
    """
    Reset all failed steps and workflow runs in a run group.

    Sets failed steps back to PENDING with retry count reset to 0.
    Sets failed workflow runs back to RUNNING.
    Uses SQLModel ORM for cross-database compatibility.

    Parameters
    ----------
    run_group_id : int
        The run group ID to reset failed steps for
    """
    async with get_session() as session:
        # Subquery: Get workflow run IDs that are FAILED
        failed_runs_subq = (
            select(WorkflowRun.id)
            .where(WorkflowRun.run_group_id == run_group_id)
            .where(WorkflowRun.status == RunStatus.FAILED)
            .subquery()
        )

        # Update non-completed run steps to PENDING
        q1 = (
            update(RunStep)
            .where(RunStep.workflow_run_id.in_(select(failed_runs_subq.c.id)))
            .where(RunStep.status != RunStatus.COMPLETED)
            .values(status=RunStatus.PENDING, retry=0)
        )
        result1 = await session.exec(q1)
        reset_steps = result1.rowcount  # type: ignore

        # Update workflow runs to RUNNING
        q2 = (
            update(WorkflowRun)
            .where(WorkflowRun.run_group_id == run_group_id)
            .where(WorkflowRun.status == RunStatus.FAILED)
            .values(status=RunStatus.RUNNING)
        )
        result2 = await session.exec(q2)
        reset_runs = result2.rowcount  # type: ignore

        await session.commit()

        logger.info(f"Reset {reset_steps} steps and {reset_runs} runs for run group {run_group_id}")


_SOFT_STEP_STATUSES = (RunStatus.FAILED, RunStatus.CANCELLED)


async def reset_failed(
    run_group_id: int | None = None,
    hard: bool = False,
) -> None:
    """
    Reset steps and workflow runs back to PENDING.

    By default, resets FAILED steps plus any CANCELLED steps that were
    cascaded from a failed sibling, and FAILED workflow runs. When
    *hard* is ``True``, every non-COMPLETED step is set to PENDING with
    retry count and worker_id cleared, and **every run that owns such a
    step** is also set to PENDING — including runs that were spuriously
    promoted to COMPLETED while children were still pending.

    Parameters
    ----------
    run_group_id : int | None
        If specified, only reset within this run group.
        If None, reset globally.
    hard : bool
        If True, reset every non-COMPLETED step and the runs containing
        them.
    """
    # Soft mode resets both FAILED steps and the CANCELLED siblings that
    # were marked by cancel_pending_steps — otherwise restarted runs hit
    # "can't change from CANCELLED to RUNNING". The CANCELLED siblings
    # set is captured in the module-level ``_SOFT_STEP_STATUSES``
    # tuple used below.
    async with get_session() as session:
        reset_values = {
            "status": RunStatus.PENDING,
            "retry": 0,
            "worker_id": None,
        }
        if hard:
            # Resolve the set of runs first, then drive both the step
            # and run updates from that same set. This guarantees we
            # never reset a step without also resetting its parent run.
            affected_q = select(RunStep.workflow_run_id).where(
                RunStep.status != RunStatus.COMPLETED,
            )
            if run_group_id is not None:
                affected_q = affected_q.join(
                    WorkflowRun,
                    WorkflowRun.id == RunStep.workflow_run_id,
                ).where(WorkflowRun.run_group_id == run_group_id)
            affected_run_ids = list(
                (await session.exec(affected_q.distinct())).all(),
            )
            if not affected_run_ids:
                scope = f"run group {run_group_id}" if run_group_id else "all"
                logger.info(f"reset_failed: nothing to reset ({scope}, hard)")
                return
            q1 = (
                update(RunStep)
                .where(RunStep.workflow_run_id.in_(affected_run_ids))
                .where(RunStep.status != RunStatus.COMPLETED)
                .values(**reset_values)
            )
            q2 = update(WorkflowRun).where(WorkflowRun.id.in_(affected_run_ids)).values(status=RunStatus.PENDING)
        elif run_group_id is not None:
            runs_subq = (
                select(WorkflowRun.id)
                .where(WorkflowRun.run_group_id == run_group_id)
                .where(WorkflowRun.status == RunStatus.FAILED)
                .subquery()
            )
            q1 = (
                update(RunStep)
                .where(RunStep.workflow_run_id.in_(select(runs_subq.c.id)))
                .where(RunStep.status.in_(_SOFT_STEP_STATUSES))
                .values(**reset_values)
            )
            q2 = (
                update(WorkflowRun)
                .where(WorkflowRun.run_group_id == run_group_id)
                .where(WorkflowRun.status == RunStatus.FAILED)
                .values(status=RunStatus.PENDING)
            )
        else:
            q1 = update(RunStep).where(RunStep.status.in_(_SOFT_STEP_STATUSES)).values(**reset_values)
            q2 = update(WorkflowRun).where(WorkflowRun.status == RunStatus.FAILED).values(status=RunStatus.PENDING)

        result1 = await session.exec(q1)
        reset_steps = result1.rowcount  # type: ignore
        result2 = await session.exec(q2)
        reset_runs = result2.rowcount  # type: ignore

        await session.commit()

        mode = "hard" if hard else "soft"
        scope = f"run group {run_group_id}" if run_group_id else "all"
        logger.info(f"reset_failed: {reset_steps} steps and {reset_runs} runs reset ({scope}, {mode})")


async def get_running_steps_enriched() -> list[dict]:
    """
    Get all run steps in RUNNING status with enriched context.

    Joins RunStep with WorkflowRun, DocumentURI, and RunGroup to provide
    full context for each running step.

    Returns
    -------
    list[dict]
        List of dicts with keys: workflow_run_id, doc_hash, doc_uri,
        run_group_id, param_definition_id, step_type, start_date,
        elapsed_seconds
    """
    async with get_session() as session:
        q = (
            select(
                RunStep.workflow_run_id,
                WorkflowRun.doc_id.label("doc_hash"),
                DocumentURI.uri.label("doc_uri"),
                WorkflowRun.run_group_id,
                RunGroup.param_definition_id,
                RunStep.step_type,
                RunStep.start_date,
            )
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .join(RunGroup, RunGroup.id == WorkflowRun.run_group_id)
            .outerjoin(
                DocumentURI,
                (DocumentURI.doc_hash == WorkflowRun.doc_id) & (DocumentURI.batch_id == WorkflowRun.batch_id),
            )
            .where(RunStep.status == RunStatus.RUNNING)
        )
        result = await session.exec(q)
        rows = result.all()

        now_aware = datetime.datetime.now(datetime.UTC)
        now_naive = now_aware.replace(tzinfo=None)
        enriched = []
        for row in rows:
            elapsed = None
            if row.start_date:
                sd = row.start_date
                now = now_aware if sd.tzinfo else now_naive
                elapsed = (now - sd).total_seconds()
            enriched.append(
                {
                    "workflow_run_id": row.workflow_run_id,
                    "doc_hash": row.doc_hash,
                    "doc_uri": row.doc_uri,
                    "run_group_id": row.run_group_id,
                    "param_definition_id": row.param_definition_id,
                    "step_type": row.step_type,
                    "start_date": row.start_date,
                    "elapsed_seconds": elapsed,
                }
            )
        return enriched


INTERVAL_MAP = {
    "minute": datetime.timedelta(minutes=1),
    "hour": datetime.timedelta(hours=1),
    "day": datetime.timedelta(days=1),
    "week": datetime.timedelta(weeks=1),
}


async def get_recent_steps(
    interval: str = "minute",
    status: RunStatus | None = None,
) -> list[dict]:
    """
    Get run steps with a status_date within a specified interval.

    Joins RunStep with WorkflowRun, DocumentURI, and RunGroup.

    Parameters
    ----------
    interval : str
        One of "minute", "hour", "day", "week"
    status : RunStatus | None
        Optional status filter

    Returns
    -------
    list[dict]
        Enriched step data

    Raises
    ------
    ValueError
        If interval is not a recognized value
    """
    if interval not in INTERVAL_MAP:
        raise ValueError(f"Invalid interval '{interval}'. Must be one of: {', '.join(INTERVAL_MAP)}")

    # Use naive datetime for cross-database compatibility (SQLite stores naive)
    cutoff = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - INTERVAL_MAP[interval]

    async with get_session() as session:
        q = (
            select(
                RunStep.step_type,
                RunStep.workflow_run_id,
                WorkflowRun.doc_id.label("doc_hash"),
                DocumentURI.uri.label("doc_uri"),
                WorkflowRun.run_group_id,
                RunGroup.param_definition_id,
                RunStep.start_date,
                RunStep.status_date,
                RunStep.status,
                RunStep.retry,
                RunStep.status_message,
            )
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .join(RunGroup, RunGroup.id == WorkflowRun.run_group_id)
            .outerjoin(
                DocumentURI,
                (DocumentURI.doc_hash == WorkflowRun.doc_id) & (DocumentURI.batch_id == WorkflowRun.batch_id),
            )
            .where(RunStep.status_date >= cutoff)
        )
        if status is not None:
            q = q.where(RunStep.status == status)

        q = q.order_by(RunStep.status_date.desc())
        result = await session.exec(q)
        rows = result.all()

        now_aware = datetime.datetime.now(datetime.UTC)
        now_naive = now_aware.replace(tzinfo=None)
        enriched = []
        for row in rows:
            elapsed = None
            if row.start_date:
                sd = row.start_date
                now = now_aware if sd.tzinfo else now_naive
                elapsed = (now - sd).total_seconds()
            enriched.append(
                {
                    "step_type": row.step_type,
                    "workflow_run_id": row.workflow_run_id,
                    "doc_hash": row.doc_hash,
                    "doc_uri": row.doc_uri,
                    "run_group_id": row.run_group_id,
                    "param_definition_id": row.param_definition_id,
                    "start_date": row.start_date,
                    "elapsed_seconds": elapsed,
                    "retry": row.retry,
                    "status": row.status,
                    "status_message": row.status_message,
                }
            )
        return enriched


async def get_run_group_details(run_group_id: int) -> list[dict]:  # pragma: no cover
    """
    Get aggregated step details for a run group.

    **PostgreSQL only** - Uses PostgreSQL-specific JSONB functions
    via SQLAlchemy for type safety and ORM benefits. Body excluded
    from unit coverage; covered by integration tests on Postgres.

    Parameters
    ----------
    run_group_id : int
        The run group ID

    Returns
    -------
    list[dict]
        Rows with keys: name, param_definition_id, step_type,
        status, count, pages

    Raises
    ------
    RuntimeError
        If database is not PostgreSQL
    """
    from soliplex.ingester.lib.config import get_settings

    settings = get_settings()
    doc_db_url = (
        settings.doc_db_url.get_secret_value() if hasattr(settings.doc_db_url, "get_secret_value") else settings.doc_db_url
    )
    if "postgresql" not in doc_db_url:
        raise RuntimeError("get_run_group_details requires PostgreSQL (uses PostgreSQL-specific functions)")

    async with get_session() as session:
        q = (
            select(
                DocumentBatch.name,
                RunGroup.param_definition_id,
                RunStep.step_type,
                RunStep.status,
                func.count(literal_column("1")).label("count"),
                func.sum(
                    cast(
                        func.jsonb_extract_path_text(cast(Document.doc_meta, JSONB), "page_count"),
                        Integer,
                    )
                ).label("pages"),
            )
            .select_from(RunStep)
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .join(DocumentBatch, DocumentBatch.id == WorkflowRun.batch_id)
            .join(Document, Document.hash == WorkflowRun.doc_id)
            .join(RunGroup, RunGroup.id == WorkflowRun.run_group_id)
            .where(RunGroup.id == run_group_id)
            .where(RunStep.status != RunStatus.PENDING)
            .group_by(
                DocumentBatch.name,
                RunGroup.param_definition_id,
                RunStep.step_type,
                RunStep.status,
            )
            .order_by(
                DocumentBatch.name,
                RunStep.step_type,
                RunStep.status,
            )
        )

        result = await session.exec(q)
        return [dict(row._mapping) for row in result.all()]


async def get_workflow_runs_for_group_with_doc_info(
    run_group_id: int,
    status_filter: str | None = None,
) -> dict:
    """
    Get workflow runs for a run group enriched with document info.

    Combines get_workflow_runs_for_group() and get_document_info_for_workflow_runs(),
    with an optional status filter applied before enrichment.

    Args:
        run_group_id: The run group ID to query
        status_filter: Optional status string to filter runs (e.g. "FAILED", "COMPLETED")

    Returns:
        Dict mapping doc_id -> DocumentInfo for filtered workflow runs
    """
    runs = await get_workflow_runs_for_group(run_group_id)
    if status_filter:
        runs = [r for r in runs if r.status.value == status_filter.upper()]
    return await get_document_info_for_workflow_runs(runs)


# ====================================================================
# wf refactor: atomic claim, lease tokens, run-status recompute,
# resource locks, worker heartbeat / reaping.
#
# These functions are the persistence seam for the new Worker
# orchestrator. All concurrency invariants live here at the SQL
# layer; runner.py is pure orchestration over these.
# ====================================================================


def _utc_now() -> datetime.datetime:
    """Naive UTC. SQLite stores datetimes without timezone, so we
    consistently strip tzinfo at the boundary to keep comparisons
    well-defined under both backends."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


_CLAIMABLE_STEP_STATUSES = (RunStatus.PENDING, RunStatus.ERROR)
_NONTERMINAL_RUN_STATUSES = (
    RunStatus.PENDING,
    RunStatus.RUNNING,
    RunStatus.ERROR,
)

# Sentinel expires_at for WORKER-held ``ResourceLock`` rows. Worker
# locks have no functional TTL — they're cleared by complete_step,
# error_step, release_step, or reap_dead_workers — but the row must
# still satisfy ``subq_locked``'s ``expires_at > now`` predicate and
# stay clear of the opportunistic ``WHERE expires_at < now`` sweep.
# CLI/web/lifecycle holders going through ``acquire_resource_lock``
# continue to use real TTLs.
_WORKER_LOCK_EXPIRES = datetime.datetime(9999, 12, 31)


async def claim_next_step(
    worker_id: str,
    lease_token: str,
    allowed_types: list[WorkflowStepType] | None = None,
    batch_id: int | None = None,
    holder_meta: dict[str, str] | None = None,
) -> RunStep | None:
    """Atomically claim the next eligible step for *worker_id*.

    Replaces ``get_runnable_steps + set_step_status(RUNNING)`` with
    a single transaction. The caller mints *lease_token* (UUID4
    typically); subsequent ``complete_step`` / ``error_step`` /
    ``release_step`` calls must present the same token, so a worker
    that was reaped between claim and write cannot finalize the
    step on top of a fresh claimant.

    Eligibility predicates mirror the original ``get_runnable_steps``
    plus two additions:

    1. ``allowed_types`` — when non-empty, only steps whose
       ``step_type`` is in the list are claimable. Used by the
       per-type consumer pools to bound concurrency for resource-
       sensitive types like ``parse`` / ``save_to_rag`` without
       claim-then-release churn.
    2. ``resource_key`` — when set on the step, the step is only
       claimable if no live :class:`ResourceLock` row holds that
       key. Prevents claiming a ``save_to_rag`` step whose RAG-DB
       is currently being vacuumed by the web/CLI/lifecycle path.

    When the claimed step has a ``resource_key``, the corresponding
    :class:`ResourceLock` row is INSERTed in the **same transaction**
    as the claim. Two concurrent claims targeting the same
    ``resource_key`` (e.g. STORE steps from different workflow runs
    pointing at the same LanceDB) serialize at the unique-PK
    constraint on ``resourcelock.resource_key``: the loser gets
    ``IntegrityError``, the whole claim rolls back, and ``None`` is
    returned so the consumer can pick a different candidate next
    poll. This closes the claim/acquire race that READ COMMITTED
    isolation leaves open between two ``UPDATE-FROM-SELECT``
    transactions.

    Returns
    -------
    RunStep | None
        The newly-claimed step (in-memory, expunged from the
        session), or ``None`` if no work was available or the
        atomic resource-lock insert lost a race.
    """
    now = _utc_now()
    async with get_session() as session:
        # Subquery 1: minimum step number per eligible workflow run.
        subq_min_step = (
            select(
                RunStep.workflow_run_id,
                func.min(RunStep.workflow_step_number).label("min_step"),
            )
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .where(RunStep.retry < RunStep.retries)
            .where(RunStep.status.in_(_CLAIMABLE_STEP_STATUSES))
            .where(WorkflowRun.status.in_(_NONTERMINAL_RUN_STATUSES))
        )
        if batch_id is not None:
            subq_min_step = subq_min_step.where(WorkflowRun.batch_id == batch_id)
        subq_min_step = subq_min_step.group_by(RunStep.workflow_run_id).subquery()

        # Subquery 2: workflow runs that already have a RUNNING step.
        subq_running = select(RunStep.workflow_run_id).where(RunStep.status == RunStatus.RUNNING).distinct().subquery()

        # Subquery 3: resource_keys currently locked.
        subq_locked = select(ResourceLock.resource_key).where(ResourceLock.expires_at > now).subquery()

        # Subquery 4: resource_keys already held by a currently-RUNNING
        # step. Closes the race window between a claim transaction
        # committing (step → RUNNING) and the worker's separate
        # ``acquire_resource_lock`` transaction committing: as soon as
        # worker A's claim commits with status=RUNNING and
        # resource_key=X, any concurrent claim from another consumer
        # sees A here and excludes other resource_key=X candidates,
        # so they don't get claimed-then-released.
        subq_running_rk = (
            select(RunStep.resource_key)
            .where(RunStep.status == RunStatus.RUNNING)
            .where(RunStep.resource_key.is_not(None))
            .distinct()
            .subquery()
        )

        q = (
            select(RunStep)
            .where(
                tuple_(RunStep.workflow_run_id, RunStep.workflow_step_number).in_(
                    select(subq_min_step.c.workflow_run_id, subq_min_step.c.min_step),
                )
            )
            .where(RunStep.status.in_(_CLAIMABLE_STEP_STATUSES))
            .where(RunStep.workflow_run_id.not_in(select(subq_running.c.workflow_run_id)))
            .where(
                or_(
                    RunStep.resource_key.is_(None),
                    and_(
                        RunStep.resource_key.not_in(select(subq_locked.c.resource_key)),
                        RunStep.resource_key.not_in(select(subq_running_rk.c.resource_key)),
                    ),
                )
            )
            .order_by(
                RunStep.priority.desc(),
                RunStep.retry,
                RunStep.created_date,
                RunStep.workflow_step_number,
            )
            .limit(1)
        )
        if allowed_types:
            q = q.where(RunStep.step_type.in_(allowed_types))

        # SKIP LOCKED is honored on Postgres and silently ignored on
        # SQLite (which serializes writers at the WAL level anyway).
        q = q.with_for_update(skip_locked=True)

        result = await session.exec(q)
        step = result.first()
        if step is None:
            return None

        step.status = RunStatus.RUNNING
        step.worker_id = worker_id
        step.lease_token = lease_token
        step.start_date = now
        step.status_date = now
        step.status_message = None
        step.status_meta = {}
        session.add(step)
        await session.flush()

        # Atomically take the cross-subsystem resource lock in the
        # SAME transaction as the claim. The unique constraint on
        # ``resourcelock.resource_key`` serializes concurrent claims
        # for the same key: the loser gets IntegrityError → rollback
        # → return None so the consumer can pick a different
        # candidate or back off. The opportunistic sweep is in line
        # with what ``acquire_resource_lock`` does — keeps the table
        # bounded under churn.
        if step.resource_key:
            await session.exec(
                delete(ResourceLock).where(ResourceLock.expires_at < now),
            )
            session.add(
                ResourceLock(
                    resource_key=step.resource_key,
                    holder_id=lease_token,
                    holder_kind=ResourceLockKind.WORKER,
                    step_id=step.id,
                    acquired_at=now,
                    expires_at=_WORKER_LOCK_EXPIRES,
                    holder_meta=holder_meta or {"worker_id": worker_id},
                ),
            )
            try:
                await session.flush()
            except IntegrityError:
                # Another claim won the race to insert the lock. Roll
                # the whole transaction back so the step stays
                # PENDING; let the caller treat this as ``no work
                # available`` and back off.
                await session.rollback()
                return None

        # Promote the workflow run to RUNNING if it is still PENDING /
        # ERROR. We do this in the same transaction as the claim so
        # there is never a window where a RUNNING step's parent run
        # looks PENDING.
        wf_q = select(WorkflowRun).where(WorkflowRun.id == step.workflow_run_id)
        wf_rs = await session.exec(wf_q)
        wf = wf_rs.first()
        if wf is not None and wf.status != RunStatus.RUNNING:
            wf.status = RunStatus.RUNNING
            wf.status_date = now
            session.add(wf)
            await session.flush()

        # Detach before commit so the caller can read attributes
        # without tripping SQLAlchemy's expire-on-commit refresh.
        session.expunge(step)
        await session.commit()
        return step


async def complete_step(
    step_id: int,
    lease_token: str,
    message: str | None = "success",
    meta: dict[str, str] | None = None,
) -> bool:
    """Mark a step COMPLETED, gated on the lease token.

    Returns True iff the row was updated. False means the lease
    was lost (worker reaped or step released by another path) —
    the caller must treat this as "step is not ours anymore" and
    *not* assume the work succeeded from the system's point of
    view.

    The matching :class:`ResourceLock` row, if any, is deleted in
    the same transaction so a follow-on consumer can immediately
    claim a step that was waiting on this DB.
    """
    now = _utc_now()
    async with get_session() as session:
        stmt = (
            update(RunStep)
            .where(RunStep.id == step_id)
            .where(RunStep.lease_token == lease_token)
            .where(RunStep.status == RunStatus.RUNNING)
            .values(
                status=RunStatus.COMPLETED,
                status_date=now,
                completed_date=now,
                status_message=message,
                status_meta=meta or {},
                lease_token=None,
            )
        )
        result = await session.exec(stmt)
        rows = result.rowcount  # type: ignore
        if rows == 0:
            await session.commit()
            return False

        # Drop any resource lock this step held.
        await session.exec(
            delete(ResourceLock).where(ResourceLock.holder_id == lease_token),
        )
        await session.commit()
        return True


async def error_step(
    step_id: int,
    lease_token: str,
    message: str,
    meta: dict[str, str] | None = None,
) -> RunStatus | None:
    """Mark a step ERROR (or FAILED if retries exhausted), gated on
    the lease token.

    Returns the resulting :class:`RunStatus` (``ERROR`` or
    ``FAILED``), or ``None`` if the lease was lost. ``FAILED`` also
    cascades pending sibling steps to ``CANCELLED`` so the rest of
    the workflow run does not silently sit eligible.
    """
    now = _utc_now()
    async with get_session() as session:
        q = select(RunStep).where(RunStep.id == step_id)
        rs = await session.exec(q)
        step = rs.first()
        if step is None:
            return None
        if step.lease_token != lease_token or step.status != RunStatus.RUNNING:
            # Lost the lease (e.g. reaped while we ran).
            return None

        new_retry = step.retry + 1
        new_status = RunStatus.FAILED if new_retry >= step.retries else RunStatus.ERROR

        step.status = new_status
        step.retry = new_retry
        step.status_date = now
        step.status_message = message
        step.status_meta = meta or {}
        step.lease_token = None
        session.add(step)
        await session.flush()

        workflow_run_id = step.workflow_run_id
        cancelled = 0
        if new_status == RunStatus.FAILED:
            cancelled = await cancel_pending_steps(workflow_run_id, session)

        await session.exec(
            delete(ResourceLock).where(ResourceLock.holder_id == lease_token),
        )
        await session.commit()

        if cancelled:
            logger.info(
                f"cancelled {cancelled} pending steps for run {workflow_run_id}",
            )
        return new_status


async def release_step(step_id: int, lease_token: str) -> bool:
    """Release a still-RUNNING step back to PENDING, gated on the
    lease token. Used by graceful shutdown so in-flight work
    becomes immediately re-claimable instead of waiting for the
    worker-checkin timeout to elapse.

    Returns True iff the lease still matched. ``retry`` is **not**
    incremented — this is a cooperative release, not a failure.
    """
    now = _utc_now()
    async with get_session() as session:
        stmt = (
            update(RunStep)
            .where(RunStep.id == step_id)
            .where(RunStep.lease_token == lease_token)
            .where(RunStep.status == RunStatus.RUNNING)
            .values(
                status=RunStatus.PENDING,
                worker_id=None,
                lease_token=None,
                status_date=now,
                status_message="released by worker shutdown",
            )
        )
        result = await session.exec(stmt)
        await session.exec(
            delete(ResourceLock).where(ResourceLock.holder_id == lease_token),
        )
        await session.commit()
        return result.rowcount > 0  # type: ignore


async def recompute_run_status(workflow_run_id: int) -> RunStatus | None:
    """Recompute ``WorkflowRun.status`` from its steps.

    Idempotent — derives the answer from current step counts rather
    than applying a delta. Run in its own transaction so a failure
    here cannot poison the step transition that triggered it.

    Rules:

    * any FAILED step → run FAILED
    * all steps COMPLETED → run COMPLETED
    * any RUNNING/ERROR step → run RUNNING
    * otherwise → run PENDING
    """
    async with get_session() as session:
        q = (
            select(RunStep.status, func.count(RunStep.id))
            .where(RunStep.workflow_run_id == workflow_run_id)
            .group_by(RunStep.status)
        )
        rs = await session.exec(q)
        counts: dict[RunStatus, int] = dict(rs.all())  # type: ignore

        if not counts:
            return None

        total = sum(counts.values())
        completed = counts.get(RunStatus.COMPLETED, 0)
        failed = counts.get(RunStatus.FAILED, 0)
        cancelled = counts.get(RunStatus.CANCELLED, 0)
        running = counts.get(RunStatus.RUNNING, 0)
        errored = counts.get(RunStatus.ERROR, 0)

        if failed > 0:
            new_status = RunStatus.FAILED
        elif completed + cancelled == total:
            new_status = RunStatus.COMPLETED
        elif running + errored > 0 or completed > 0:
            new_status = RunStatus.RUNNING
        else:
            new_status = RunStatus.PENDING

        wf_q = select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
        wf_rs = await session.exec(wf_q)
        wf = wf_rs.first()
        if wf is None:
            return None

        if wf.status == new_status:
            return new_status

        now = _utc_now()
        wf.status = new_status
        wf.status_date = now
        if new_status in (RunStatus.COMPLETED, RunStatus.FAILED):
            wf.completed_date = now
        session.add(wf)
        await session.commit()
        return new_status


async def try_complete_run_group(run_group_id: int) -> RunStatus | None:
    """Atomically transition a run group to COMPLETED or FAILED iff
    no steps in the group are still pending or retryable.

    The transition is keyed on the same predicate that callers use
    to decide whether to fire ``GROUP_END``, so under concurrency
    only the worker whose update affected a row should fire the
    event. Returns the new group status, or ``None`` if the group
    is not yet finished.
    """
    now = _utc_now()
    async with get_session() as session:
        # Are there any non-terminal steps left?
        pending_q = (
            select(func.count(RunStep.id))
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .where(WorkflowRun.run_group_id == run_group_id)
            .where(
                RunStep.status.in_(
                    (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.ERROR),
                ),
            )
        )
        pending = (await session.exec(pending_q)).first() or 0
        if pending > 0:
            return None

        # Any FAILED steps determine group status.
        failed_q = (
            select(func.count(RunStep.id))
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .where(WorkflowRun.run_group_id == run_group_id)
            .where(RunStep.status == RunStatus.FAILED)
        )
        failed = (await session.exec(failed_q)).first() or 0
        new_status = RunStatus.FAILED if failed > 0 else RunStatus.COMPLETED

        stmt = (
            update(RunGroup)
            .where(RunGroup.id == run_group_id)
            .where(
                RunGroup.status.not_in((RunStatus.COMPLETED, RunStatus.FAILED)),
            )
            .values(
                status=new_status,
                status_date=now,
                completed_date=now,
            )
        )
        result = await session.exec(stmt)
        await session.commit()
        if result.rowcount == 0:  # type: ignore
            # Lost the race — another worker fired GROUP_END.
            return None
        return new_status


# ----- worker heartbeat / reaper --------------------------------------


async def worker_heartbeat(worker_id: str) -> None:
    """Insert or update a worker's checkin row to *now*."""
    now = _utc_now()
    async with get_session() as session:
        q = select(WorkerCheckin).where(WorkerCheckin.id == worker_id)
        rs = await session.exec(q)
        res = rs.first()
        if res is not None:
            res.last_checkin = now
            session.add(res)
        else:
            session.add(
                WorkerCheckin(
                    id=worker_id,
                    last_checkin=now,
                    first_checkin=now,
                ),
            )
        await session.commit()


async def delete_worker_checkin(worker_id: str) -> None:
    """Remove a worker's checkin row.

    Called by graceful shutdown so siblings see the departure
    immediately and don't wait the full ``worker_checkin_timeout``.
    """
    async with get_session() as session:
        await session.exec(
            delete(WorkerCheckin).where(WorkerCheckin.id == worker_id),
        )
        await session.commit()


async def reap_dead_workers(
    my_worker_id: str,
    threshold_seconds: int,
) -> tuple[list[str], list[int]]:
    """Sweep dead workers and reset any RUNNING steps they held.

    *my_worker_id* is **always** excluded from the sweep — a worker
    can never reap itself, eliminating the self-reaping race where
    a stalled checkin loop would let the worker mistakenly reset
    its own in-flight steps.

    Reset semantics:

    * status → ``PENDING``
    * ``worker_id`` → ``NULL``
    * ``lease_token`` → ``NULL`` (so any straggling write from the
      dead worker becomes a no-op via the lease gate)
    * ``status_message`` → an audit string

    Resource locks held by reaped workers are deleted alongside.

    Returns
    -------
    (reaped_worker_ids, reset_step_ids)
    """
    cutoff = _utc_now() - datetime.timedelta(seconds=threshold_seconds)
    reaped: list[str] = []
    reset_ids: list[int] = []
    async with get_session() as session:
        q = select(WorkerCheckin).where(WorkerCheckin.last_checkin < cutoff).where(WorkerCheckin.id != my_worker_id)
        rs = await session.exec(q)
        dead = list(rs.all())
        if not dead:
            return [], []

        dead_ids = [w.id for w in dead]
        reaped = list(dead_ids)

        # Find their RUNNING steps so we can record which were reset.
        steps_q = (
            select(RunStep.id, RunStep.lease_token)
            .where(RunStep.worker_id.in_(dead_ids))
            .where(RunStep.status == RunStatus.RUNNING)
        )
        step_rs = await session.exec(steps_q)
        rows = list(step_rs.all())
        reset_ids = [r[0] for r in rows]
        dead_lease_tokens = [r[1] for r in rows if r[1] is not None]

        if reset_ids:
            await session.exec(
                update(RunStep)
                .where(RunStep.id.in_(reset_ids))
                .values(
                    status=RunStatus.PENDING,
                    worker_id=None,
                    lease_token=None,
                    status_date=_utc_now(),
                    status_message="reset by dead-worker reaper",
                ),
            )

        if dead_lease_tokens:
            await session.exec(
                delete(ResourceLock).where(
                    ResourceLock.holder_id.in_(dead_lease_tokens),
                ),
            )

        # Drop the dead checkin rows.
        await session.exec(
            delete(WorkerCheckin).where(WorkerCheckin.id.in_(dead_ids)),
        )
        await session.commit()
    return reaped, reset_ids


# ----- ResourceLock ---------------------------------------------------


async def acquire_resource_lock(
    resource_key: str,
    holder_id: str,
    holder_kind: ResourceLockKind,
    step_id: int | None = None,
    ttl_seconds: int = 300,
    holder_meta: dict[str, str] | None = None,
) -> bool:
    """Try to acquire a named resource lock.

    Returns True iff acquired. Implemented as ``INSERT ... DELETE
    expired ... INSERT again`` to avoid a hard dependency on
    ``ON CONFLICT`` semantics: we sweep first, then attempt the
    insert under the unique primary key. Concurrent acquirers
    serialize at the row level.

    Idempotent for the current holder: calling with a ``holder_id``
    that already owns the lock refreshes the TTL and returns True.
    Workers that took the lock atomically at claim time can pass
    through their ``_run_step`` acquire path as a defensive
    refresh without needing to special-case the "already mine"
    state.
    """
    now = _utc_now()
    expires = now + datetime.timedelta(seconds=ttl_seconds)
    async with get_session() as session:
        # Opportunistic sweep: an expired holder shouldn't block.
        await session.exec(
            delete(ResourceLock).where(ResourceLock.expires_at < now),
        )
        # Is the lock free?
        q = select(ResourceLock).where(ResourceLock.resource_key == resource_key)
        rs = await session.exec(q)
        existing = rs.first()
        if existing is not None:
            if existing.holder_id == holder_id:
                # We already hold the lock — refresh TTL and succeed.
                existing.expires_at = expires
                session.add(existing)
                await session.commit()
                return True
            await session.commit()
            return False
        session.add(
            ResourceLock(
                resource_key=resource_key,
                holder_id=holder_id,
                holder_kind=holder_kind,
                step_id=step_id,
                acquired_at=now,
                expires_at=expires,
                holder_meta=holder_meta or {},
            ),
        )
        try:
            await session.commit()
        except Exception:
            # Lost the race to a concurrent acquirer.
            await session.rollback()
            return False
        return True


async def refresh_resource_lock(
    resource_key: str,
    holder_id: str,
    ttl_seconds: int = 300,
) -> bool:
    """Extend the TTL of a held resource lock. Returns True iff the
    holder still owned it (0 rows updated → lock was lost / swept,
    holder must abort)."""
    expires = _utc_now() + datetime.timedelta(seconds=ttl_seconds)
    async with get_session() as session:
        stmt = (
            update(ResourceLock)
            .where(ResourceLock.resource_key == resource_key)
            .where(ResourceLock.holder_id == holder_id)
            .values(expires_at=expires)
        )
        result = await session.exec(stmt)
        await session.commit()
        return result.rowcount > 0  # type: ignore


async def release_resource_lock(
    resource_key: str,
    holder_id: str,
) -> bool:
    """Release a held resource lock. Idempotent — releasing a lock
    you don't hold is silently a no-op."""
    async with get_session() as session:
        stmt = (
            delete(ResourceLock).where(ResourceLock.resource_key == resource_key).where(ResourceLock.holder_id == holder_id)
        )
        result = await session.exec(stmt)
        await session.commit()
        return result.rowcount > 0  # type: ignore


async def force_release_resource_lock(resource_key: str) -> bool:
    """Drop a resource lock regardless of holder. Used by the
    ``si-diag --force`` path; emits an audit log line."""
    async with get_session() as session:
        q = select(ResourceLock).where(ResourceLock.resource_key == resource_key)
        rs = await session.exec(q)
        existing = rs.first()
        if existing is None:
            return False
        logger.warning(
            "force-releasing resource lock %s held by %s/%s since %s",
            resource_key,
            existing.holder_id,
            existing.holder_kind,
            existing.acquired_at,
        )
        await session.exec(
            delete(ResourceLock).where(ResourceLock.resource_key == resource_key),
        )
        await session.commit()
        return True


async def get_resource_lock(resource_key: str) -> ResourceLock | None:
    """Read the current holder of *resource_key* (or None)."""
    async with get_session() as session:
        q = select(ResourceLock).where(ResourceLock.resource_key == resource_key)
        rs = await session.exec(q)
        existing = rs.first()
        if existing is not None:
            session.expunge(existing)
        return existing


async def sweep_expired_resource_locks() -> int:
    """Delete every expired lock row. Returns the number deleted."""
    now = _utc_now()
    async with get_session() as session:
        stmt = delete(ResourceLock).where(ResourceLock.expires_at < now)
        result = await session.exec(stmt)
        await session.commit()
        return result.rowcount  # type: ignore


# ----- Worker orchestration helpers -----------------------------------


async def list_active_resource_locks_for_holder(holder_id: str) -> list[str]:
    """Return resource keys currently held by *holder_id*."""
    async with get_session() as session:
        q = select(ResourceLock.resource_key).where(
            ResourceLock.holder_id == holder_id,
        )
        rs = await session.exec(q)
        return list(rs.all())
