import pathlib
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from docling_core.types.doc.document import DoclingDocument
from haiku.rag.config import Config as HRConfig
from haiku.rag.store.models.chunk import Chunk

from soliplex.ingester.lib import models
from soliplex.ingester.lib import rag
from soliplex.ingester.lib.config import LLMProvider


@pytest.fixture
def mock_app_config():
    """Create a mock AppConfig object"""
    config = MagicMock()
    config.providers = MagicMock()
    config.providers.docling_serve = MagicMock()
    config.providers.docling_serve.base_url = "http://localhost:5004/v1"
    config.providers.docling_serve.timeout = 30
    config.embeddings = MagicMock()
    config.embeddings.model = MagicMock()
    config.embeddings.model.name = "default-model"
    config.embeddings.model.vector_dim = 768
    config.embeddings.model.provider = "ollama"
    config.embeddings.model.base_url = None
    config.providers.ollama = MagicMock()
    config.providers.ollama.base_url = "http://ollama:11434"
    config.processing = MagicMock()
    config.storage = MagicMock()
    config.storage.data_dir = "/tmp/lancedb"
    config.storage.auto_vacuum = True
    return config


@pytest.fixture
def mock_settings():
    """Create a mock settings object"""
    settings = MagicMock()
    settings.docling_server_url = "http://localhost:5004/v1"
    settings.docling_chunk_server_url = "http://localhost:5004/v1"
    settings.docling_http_timeout = 60
    settings.lancedb_dir = "/tmp/lancedb"
    settings.embed_batch_size = 10
    settings.ollama_base_url = "http://ollama-test:11434"
    settings.embed_llm_url = "http://embed-llm:8000/v1"
    return settings


def test_build_docling_config(mock_app_config, mock_settings):
    """Test build_docling_config function"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {}
        result = rag.build_docling_config(mock_app_config, config_dict)

        # Verify the config is a copy
        assert result is not mock_app_config

        # Verify docling_serve configuration is updated
        assert result.providers.docling_serve.base_url == "http://localhost:5004"
        assert result.providers.docling_serve.timeout == 30


def test_build_embed_config_ollama(mock_app_config, mock_settings):
    """Test build_embed_config sets provider to ollama when config provider is OLLAMA"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "model": "test-model",
            "vector_dim": 1024,
            "provider": LLMProvider.OLLAMA,
        }
        result = rag.build_embed_config(mock_app_config, config_dict)

        # Verify the config is a copy
        assert result is not mock_app_config

        # Verify embeddings configuration is updated
        assert result.embeddings.model.name == "test-model"
        assert result.embeddings.model.vector_dim == 1024
        assert result.embeddings.model.provider == "ollama"
        assert result.providers.ollama.base_url == "http://ollama-test:11434"


def test_build_embed_config_openai(mock_app_config, mock_settings):
    """Test build_embed_config sets provider to openai and base_url from embed_llm_url when config provider is OPENAI"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "model": "test-model",
            "vector_dim": 1024,
            "provider": LLMProvider.OPENAI,
        }
        result = rag.build_embed_config(mock_app_config, config_dict)

        assert result.embeddings.model.name == "test-model"
        assert result.embeddings.model.vector_dim == 1024
        assert result.embeddings.model.provider == "openai"
        assert result.embeddings.model.base_url == "http://embed-llm:8000/v1"


def test_build_embed_config_openai_missing_embed_llm_url(mock_app_config, mock_settings):
    """Test build_embed_config raises ValueError when OPENAI provider has no embed_llm_url"""
    mock_settings.embed_llm_url = None
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "model": "test-model",
            "vector_dim": 1024,
            "provider": LLMProvider.OPENAI,
        }
        with pytest.raises(ValueError, match="embed_llm_url is not set"):
            rag.build_embed_config(mock_app_config, config_dict)


def test_build_embed_config_missing_required_key(mock_app_config, mock_settings):
    """Test build_embed_config raises ValueError when required keys are missing"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {"model": "test-model"}  # Missing vector_dim

        with pytest.raises(ValueError, match="Missing required key vector_dim"):
            rag.build_embed_config(mock_app_config, config_dict)


