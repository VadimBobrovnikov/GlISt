"""Контрольный прогон сценария затопления (Оренбург, апрель 2024).

Дата события указана явно: затопление краткосрочно, сезонный композит его
скрывает. Базовый период подбирается обработчиком (ближайшее окно без снега).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_scenario import main  # noqa: E402

QUERY = "Наводнение в Оренбурге 2024-04-10: покажи зоны затопления"
ANALYSIS_TYPE = "flood_extent"
TERRITORY = "Оренбург"

if __name__ == "__main__":
    main(QUERY, ANALYSIS_TYPE, TERRITORY)
