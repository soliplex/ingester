"""Generate test PDFs with random text and images for ingester testing.

Usage:
    uv run python scripts/generate_test_pdfs.py OUTPUT_DIR \\
        --count 5 --pages 3 --images-per-page 2
"""

import io
import random
from pathlib import Path
from typing import Annotated

import typer
from faker import Faker
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image as RLImage
from reportlab.platypus import KeepTogether
from reportlab.platypus import PageBreak
from reportlab.platypus import Paragraph
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus import Spacer
from reportlab.platypus import Table
from reportlab.platypus import TableStyle

app = typer.Typer(add_completion=False, help=__doc__)


def _random_image(width: int = 320, height: int = 200) -> io.BytesIO:
    pixels = bytes(random.randint(0, 255) for _ in range(width * height * 3))
    img = Image.frombytes("RGB", (width, height), pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _build_table_block(
    fake: Faker,
    table_number: int,
    styles: dict[str, ParagraphStyle],
) -> KeepTogether:
    """Build a sample data table with a captioned label.

    The block is wrapped in ``KeepTogether`` so the caption stays with the
    table when ReportLab paginates the story.
    """
    headers = ["ID", "Name", "Department", "Score"]
    rows: list[list[str]] = [headers]
    for i in range(random.randint(3, 6)):
        rows.append(
            [
                str(i + 1),
                fake.first_name(),
                fake.job().split(",")[0][:24],
                f"{random.uniform(0, 100):.1f}",
            ]
        )

    table = Table(rows, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 1), (0, -1), "RIGHT"),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]
        )
    )

    caption_text = f"Table {table_number}: {fake.sentence(nb_words=8).rstrip('.')}"
    return KeepTogether(
        [
            table,
            Spacer(1, 4),
            Paragraph(caption_text, styles["caption"]),
            Spacer(1, 12),
        ]
    )


def _build_page_flowables(
    fake: Faker,
    images_per_page: int,
    styles: dict[str, ParagraphStyle],
    table_number: int,
) -> list:
    flowables: list = [Paragraph(fake.sentence(nb_words=6).rstrip("."), styles["h1"])]
    flowables.append(Spacer(1, 12))

    paragraphs = random.randint(3, 6)
    image_slots = random.sample(range(paragraphs), min(images_per_page, paragraphs))
    h2_slots = set(random.sample(range(1, paragraphs), min(2, max(0, paragraphs - 1))))
    table_slot = random.randint(0, paragraphs - 1)

    for i in range(paragraphs):
        if i in h2_slots:
            flowables.append(Paragraph(fake.sentence(nb_words=4).rstrip("."), styles["h2"]))
            flowables.append(Spacer(1, 6))
        flowables.append(Paragraph(fake.paragraph(nb_sentences=random.randint(3, 7)), styles["body"]))
        flowables.append(Spacer(1, 8))
        if i in image_slots:
            buf = _random_image()
            flowables.append(RLImage(buf, width=320, height=200))
            flowables.append(Spacer(1, 8))
        if i == table_slot:
            flowables.append(_build_table_block(fake, table_number, styles))

    return flowables


def _generate_pdf(path: Path, pages: int, images_per_page: int, fake: Faker) -> None:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
        title=fake.sentence(nb_words=5).rstrip("."),
        author=fake.name(),
    )
    sample = getSampleStyleSheet()
    caption_style = ParagraphStyle(
        "Caption",
        parent=sample["BodyText"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=6,
    )
    styles = {
        "h1": sample["Heading1"],
        "h2": sample["Heading2"],
        "body": sample["BodyText"],
        "caption": caption_style,
    }

    story: list = []
    for page in range(pages):
        story.extend(
            _build_page_flowables(
                fake,
                images_per_page,
                styles,
                table_number=page + 1,
            )
        )
        if page < pages - 1:
            story.append(PageBreak())

    doc.build(story)


@app.command()
def main(
    output_dir: Annotated[Path, typer.Argument(help="Directory to write generated PDFs into.")],
    count: Annotated[int, typer.Option("--count", "-c", min=1, help="Number of PDFs to generate.")] = 1,
    pages: Annotated[int, typer.Option("--pages", "-p", min=1, help="Pages per PDF.")] = 3,
    images_per_page: Annotated[int, typer.Option("--images-per-page", "-i", min=0, help="Images per page.")] = 0,
    seed: Annotated[int | None, typer.Option("--seed", help="Seed for reproducible output.")] = None,
    prefix: Annotated[str, typer.Option("--prefix", help="Filename prefix.")] = "test_",
) -> None:
    """Generate ``count`` PDFs of ``pages`` pages with ``images_per_page`` random images."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)
    fake = Faker()

    for n in range(count):
        path = output_dir / f"{prefix}{n + 1:04d}.pdf"
        _generate_pdf(path, pages=pages, images_per_page=images_per_page, fake=fake)
        typer.echo(f"wrote {path}")


if __name__ == "__main__":
    app()