def test_build_chunk_config(mock_app_config, mock_settings):
    """Test build_chunk_config function"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "chunk_size": 512,
            "chunker": "hierarchical",
            "text_context_radius": 2,  # Should be removed
            "extra_param": "value",
        }
        result = rag.build_chunk_config(mock_app_config, config_dict)

        # Verify the config is a copy
        assert result is not mock_app_config

        # Verify docling config is also built
        assert result.providers.docling_serve.base_url == "http://localhost:5004"

        # Verify processing parameters are set (text_context_radius should be excluded)
        assert result.processing.chunk_size == 512
        assert result.processing.chunker == "hierarchical"
        assert result.processing.extra_param == "value"


def test_build_chunk_config_missing_required_key(mock_app_config, mock_settings):
    """Test build_chunk_config raises ValueError when required keys are missing"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {"chunk_size": 512}  # Missing chunker

        with pytest.raises(ValueError, match="Missing required key chunker"):
            rag.build_chunk_config(mock_app_config, config_dict)


def test_build_storage_config(mock_app_config, mock_settings):
    """Test build_storage_config function"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "data_dir": "test-dir",
            "extra_param": "value",
        }
        result = rag.build_storage_config(mock_app_config, config_dict)

        # Verify the config is a copy
        assert result is not mock_app_config

        # Verify storage parameters are set
        expected_path = pathlib.Path("/tmp/lancedb") / pathlib.Path("test-dir")
        assert result.storage.data_dir == expected_path
        assert result.storage.auto_vacuum is False  # Hardcoded to False
        assert result.storage.extra_param == "value"


def test_build_storage_config_missing_required_key(mock_app_config, mock_settings):
    """Test build_storage_config raises ValueError when required keys are missing"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {}  # Missing data_dir

        with pytest.raises(ValueError, match="Missing required key data_dir"):
            rag.build_storage_config(mock_app_config, config_dict)


def test_build_full_config(mock_app_config, mock_settings):
    """Test build_full_config function"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        chunk_config = {"chunk_size": 512, "chunker": "hierarchical"}
        embed_config = {
            "model": "test-model",
            "vector_dim": 1024,
            "provider": LLMProvider.OLLAMA,
        }
        storage_config = {"data_dir": "test-dir"}

        result = rag.build_full_config(mock_app_config, chunk_config, embed_config, storage_config)

        # Verify the config is a copy
        assert result is not mock_app_config

        # Verify all configurations are applied
        assert result.processing.chunk_size == 512
        assert result.processing.chunker == "hierarchical"
        assert result.embeddings.model.name == "test-model"
        assert result.embeddings.model.vector_dim == 1024
        expected_path = pathlib.Path("/tmp/lancedb") / pathlib.Path("test-dir")
        assert result.storage.data_dir == expected_path


@pytest.mark.asyncio
async def test_get_chunk_objs():
    """Test get_chunk_objs function"""
    mock_docling_doc = MagicMock(spec=DoclingDocument)
    config_dict = {"chunk_size": 512, "chunker": "hierarchical"}

    mock_chunk1 = MagicMock(spec=Chunk)
    mock_chunk2 = MagicMock(spec=Chunk)
    expected_chunks = [mock_chunk1, mock_chunk2]

    with (
        patch("soliplex.ingester.lib.rag.build_chunk_config") as mock_build_config,
        patch("soliplex.ingester.lib.rag.get_chunker") as mock_get_chunker,
    ):
        mock_config = MagicMock()
        mock_build_config.return_value = mock_config

        mock_chunker = MagicMock()
        mock_chunker.chunk = AsyncMock(return_value=expected_chunks)
        mock_get_chunker.return_value = mock_chunker

        result = await rag.get_chunk_objs(mock_docling_doc, config_dict)

        # Verify the correct functions were called
        mock_build_config.assert_called_once_with(HRConfig, config_dict)
        mock_get_chunker.assert_called_once_with(mock_config)
        mock_chunker.chunk.assert_called_once_with(mock_docling_doc)

        # Verify the result
        assert result == expected_chunks


@pytest.mark.asyncio
async def test_embed(mock_settings):
    """Test embed function"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        mock_chunk1 = MagicMock(spec=Chunk)
        mock_chunk2 = MagicMock(spec=Chunk)
        mock_chunk3 = MagicMock(spec=Chunk)
        chunks = [mock_chunk1, mock_chunk2, mock_chunk3]

        config_dict = {"model": "test-model", "vector_dim": 1024, "provider": "test-provider"}
        doc_hash = "test-hash-123"

        # Mock embedded chunks returned
        embedded_chunk1 = MagicMock(spec=Chunk)
        embedded_chunk2 = MagicMock(spec=Chunk)
        embedded_chunk3 = MagicMock(spec=Chunk)

        with (
            patch("soliplex.ingester.lib.rag.build_embed_config") as mock_build_config,
            patch("soliplex.ingester.lib.rag.embed_chunks") as mock_embed_chunks,
        ):
            mock_config = MagicMock()
            mock_build_config.return_value = mock_config

            # Mock embed_chunks to return batches
            mock_embed_chunks.side_effect = [
                [embedded_chunk1, embedded_chunk2],  # First batch
                [embedded_chunk3],  # Second batch
            ]

            # Set batch size to 2
            mock_settings.embed_batch_size = 2

            result = await rag.embed(chunks, config_dict, doc_hash)

            # Verify the correct functions were called
            mock_build_config.assert_called_once_with(HRConfig, config_dict)
            assert mock_embed_chunks.call_count == 2

            # Verify the result
            assert result == [embedded_chunk1, embedded_chunk2, embedded_chunk3]


