"""Unit tests for workflow.parse_document and workflow.split_parse_document.

These tests monkeypatch the bytes-based helpers (``parse_bytes`` /
``split_parse_bytes``) so the storage and workflow plumbing can be
exercised without a docling server. The functional tests in
``tests/functional/test_workflow.py`` cover the real-docling path; these
unit tests cover branches that were previously unreachable.
"""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from soliplex.ingester.lib import workflow
from soliplex.ingester.lib.models import ArtifactType
from soliplex.ingester.lib.models import WorkflowStepType


class _FakeOp:
    """In-memory artifact operator. Captures writes and supports exists/delete.

    Set ``_exists_override`` to pin the return value of ``exists()``;
    individual tests can also replace ``self.exists`` directly with an
    ``AsyncMock(side_effect=[...])`` to simulate transient missing artifacts.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.writes: list[tuple[str, bytes]] = []
        self.deletes: list[str] = []
        self._exists_override: bool | None = None

    async def write(self, key: str, content: bytes) -> None:
        self.writes.append((key, content))
        self.store[key] = content

    async def read(self, key: str) -> bytes:
        return self.store[key]

    async def exists(self, key: str) -> bool:
        if self._exists_override is not None:
            return self._exists_override
        return key in self.store

    async def delete(self, key: str) -> None:
        self.deletes.append(key)
        self.store.pop(key, None)

    def force_exists(self, value: bool | None) -> None:
        self._exists_override = value


def _patch_doc_ops(monkeypatch, *, doc=None, doc_uris=None, doc_bytes=b"FAKE") -> dict:
    """Patch the doc_ops calls used by parse_document / split_parse_document."""
    if doc is None:
        doc = MagicMock(mime_type="application/pdf", doc_meta={}, hash="abc123")
    if doc_uris is None:
        doc_uris = [MagicMock(uri="document.pdf")]
    history = AsyncMock()
    get_doc = AsyncMock(return_value=doc)
    get_uris = AsyncMock(return_value=doc_uris)
    read_bytes = AsyncMock(return_value=doc_bytes)
    monkeypatch.setattr(workflow.doc_ops, "get_document", get_doc)
    monkeypatch.setattr(workflow.doc_ops, "get_document_uris_by_hash", get_uris)
    monkeypatch.setattr(workflow.doc_ops, "read_doc_bytes", read_bytes)
    monkeypatch.setattr(workflow.doc_ops, "add_history_for_hash", history)
    return {
        "doc": doc,
        "doc_uris": doc_uris,
        "get_doc": get_doc,
        "get_uris": get_uris,
        "read_bytes": read_bytes,
        "history": history,
    }


def _install_op_factory(monkeypatch) -> dict[tuple, _FakeOp]:
    """Patch ``workflow._get_op`` to return per-(step,artifact) FakeOps.

    Returns the underlying dict so tests can pre-seed or inspect ops.
    """
    ops: dict[tuple, _FakeOp] = {}

    async def _get_op(workflow_run_id, step_type, artifact_type=None):  # noqa: ARG001
        return ops.setdefault((step_type, artifact_type), _FakeOp())

    monkeypatch.setattr(workflow, "_get_op", _get_op)
    return ops


def _make_run_and_config(config_json: dict | None = None):
    return MagicMock(id=42), MagicMock(config_json=config_json or {})


# =====================================================================
# parse_document
# =====================================================================


@pytest.mark.asyncio
async def test_parse_document_writes_artifacts_and_records_history(monkeypatch):
    """Happy path: parse_bytes returns canned bytes → both artifacts written, history appended."""
    mocks = _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    parse_bytes_mock = AsyncMock(return_value=(b"# md", b'{"j":1}'))
    monkeypatch.setattr(workflow, "parse_bytes", parse_bytes_mock)
    wf_run, sc = _make_run_and_config({"k": "v"})

    await workflow.parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
    )

    parse_bytes_mock.assert_awaited_once_with(b"FAKE", "application/pdf", "document.pdf", {"k": "v"})
    json_op = ops[(WorkflowStepType.PARSE, ArtifactType.PARSED_JSON)]
    md_op = ops[(WorkflowStepType.PARSE, ArtifactType.PARSED_MD)]
    assert json_op.store == {"abc": b'{"j":1}'}
    assert md_op.store == {"abc": b"# md"}
    mocks["history"].assert_awaited_once_with("abc", "parsed", batch_id=1)


@pytest.mark.asyncio
async def test_parse_document_skips_when_artifact_exists_and_not_force(monkeypatch):
    """Pre-existing PARSED_JSON + force=False → parse_bytes never called, no writes."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    json_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_JSON), _FakeOp())
    json_op.store["abc"] = b"already there"
    parse_bytes_mock = AsyncMock()
    monkeypatch.setattr(workflow, "parse_bytes", parse_bytes_mock)
    wf_run, sc = _make_run_and_config()

    await workflow.parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
        force=False,
    )

    parse_bytes_mock.assert_not_awaited()
    md_op = ops.get((WorkflowStepType.PARSE, ArtifactType.PARSED_MD))
    assert md_op is None or md_op.writes == []


