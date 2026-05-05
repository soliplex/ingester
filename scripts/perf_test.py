"""Time parse_bytes / split_parse_bytes across a directory of PDFs.

Reads every PDF in INPUT_DIR, calls the chosen helper, and prints the elapsed
time per document plus a summary at the end. Requires a reachable docling
server (set ``DOCLING_SERVER_URL`` in the environment).

Usage:
    uv run --env-file .env python scripts/perf_test.py INPUT_DIR --mode split
    uv run --env-file .env python scripts/perf_test.py INPUT_DIR --mode parse \\
        --use-serve --split-workers 4 --limit 10
"""

import asyncio
import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import aiofiles
import typer

from soliplex.ingester.lib.workflow import parse_bytes
from soliplex.ingester.lib.workflow import split_parse_bytes

app = typer.Typer(add_completion=False, help=__doc__)


class Mode(StrEnum):
    parse = "parse"
    split = "split"


async def _read_bytes(path: Path) -> bytes:
    async with aiofiles.open(path, "rb") as f:
        return await f.read()


async def _time_one(path: Path, mode: Mode, config: dict) -> tuple[float, int, Exception | None]:
    file_bytes = await _read_bytes(path)
    size = len(file_bytes)
    start = time.perf_counter()
    try:
        if mode is Mode.split:
            await split_parse_bytes(file_bytes, str(path), config)
        else:
            await parse_bytes(file_bytes, "application/pdf", str(path), config)
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        return elapsed, size, e
    return time.perf_counter() - start, size, None


async def _run(input_dir: Path, mode: Mode, config: dict, limit: int | None) -> None:
    files = sorted(input_dir.glob("*.pdf"))
    if limit is not None:
        files = files[:limit]
    if not files:
        typer.echo(f"no PDFs found in {input_dir}")
        raise typer.Exit(code=1)

    typer.echo(f"mode={mode.value}  files={len(files)}  config={config}")
    typer.echo(f"{'elapsed_s':>10}  {'size_kb':>8}  {'mb_per_s':>9}  status  path")

    elapsed_total = 0.0
    bytes_total = 0
    failures = 0
    for path in files:
        elapsed, size, err = await _time_one(path, mode, config)
        elapsed_total += elapsed
        bytes_total += size
        if err is None:
            mbps = (size / 1_048_576) / elapsed if elapsed > 0 else 0.0
            typer.echo(f"{elapsed:>10.3f}  {size / 1024:>8.1f}  {mbps:>9.3f}  ok      {path}")
        else:
            failures += 1
            typer.echo(f"{elapsed:>10.3f}  {size / 1024:>8.1f}  {'-':>9}  FAIL    {path}  ({type(err).__name__}: {err})")

    typer.echo("")
    typer.echo(f"total: {len(files)} files, {failures} failed, {bytes_total / 1_048_576:.2f} MB in {elapsed_total:.2f}s")
    if elapsed_total > 0:
        typer.echo(f"throughput: {(bytes_total / 1_048_576) / elapsed_total:.3f} MB/s")


@app.command()
def main(
    input_dir: Annotated[Path, typer.Argument(help="Directory containing PDF files.")],
    mode: Annotated[Mode, typer.Option("--mode", "-m", help="Which helper to call.")] = Mode.split,
    use_serve: Annotated[
        bool,
        typer.Option("--use-serve/--no-use-serve", help="Use docling-serve fan-out (split mode only)."),
    ] = True,
    split_workers: Annotated[
        int,
        typer.Option("--split-workers", min=1, help="BatchProcessor workers when --no-use-serve."),
    ] = 1,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Process only the first N PDFs."),
    ] = None,
) -> None:
    """Run timing across every PDF in INPUT_DIR."""
    if not input_dir.is_dir():
        typer.echo(f"not a directory: {input_dir}")
        raise typer.Exit(code=1)
    config = {"use_serve": use_serve, "split_workers": split_workers}
    asyncio.run(_run(input_dir, mode, config, limit))


if __name__ == "__main__":
    app()