@pytest.mark.asyncio
async def test_save_to_rag():
    """Test save_to_rag function"""
    # Create mock objects
    mock_doc = MagicMock(spec=models.Document)
    mock_doc.hash = "doc-hash-123"
    mock_doc.doc_meta = {"md5": "md5-hash-456", "extra": "metadata"}
    mock_doc.mime_type = "application/pdf"

    mock_chunk1 = MagicMock(spec=Chunk)
    mock_chunk2 = MagicMock(spec=Chunk)
    chunks = [mock_chunk1, mock_chunk2]

    docling_json = '{"document": "content"}'
    source_uri = "http://example.com/doc.pdf"

    step_config = MagicMock(spec=models.StepConfig)
    step_config.config_json = {"data_dir": "test-dir"}

    embed_config = MagicMock(spec=models.StepConfig)
    embed_config.config_json = {"model": "test-model", "vector_dim": 1024, "provider": "test-provider"}

    mock_docling_document = MagicMock(spec=DoclingDocument)

    mock_new_doc = MagicMock()
    mock_new_doc.id = "new-rag-doc-id"

    with (
        patch("soliplex.ingester.lib.rag.build_embed_config") as mock_build_embed_config,
        patch("soliplex.ingester.lib.rag.build_storage_config") as mock_build_storage_config,
        patch("soliplex.ingester.lib.rag.DoclingDocument") as mock_docling_class,
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
    ):
        mock_config = MagicMock()
        mock_build_embed_config.return_value = mock_config
        mock_build_storage_config.return_value = mock_config

        mock_docling_class.model_validate_json.return_value = mock_docling_document

        # Setup the async context manager for HaikuRAG
        mock_client = MagicMock()
        mock_client.import_document = AsyncMock(return_value=mock_new_doc)
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await rag.save_to_rag(
            doc=mock_doc,
            chunks=chunks,
            docling_json=docling_json,
            source_uri=models.DocumentURI(uri=source_uri, source="test"),
            step_config=step_config,
            embed_config=embed_config,
        )

        # Verify the correct functions were called
        mock_build_embed_config.assert_called_once_with(HRConfig, embed_config.config_json)
        mock_build_storage_config.assert_called_once()
        mock_docling_class.model_validate_json.assert_called_once_with(docling_json)

        # Verify HaikuRAG was initialized and import_document was called
        # db_path is computed from lancedb_dir (default "lancedb") / data_dir ("test-dir")
        import pathlib

        expected_db_path = pathlib.Path("lancedb") / "test-dir"
        mock_haiku_rag.assert_called_once_with(config=mock_config, create=True, db_path=expected_db_path)
        mock_client.import_document.assert_called_once()

        # Verify the metadata passed to import_document
        call_kwargs = mock_client.import_document.call_args.kwargs
        assert call_kwargs["chunks"] == chunks
        assert call_kwargs["title"] is None
        assert call_kwargs["uri"] == source_uri
        assert call_kwargs["docling_document"] == mock_docling_document
        assert call_kwargs["metadata"]["doc_id"] == "doc-hash-123"
        assert call_kwargs["metadata"]["md5"] == "md5-hash-456"
        assert call_kwargs["metadata"]["content_type"] == "application/pdf"
        assert call_kwargs["metadata"]["extra"] == "metadata"

        # Verify the result
        assert result == "new-rag-doc-id"