@pytest.mark.asyncio
async def test_parse_document_force_deletes_then_writes(monkeypatch):
    """force=True with prior artifacts → op.delete called before op.write, content replaced."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    json_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_JSON), _FakeOp())
    md_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_MD), _FakeOp())
    json_op.store["abc"] = b"old json"
    md_op.store["abc"] = b"old md"
    monkeypatch.setattr(workflow, "parse_bytes", AsyncMock(return_value=(b"new md", b"new json")))
    wf_run, sc = _make_run_and_config()

    await workflow.parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
        force=True,
    )

    assert json_op.deletes == ["abc"]
    assert md_op.deletes == ["abc"]
    assert json_op.store == {"abc": b"new json"}
    assert md_op.store == {"abc": b"new md"}


@pytest.mark.asyncio
async def test_parse_document_markdown_override_replaces_md_only(monkeypatch):
    """markdown_override='custom' → PARSED_MD == b'custom'; PARSED_JSON unchanged."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    monkeypatch.setattr(workflow, "parse_bytes", AsyncMock(return_value=(b"original-md", b'{"original": true}')))
    wf_run, sc = _make_run_and_config()

    await workflow.parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
        markdown_override="my custom md",
    )

    assert ops[(WorkflowStepType.PARSE, ArtifactType.PARSED_JSON)].store["abc"] == b'{"original": true}'
    assert ops[(WorkflowStepType.PARSE, ArtifactType.PARSED_MD)].store["abc"] == b"my custom md"


@pytest.mark.asyncio
async def test_parse_document_mime_type_override_forwarded_to_parse_bytes(monkeypatch):
    """mime_type_override flows through to parse_bytes as the second positional arg."""
    _patch_doc_ops(monkeypatch)
    _install_op_factory(monkeypatch)
    parse_bytes_mock = AsyncMock(return_value=(b"md", b"json"))
    monkeypatch.setattr(workflow, "parse_bytes", parse_bytes_mock)
    wf_run, sc = _make_run_and_config()

    await workflow.parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
        mime_type_override="text/asciidoc",
    )

    args, _ = parse_bytes_mock.await_args
    assert args[1] == "text/asciidoc"


@pytest.mark.asyncio
async def test_parse_document_file_bytes_override_skips_storage_read(monkeypatch):
    """file_bytes_override=... → read_doc_bytes is NOT called and the override goes to parse_bytes."""
    mocks = _patch_doc_ops(monkeypatch)
    _install_op_factory(monkeypatch)
    parse_bytes_mock = AsyncMock(return_value=(b"md", b"json"))
    monkeypatch.setattr(workflow, "parse_bytes", parse_bytes_mock)
    wf_run, sc = _make_run_and_config()

    await workflow.parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
        file_bytes_override=b"INJECTED",
    )

    mocks["read_bytes"].assert_not_awaited()
    args, _ = parse_bytes_mock.await_args
    assert args[0] == b"INJECTED"


@pytest.mark.asyncio
async def test_parse_document_propagates_parse_bytes_exception(monkeypatch):
    """parse_bytes raising WorkflowException → workflow re-raises and writes nothing."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    monkeypatch.setattr(workflow, "parse_bytes", AsyncMock(side_effect=workflow.WorkflowException("boom")))
    wf_run, sc = _make_run_and_config()

    with pytest.raises(workflow.WorkflowException, match="boom"):
        await workflow.parse_document(
            batch_id=1,
            doc_hash="abc",
            source="src",
            step_config=sc,
            workflow_run=wf_run,
        )

    for op in ops.values():
        assert op.writes == []


@pytest.mark.asyncio
async def test_parse_document_raises_when_artifact_missing_after_write(monkeypatch):
    """Post-write existence check fails → raises 'missing after write'."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    json_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_JSON), _FakeOp())
    json_op.force_exists(False)
    monkeypatch.setattr(workflow, "parse_bytes", AsyncMock(return_value=(b"md", b"json")))
    wf_run, sc = _make_run_and_config()

    with pytest.raises(workflow.WorkflowException, match="missing after write"):
        await workflow.parse_document(
            batch_id=1,
            doc_hash="abc",
            source="src",
            step_config=sc,
            workflow_run=wf_run,
        )


# =====================================================================
# split_parse_document
# =====================================================================


