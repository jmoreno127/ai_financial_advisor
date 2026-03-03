from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


class StructuredLogger:
    def __init__(self, json_log_path: str):
        self.logger = logging.getLogger("advisor")
        if not self.logger.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
            )
        self.json_log_path = Path(json_log_path)
        self.json_log_path.parent.mkdir(parents=True, exist_ok=True)

    def info(self, message: str, **payload: Any) -> None:
        self.logger.info(message)
        self._write_json({"level": "INFO", "message": message, **payload})

    def error(self, message: str, **payload: Any) -> None:
        self.logger.error(message)
        self._write_json({"level": "ERROR", "message": message, **payload})

    def _write_json(self, payload: dict[str, Any]) -> None:
        with self.json_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