@pytest.mark.asyncio
async def test_save_to_rag_missing_data_dir():
    """Test save_to_rag raises ValueError when data_dir is missing"""
    mock_doc = MagicMock(spec=models.Document)
    mock_doc.hash = "doc-hash-123"
    mock_doc.doc_meta = {"md5": "md5-hash-456"}

    step_config = MagicMock(spec=models.StepConfig)
    step_config.config_json = {}  # Missing data_dir

    embed_config = MagicMock(spec=models.StepConfig)
    embed_config.config_json = {
        "model": "test-model",
        "vector_dim": 1024,
        "provider": LLMProvider.OLLAMA,
    }

    with patch("soliplex.ingester.lib.rag.build_embed_config", return_value=MagicMock()):
        with pytest.raises(ValueError, match="Missing required key data_dir"):
            await rag.save_to_rag(
                doc=mock_doc,
                chunks=[],
                docling_json='{"test": "data"}',
                source_uri=models.DocumentURI(uri="http://example.com/doc.pdf", source="test"),
                step_config=step_config,
                embed_config=embed_config,
            )


def test_build_storage_config_s3_in_data_dir(mock_app_config, mock_settings):
    """Test build_storage_config when data_dir contains an S3 URI"""
    mock_app_config.lancedb = MagicMock()

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "data_dir": "s3://my-bucket/rag-data",
        }
        result = rag.build_storage_config(mock_app_config, config_dict)

        # Verify lancedb configuration is set for S3
        assert result.lancedb.uri == "s3://my-bucket/rag-data"
        assert result.lancedb.api_key == "xxx"
        assert result.lancedb.region == "xx"
        assert result.storage.data_dir == pathlib.Path("s3://my-bucket/rag-data")
        assert result.storage.auto_vacuum is False


def test_build_storage_config_s3_in_env_lancedb_dir_with_trailing_slash(mock_app_config):
    """Test build_storage_config when env.lancedb_dir contains an S3 URI with trailing slash"""
    mock_settings_s3 = MagicMock()
    mock_settings_s3.lancedb_dir = "s3://env-bucket/lancedb/"

    mock_app_config.lancedb = MagicMock()

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings_s3):
        config_dict = {
            "data_dir": "my-project-data",
        }
        result = rag.build_storage_config(mock_app_config, config_dict)

        # Verify lancedb configuration uses env.lancedb_dir + data_dir
        assert result.lancedb.uri == "s3://env-bucket/lancedb/my-project-data"
        assert result.lancedb.api_key == "xxx"
        assert result.lancedb.region == "xx"
        assert result.storage.data_dir == pathlib.Path("s3://env-bucket/lancedb/my-project-data")
        assert result.storage.auto_vacuum is False