@pytest.mark.asyncio
async def test_split_parse_document_writes_artifacts_and_records_history(monkeypatch):
    """Happy path: split_parse_bytes returns canned bytes → both artifacts written, history appended."""
    mocks = _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    split_mock = AsyncMock(return_value=(b"# split md", b'{"split":"yes"}'))
    monkeypatch.setattr(workflow, "split_parse_bytes", split_mock)
    wf_run, sc = _make_run_and_config({"k": "v"})

    await workflow.split_parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
    )

    split_mock.assert_awaited_once_with(b"FAKE", "document.pdf", {"k": "v"})
    json_op = ops[(WorkflowStepType.PARSE, ArtifactType.PARSED_JSON)]
    md_op = ops[(WorkflowStepType.PARSE, ArtifactType.PARSED_MD)]
    assert json_op.store == {"abc": b'{"split":"yes"}'}
    assert md_op.store == {"abc": b"# split md"}
    mocks["history"].assert_awaited_once_with("abc", "parsed", batch_id=1)


@pytest.mark.asyncio
async def test_split_parse_document_delegates_non_pdf_to_parse_document(monkeypatch):
    """Non-PDF source_uri → parse_document is called and split_parse_bytes is not."""
    _patch_doc_ops(monkeypatch, doc_uris=[MagicMock(uri="document.html")])
    _install_op_factory(monkeypatch)
    split_mock = AsyncMock()
    parse_doc_mock = AsyncMock()
    monkeypatch.setattr(workflow, "split_parse_bytes", split_mock)
    monkeypatch.setattr(workflow, "parse_document", parse_doc_mock)
    wf_run, sc = _make_run_and_config()

    await workflow.split_parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
    )

    split_mock.assert_not_awaited()
    parse_doc_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_split_parse_document_skips_when_both_artifacts_exist(monkeypatch):
    """Both PARSED_* present + force=False → split_parse_bytes never called."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    json_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_JSON), _FakeOp())
    md_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_MD), _FakeOp())
    json_op.store["abc"] = b"already json"
    md_op.store["abc"] = b"already md"
    split_mock = AsyncMock()
    monkeypatch.setattr(workflow, "split_parse_bytes", split_mock)
    wf_run, sc = _make_run_and_config()

    await workflow.split_parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
        force=False,
    )

    split_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_parse_document_raises_when_no_doc_uris(monkeypatch):
    """Empty doc_uris → raises before reaching split_parse_bytes."""
    _patch_doc_ops(monkeypatch, doc_uris=[])
    _install_op_factory(monkeypatch)
    split_mock = AsyncMock()
    monkeypatch.setattr(workflow, "split_parse_bytes", split_mock)
    wf_run, sc = _make_run_and_config()

    with pytest.raises(workflow.WorkflowException, match="no uris found"):
        await workflow.split_parse_document(
            batch_id=1,
            doc_hash="abc",
            source="src",
            step_config=sc,
            workflow_run=wf_run,
        )

    split_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_parse_document_retries_when_first_write_missing(monkeypatch):
    """First post-write check reports missing → triggers a retry write that succeeds."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    json_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_JSON), _FakeOp())
    md_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_MD), _FakeOp())
    # PARSED_JSON.exists called at: line 191 (info), 217 (gate), 247 (post-check), 251 (final).
    # Returning False at 247 triggers retry; True at 251 lets the run succeed.
    json_op.exists = AsyncMock(side_effect=[False, False, False, True])
    # PARSED_MD.exists is only reached at line 252 (217 and 247 short-circuit when json False).
    md_op.exists = AsyncMock(side_effect=[True])
    monkeypatch.setattr(workflow, "split_parse_bytes", AsyncMock(return_value=(b"md", b"json")))
    wf_run, sc = _make_run_and_config()

    await workflow.split_parse_document(
        batch_id=1,
        doc_hash="abc",
        source="src",
        step_config=sc,
        workflow_run=wf_run,
    )

    assert len(json_op.writes) == 2
    assert len(md_op.writes) == 2


@pytest.mark.asyncio
async def test_split_parse_document_raises_when_retry_also_fails(monkeypatch):
    """Both writes still missing after retry → raises 'missing files after retrying write'."""
    _patch_doc_ops(monkeypatch)
    ops = _install_op_factory(monkeypatch)
    json_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_JSON), _FakeOp())
    md_op = ops.setdefault((WorkflowStepType.PARSE, ArtifactType.PARSED_MD), _FakeOp())
    json_op.force_exists(False)
    md_op.force_exists(False)
    monkeypatch.setattr(workflow, "split_parse_bytes", AsyncMock(return_value=(b"md", b"json")))
    wf_run, sc = _make_run_and_config()

    with pytest.raises(workflow.WorkflowException, match="missing files after retrying write"):
        await workflow.split_parse_document(
            batch_id=1,
            doc_hash="abc",
            source="src",
            step_config=sc,
            workflow_run=wf_run,
        )
