"""Print the CI screenshots into the job log as small base64 JPEGs between markers.

Artifacts need direct blob access, which some maintainers' tools do not have; the job log is
always readable. Usage: python packaging/desktop/print_shots.py <smoke dir>
"""

import base64
import io
import sys
from pathlib import Path

from PIL import Image

for png in sorted(Path(sys.argv[1]).rglob("*.png")):
    if not png.name.startswith(("screen-", "companies")):
        continue
    img = Image.open(png).convert("RGB")
    img.thumbnail((1100, 1100))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=62, optimize=True)
    data = base64.b64encode(buf.getvalue()).decode()
    print(f"=== SHOT {png.parent.name}/{png.name} {img.size[0]}x{img.size[1]} ===")
    for i in range(0, len(data), 1000):
        print(data[i : i + 1000])
    print("=== END SHOT ===")