def test_build_storage_config_s3_in_env_lancedb_dir_without_trailing_slash(mock_app_config):
    """Test build_storage_config when env.lancedb_dir contains an S3 URI without trailing slash"""
    mock_settings_s3 = MagicMock()
    mock_settings_s3.lancedb_dir = "s3://env-bucket/lancedb"

    mock_app_config.lancedb = MagicMock()

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings_s3):
        config_dict = {
            "data_dir": "my-project-data",
        }
        result = rag.build_storage_config(mock_app_config, config_dict)

        # Verify lancedb configuration uses env.lancedb_dir + "/" + data_dir
        assert result.lancedb.uri == "s3://env-bucket/lancedb/my-project-data"
        assert result.lancedb.api_key == "xxx"
        assert result.lancedb.region == "xx"
        assert result.storage.data_dir == pathlib.Path("s3://env-bucket/lancedb/my-project-data")
        assert result.storage.auto_vacuum is False


def test_build_storage_config_both_s3_uses_config_dict(mock_app_config):
    """Test build_storage_config when both env.lancedb_dir and config_dict['data_dir'] contain S3 URIs.

    When both contain S3 URIs, the value from config_dict should be used (takes precedence).
    """
    mock_settings_s3 = MagicMock()
    mock_settings_s3.lancedb_dir = "s3://env-bucket/lancedb"

    mock_app_config.lancedb = MagicMock()

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings_s3):
        config_dict = {
            "data_dir": "s3://config-bucket/rag-data",
        }
        result = rag.build_storage_config(mock_app_config, config_dict)

        # Verify config_dict S3 URI is used, not the env.lancedb_dir
        assert result.lancedb.uri == "s3://config-bucket/rag-data"
        assert result.lancedb.api_key == "xxx"
        assert result.lancedb.region == "xx"
        assert result.storage.data_dir == pathlib.Path("s3://config-bucket/rag-data")
        assert result.storage.auto_vacuum is False


def test_build_embed_config_missing_model_key(mock_app_config, mock_settings):
    """Test build_embed_config raises ValueError when 'model' key is missing"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {"vector_dim": 1024}  # Missing model

        with pytest.raises(ValueError, match="Missing required key model"):
            rag.build_embed_config(mock_app_config, config_dict)


def test_build_chunk_config_missing_chunk_size_key(mock_app_config, mock_settings):
    """Test build_chunk_config raises ValueError when 'chunk_size' key is missing"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {"chunker": "hierarchical"}  # Missing chunk_size

        with pytest.raises(ValueError, match="Missing required key chunk_size"):
            rag.build_chunk_config(mock_app_config, config_dict)


def test_build_chunk_config_without_text_context_radius(mock_app_config, mock_settings):
    """Test build_chunk_config when text_context_radius is not in config_dict"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {
            "chunk_size": 512,
            "chunker": "hierarchical",
        }
        result = rag.build_chunk_config(mock_app_config, config_dict)

        # Verify the config is built successfully
        assert result is not mock_app_config
        assert result.processing.chunk_size == 512
        assert result.processing.chunker == "hierarchical"


def test_build_embed_config_unknown_provider(mock_app_config, mock_settings):
    """Test build_embed_config raises ValueError for an unknown provider in config_dict"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        config_dict = {"model": "m", "vector_dim": 8, "provider": "unknown"}
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            rag.build_embed_config(mock_app_config, config_dict)


@pytest.mark.asyncio
async def test_get_chunk_objs_no_chunks():
    """Test get_chunk_objs raises ValueError when no chunks produced"""
    mock_docling_doc = MagicMock(spec=DoclingDocument)
    config_dict = {"chunk_size": 512, "chunker": "hierarchical"}

    with (
        patch("soliplex.ingester.lib.rag.build_chunk_config") as mock_build_config,
        patch("soliplex.ingester.lib.rag.get_chunker") as mock_get_chunker,
    ):
        mock_build_config.return_value = MagicMock()
        mock_chunker = MagicMock()
        mock_chunker.chunk = AsyncMock(return_value=[])
        mock_get_chunker.return_value = mock_chunker

        with pytest.raises(ValueError, match="No chunks found"):
            await rag.get_chunk_objs(mock_docling_doc, config_dict)


def test_resolve_lancedb_path(mock_settings):
    """resolve_lancedb_path joins env.lancedb_dir with step config data_dir"""
    step_config = MagicMock(spec=models.StepConfig)
    step_config.config_json = {"data_dir": "proj-a"}
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        result = rag.resolve_lancedb_path(step_config)
        assert result == pathlib.Path("/tmp/lancedb") / "proj-a"


