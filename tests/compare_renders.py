#!/usr/bin/env python3

import sys
from PIL import Image, ImageChops


before = Image.open(sys.argv[1]).convert("RGB")
after = Image.open(sys.argv[2]).convert("RGB")
if before.size != after.size:
    raise SystemExit(f"render sizes differ: {before.size} != {after.size}")
if ImageChops.difference(before, after).getbbox() is not None:
    raise SystemExit("visible PDF renders differ")
print("renders are pixel-identical")
