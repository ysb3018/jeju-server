import json
import pathlib
from datetime import datetime

from server import KST, latest_tmfc, midterm, shortterm

tmfc = latest_tmfc()
data = {
    "updatedAt": datetime.now(KST).isoformat(timespec="seconds"),
    "shortTerm": shortterm(),
    "midTerm": midterm(tmfc),
}

out_dir = pathlib.Path("docs/forecast")
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "weekly.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("saved", out_dir / "weekly.json")