def test_resolve_lancedb_path_from_param_config(mock_settings):
    """resolve_lancedb_path_from_param_config joins env.lancedb_dir with data_dir"""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        result = rag.resolve_lancedb_path_from_param_config({"data_dir": "proj-b"})
        assert result == pathlib.Path("/tmp/lancedb") / "proj-b"


@pytest.mark.asyncio
async def test_check_rag_existence_missing_db(mock_settings, tmp_path):
    """check_rag_existence returns empty set when the db directory doesn't exist"""
    mock_settings.lancedb_dir = str(tmp_path)
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        result = await rag.check_rag_existence(
            doc_hashes=["h1", "h2"],
            store_config={"data_dir": "nonexistent"},
            embed_config={"model": "m", "vector_dim": 8},
        )
        assert result == set()


@pytest.mark.asyncio
async def test_check_rag_existence_finds_some(mock_settings, tmp_path):
    """check_rag_existence returns hashes that exist in the store"""
    db_dir = tmp_path / "store-a"
    db_dir.mkdir()
    mock_settings.lancedb_dir = str(tmp_path)

    # Simulate _find_docs_by_hash: h1 found, h2 not found
    def fake_find(h, tbl):
        return [MagicMock()] if h == "h1" else []

    mock_client = MagicMock()
    mock_client.document_repository.store.documents_table = MagicMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.build_embed_config", return_value=MagicMock()),
        patch("soliplex.ingester.lib.rag.build_storage_config", return_value=MagicMock()),
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag._find_docs_by_hash", side_effect=fake_find),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await rag.check_rag_existence(
            doc_hashes=["h1", "h2"],
            store_config={"data_dir": "store-a"},
            embed_config={"model": "m", "vector_dim": 8},
        )
        assert result == {"h1"}


@pytest.mark.asyncio
async def test_save_to_rag_with_title_and_existing_doc():
    """save_to_rag uses title from doc_meta and deletes existing doc before re-import"""
    mock_doc = MagicMock(spec=models.Document)
    mock_doc.hash = "h1"
    mock_doc.doc_meta = {"md5": "m1", "title": "My Title"}
    mock_doc.mime_type = "application/pdf"

    step_config = MagicMock(spec=models.StepConfig)
    step_config.config_json = {"data_dir": "dir"}

    embed_config = MagicMock(spec=models.StepConfig)
    embed_config.config_json = {"model": "m", "vector_dim": 8}

    existing = MagicMock()
    existing.id = "old-id"
    new_doc = MagicMock()
    new_doc.id = "new-id"

    mock_client = MagicMock()
    mock_client.import_document = AsyncMock(return_value=new_doc)
    mock_client.delete_document = AsyncMock()
    mock_client.document_repository.store.documents_table = MagicMock()

    with (
        patch("soliplex.ingester.lib.rag.build_embed_config", return_value=MagicMock()),
        patch("soliplex.ingester.lib.rag.build_storage_config", return_value=MagicMock()),
        patch("soliplex.ingester.lib.rag.DoclingDocument") as mock_docling_class,
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag._find_docs_by_hash", return_value=[existing]),
    ):
        mock_docling_class.model_validate_json.return_value = MagicMock(spec=DoclingDocument)
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await rag.save_to_rag(
            doc=mock_doc,
            chunks=[MagicMock(spec=Chunk)],
            docling_json='{"a": 1}',
            source_uri=models.DocumentURI(uri="http://x/y", source="s"),
            step_config=step_config,
            embed_config=embed_config,
        )

        mock_client.delete_document.assert_awaited_once_with("old-id")
        call_kwargs = mock_client.import_document.call_args.kwargs
        assert call_kwargs["title"] == "My Title"
        assert result == "new-id"


