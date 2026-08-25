import argparse
import json
import logging
from pathlib import Path

from physics_reviewer.agents import review_paper
from physics_reviewer.pdf_parser import extract_pdf_text_cached

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Qwen + LangGraph physics paper review.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", type=Path, help="Path to a plain text paper.")
    source.add_argument("--pdf", type=Path, help="Path to a PDF paper.")
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    if args.text:
        paper_text = args.text.read_text(encoding="utf-8")
        title = args.title or args.text.stem
    else:
        paper_text = extract_pdf_text_cached(args.pdf.read_bytes())
        title = args.title or args.pdf.stem

    response = review_paper(title, paper_text)
    print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
