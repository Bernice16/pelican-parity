"""Fake renderer for offline tests: "renders" an SVG by copying its text to the PNG path.

The fake judge then reads the drawing's marker back from A.png / B.png.
"""
import shutil
import sys

if sys.argv[1] == "--version":
    print("fake-render 1.0")
    sys.exit()
shutil.copy(sys.argv[-2], sys.argv[-1])