def test_compute_db_hmac(tmp_path):
    """_compute_db_hmac produces a stable hex digest over the directory contents"""
    db = tmp_path / "db"
    db.mkdir()
    (db / "a.bin").write_bytes(b"hello")
    (db / "sub").mkdir()
    (db / "sub" / "b.bin").write_bytes(b"world")

    key = b"k" * 64
    digest = rag._compute_db_hmac(db, key, buf_size=2)
    assert len(digest) == 128  # sha512 hex
    # deterministic — same input yields same digest
    assert digest == rag._compute_db_hmac(db, key, buf_size=2)


def test_sign_db_writes_hmac_file(mock_settings, tmp_path):
    """sign_db writes an .hmac sidecar next to the db directory"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key = MagicMock()
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "x" * 64
    db = tmp_path / "mydb"
    db.mkdir()
    (db / "data.bin").write_bytes(b"payload")

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        rag.sign_db("mydb")

    sidecar = tmp_path / "mydb.hmac"
    assert sidecar.exists()
    assert len(sidecar.read_text()) == 128


def test_sign_db_bad_key_length(mock_settings, tmp_path):
    """sign_db raises ValueError when the HMAC key isn't 64 bytes"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key = MagicMock()
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "short"

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        with pytest.raises(ValueError, match="must be 64 bytes"):
            rag.sign_db("mydb")


def test_verify_db_success(mock_settings, tmp_path):
    """verify_db returns True when the HMAC matches"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key = MagicMock()
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "x" * 64
    db = tmp_path / "mydb"
    db.mkdir()
    (db / "data.bin").write_bytes(b"payload")

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        rag.sign_db("mydb")
        assert rag.verify_db("mydb") is True


def test_verify_db_bad_key_length(mock_settings, tmp_path):
    """verify_db raises ValueError when the HMAC key isn't 64 bytes"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key = MagicMock()
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "short"

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        with pytest.raises(ValueError, match="must be 64 bytes"):
            rag.verify_db("mydb")


def test_verify_db_missing_file(mock_settings, tmp_path):
    """verify_db raises FileNotFoundError when the .hmac sidecar is missing"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key = MagicMock()
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "x" * 64
    (tmp_path / "mydb").mkdir()

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        with pytest.raises(FileNotFoundError, match="No HMAC file found"):
            rag.verify_db("mydb")


def test_verify_db_mismatch(mock_settings, tmp_path):
    """verify_db raises ValueError when the stored HMAC doesn't match"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key = MagicMock()
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "x" * 64
    db = tmp_path / "mydb"
    db.mkdir()
    (db / "data.bin").write_bytes(b"payload")
    (tmp_path / "mydb.hmac").write_text("0" * 128)

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        with pytest.raises(ValueError, match="HMAC mismatch"):
            rag.verify_db("mydb")


def test_resolve_db_path_plain(mock_settings, tmp_path):
    """_resolve_db_path returns the path when no haiku.rag.lancedb subfolder exists"""
    mock_settings.lancedb_dir = str(tmp_path)
    (tmp_path / "db1").mkdir()
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        assert rag._resolve_db_path("db1") == tmp_path / "db1"


def test_resolve_db_path_with_subfolder(mock_settings, tmp_path):
    """_resolve_db_path descends into haiku.rag.lancedb subfolder if present"""
    mock_settings.lancedb_dir = str(tmp_path)
    sub = tmp_path / "db1" / "haiku.rag.lancedb"
    sub.mkdir(parents=True)
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        assert rag._resolve_db_path("db1") == sub


def test_resolve_db_path_missing(mock_settings, tmp_path):
    """_resolve_db_path raises FileNotFoundError when the db doesn't exist"""
    mock_settings.lancedb_dir = str(tmp_path)
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        with pytest.raises(FileNotFoundError, match="Database does not exist"):
            rag._resolve_db_path("missing-db")


