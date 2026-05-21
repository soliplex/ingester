import asyncio
import http.cookiejar as cj
import json
import logging
from io import BytesIO

import aiohttp
import httpx
from tenacity import retry
from tenacity import stop_after_attempt
from tenacity import wait_exponential_jitter

from soliplex.ingester.lib.config import get_settings

logger = logging.getLogger(__name__)


SMALLEST_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


DESC_DEFAULTS = {
    "prompt": "Describe this image in detail. Be precise and concise.",
    # "model": "ministral-3",
    "model": "ministral-3:3b",
    "timeout": 90,
    "max_tokens": 200,
}

# Process-wide async semaphore that bounds the number of concurrent
# in-flight requests to the docling-serve backend. Initialized lazily
# so it binds to the running event loop on first use; tests that
# spin up fresh loops should reset this back to None between cases
# (see ``tests/conftest.py``).
_docling_sem: asyncio.Semaphore | None = None


def get_docling_sem() -> asyncio.Semaphore:
    global _docling_sem
    if _docling_sem is None:
        _docling_sem = asyncio.Semaphore(get_settings().docling_concurrency)
    return _docling_sem


def do_repl(data):
    if isinstance(data, dict):
        return {do_repl(k): do_repl(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [do_repl(item) for item in data]
    elif isinstance(data, str) and "data:image" in data:
        return SMALLEST_PNG
    return data


def is_html(file_bytes: bytes) -> bool:
    """Detect HTML from the leading bytes only.

    The ``<html`` check stays at the first 100 bytes (matches the
    original behavior). The ``<body`` check is bounded to the first
    8 KB so non-HTML payloads (e.g. multi-MB PDFs) do not trigger a
    full-buffer ``bytes.find``.
    """
    return (file_bytes.startswith(b"<!DOCTYPE html>") or b"<html" in file_bytes[:100]) and b"<body" in file_bytes[:8192]


@retry(stop=stop_after_attempt(4), wait=wait_exponential_jitter(), reraise=True)
async def docling_convert(
    file_bytes: bytes,
    mime_type: str,
    source_uri: str,
    config_dict: dict[str, str | int | bool],
    output_formats: list[str] = ("json", "md"),
) -> dict:
    """Convert a document via the docling-serve HTTP backend.

    Process-wide concurrency to docling-serve is bounded by
    ``settings.docling_concurrency`` via the module-global
    ``_docling_sem``. The semaphore wraps only the HTTP / websocket
    / result-GET round trip — large-document post-processing
    (recursive image-placeholder substitution and whole-tree
    re-serialization) runs outside the slot and on a worker thread,
    so CPU work cannot starve docling-serve capacity.

    The semaphore is acquired *inside* the tenacity retry so a
    failing attempt releases its slot while it waits to retry.

    Note: the gate is per-process. If multiple worker processes
    share one docling-serve, the server sees
    ``N_workers * docling_concurrency`` aggregate parallelism; in
    that topology, rate-limit at the proxy or in docling-serve's
    own queue instead.
    """
    async with get_docling_sem():
        res, parameters = await _docling_request(
            file_bytes,
            mime_type,
            source_uri,
            config_dict,
            output_formats,
        )
    return await asyncio.to_thread(_process_result, res, parameters, source_uri, output_formats)


async def _docling_request(
    file_bytes: bytes,
    mime_type: str,
    source_uri: str,
    config_dict: dict[str, str | int | bool],
    output_formats: list[str],
) -> tuple[dict, dict]:
    """POST + websocket wait + result GET against docling-serve.

    Returns ``(res, parameters)``. ``res`` is the parsed result
    document; ``parameters`` is the request body as sent (the
    caller needs ``image_export_mode`` from it to decide on
    placeholder replacement). Raises ``ValueError`` on protocol
    errors, which the outer ``@retry`` retries.
    """
    env = get_settings()
    local_jar = cj.CookieJar()
    async_url = f"{env.docling_server_url}/convert/file/async"
    parameters: dict = {
        "from_formats": [
            "docx",
            "pptx",
            "html",
            "image",
            "pdf",
            "asciidoc",
            "md",
            "xlsx",
        ],
        "to_formats": list(output_formats),
        "abort_on_error": True,
    }
    if "ocr_lang" in config_dict and isinstance(config_dict["ocr_lang"], str):
        config_dict = config_dict.copy()
        # this param needs to be a list
        config_dict["ocr_lang"] = [config_dict["ocr_lang"]]
    parameters.update(config_dict)
    # remove picture description
    for k in list(parameters.keys()):
        if k.startswith("picture_description_"):
            del parameters[k]
    if "do_picture_description" in config_dict and config_dict["do_picture_description"] is True:
        parameters["do_picture_description"] = True
        prompt = config_dict.get("picture_description_prompt", DESC_DEFAULTS["prompt"])
        model = config_dict.get("picture_description_model", DESC_DEFAULTS["model"])
        picture_description_api = {
            "params": {
                "model": model,
                "max_completion_tokens": config_dict.get("picture_description_max_tokens", DESC_DEFAULTS["max_tokens"]),
            },
            "prompt": prompt,
            "timeout": DESC_DEFAULTS["timeout"],
        }
        parameters["picture_description_api"] = json.dumps(picture_description_api)
    else:
        parameters["do_picture_description"] = False

    file_name = source_uri.split("/")[-1]
    if mime_type and "markdown" in mime_type and not file_name.endswith(".md"):
        file_name = file_name + ".md"
    # docling requires some special handling for html
    if is_html(file_bytes):
        parameters["from_formats"] = ["html"]
        file_name = file_name + ".html"

    f = BytesIO(file_bytes)
    try:
        files = {"files": (file_name, f, mime_type)}
        logger.debug(f"using {parameters} on {file_name}")
        async with httpx.AsyncClient(timeout=env.docling_http_timeout, cookies=local_jar) as _async_client:
            response = await _async_client.post(async_url, files=files, data=parameters)
            async_res = response.json()
            logger.debug(async_res)
            if "task_id" not in async_res:
                raise ValueError(f"no task_id in response: {async_res}")
            task_id = async_res["task_id"]
            async with aiohttp.ClientSession(cookies=response.cookies) as session:
                ws_url = f"{env.docling_server_url.replace('http', 'ws')}/status/ws/{task_id}"
                async with session.ws_connect(ws_url) as ws:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = msg.json()
                            if payload["message"] == "error":
                                break
                            if payload["message"] == "update" and payload["task"]["task_status"] in (
                                "success",
                                "failure",
                            ):
                                break
            if "task" in payload and "task_status" in payload["task"] and payload["task"]["task_status"] == "failure":
                if "errors" in payload["task"]:
                    logger.error(f"errors: {payload['task']['errors']}")
                else:
                    logger.error(f"no errors in response: {payload}")
            result_url = f"{env.docling_server_url}/result/{task_id}"
            response = await _async_client.get(result_url)
            # The result body carries the full Docling document, which
            # can be many MB. ``response.json()`` runs stdlib
            # ``json.loads`` on the calling thread — offload so the
            # event loop stays responsive while it parses.
            res = await asyncio.to_thread(response.json)
            logger.info(f"{task_id} result={res.get('status')} processing time={res.get('processing_time')}")
    finally:
        f.close()
    return res, parameters


def _process_result(
    res: dict,
    parameters: dict,
    source_uri: str,
    output_formats: list[str],
) -> dict:
    """Validate the docling-serve result and re-serialize each
    requested output format.

    Pure Python. ``do_repl`` walks the entire JSON tree and
    ``json.dumps`` re-serializes it — seconds of work on a large
    document. Designed to run on a worker thread (see the
    ``asyncio.to_thread`` call in :func:`docling_convert`).
    """
    if "status" not in res:
        raise ValueError(f"no status in response: {res}")
    if res["status"] != "success":
        raise ValueError(str(res["errors"]))

    parsed: dict[str, bytes] = {}
    for output_format in output_formats:
        output_content = res["document"][f"{output_format}_content"]
        if output_format == "json":
            if parameters.get("image_export_mode") == "placeholder":
                logger.info(f" doing placeholder replacement for {source_uri}")
                output_content = do_repl(output_content)
            parsed[output_format] = json.dumps(output_content).encode("utf-8")
        else:
            parsed[output_format] = str(output_content).encode("utf-8")
    return parsed


def get_docling_schema_version() -> str:
    import docling_core.types.doc.document as dd

    return dd.CURRENT_VERSION