@pytest.mark.asyncio
async def test_vacuum_db_success(mock_settings, tmp_path):
    """vacuum_db resolves the path, configures retention and calls app.vacuum()"""
    mock_settings.lancedb_dir = str(tmp_path)
    (tmp_path / "db1").mkdir()

    mock_app = MagicMock()
    mock_app.vacuum = AsyncMock()
    mock_config = MagicMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.get_config", return_value=mock_config),
        patch("soliplex.ingester.lib.rag.HaikuRAGApp", return_value=mock_app) as mock_app_cls,
    ):
        await rag.vacuum_db("db1")

        assert mock_config.storage.vacuum_retention_seconds == 0
        mock_app_cls.assert_called_once_with(
            db_path=tmp_path / "db1",
            config=mock_config,
            read_only=False,
        )
        mock_app.vacuum.assert_awaited_once()


@pytest.mark.asyncio
async def test_vacuum_db_migration_required(mock_settings, tmp_path):
    """vacuum_db runs migrate() then retries vacuum when MigrationRequiredError is raised"""
    from haiku.rag.store.exceptions import MigrationRequiredError

    mock_settings.lancedb_dir = str(tmp_path)
    (tmp_path / "db1").mkdir()

    mock_app = MagicMock()
    mock_app.vacuum = AsyncMock(side_effect=[MigrationRequiredError("need migrate"), None])
    mock_app.migrate = MagicMock(return_value=["m1", "m2"])

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.get_config", return_value=MagicMock()),
        patch("soliplex.ingester.lib.rag.HaikuRAGApp", return_value=mock_app),
    ):
        await rag.vacuum_db("db1")

        assert mock_app.vacuum.await_count == 2
        mock_app.migrate.assert_called_once()


@pytest.mark.asyncio
async def test_vacuum_db_with_sign(mock_settings, tmp_path):
    """vacuum_db calls sign_db when sign=True"""
    mock_settings.lancedb_dir = str(tmp_path)
    (tmp_path / "db1").mkdir()

    mock_app = MagicMock()
    mock_app.vacuum = AsyncMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.get_config", return_value=MagicMock()),
        patch("soliplex.ingester.lib.rag.HaikuRAGApp", return_value=mock_app),
        patch("soliplex.ingester.lib.rag.sign_db") as mock_sign,
    ):
        await rag.vacuum_db("db1", sign=True)
        mock_sign.assert_called_once_with("db1")


def test_list_dbs_empty_base(mock_settings, tmp_path):
    """list_dbs returns [] when the base directory doesn't exist"""
    mock_settings.lancedb_dir = str(tmp_path / "does-not-exist")
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        assert rag.list_dbs() == []


def test_list_dbs_finds_databases(mock_settings, tmp_path):
    """list_dbs returns directories with haiku.rag.lancedb subfolders or non-empty content"""
    mock_settings.lancedb_dir = str(tmp_path)

    # db with subfolder
    (tmp_path / "db-with-sub" / "haiku.rag.lancedb").mkdir(parents=True)
    # non-empty plain db
    (tmp_path / "plain-db").mkdir()
    (tmp_path / "plain-db" / "some-file").write_text("x")
    # empty dir — skipped
    (tmp_path / "empty-dir").mkdir()
    # a file (not a dir) — skipped
    (tmp_path / "sidecar.hmac").write_text("h")
    # a directory with .hmac suffix — skipped by the suffix guard
    (tmp_path / "weird.hmac").mkdir()
    (tmp_path / "weird.hmac" / "f").write_text("x")

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        result = rag.list_dbs()

    assert result == ["db-with-sub", "plain-db"]


@pytest.mark.asyncio
async def test_vacuum_all_no_dbs(mock_settings, tmp_path, caplog):
    """vacuum_all logs and returns when there are no dbs"""
    mock_settings.lancedb_dir = str(tmp_path / "empty")
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.vacuum_all()


@pytest.mark.asyncio
async def test_vacuum_all_iterates(mock_settings):
    """vacuum_all calls vacuum_db for each db name and swallows per-db failures"""
    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.list_dbs", return_value=["a", "b", "c"]),
        patch(
            "soliplex.ingester.lib.rag.vacuum_db",
            side_effect=[None, RuntimeError("boom"), None],
        ) as mock_vacuum,
    ):
        await rag.vacuum_all(sign=True)

        assert mock_vacuum.await_count == 3
        for call in mock_vacuum.await_args_list:
            assert call.kwargs == {"sign": True}
