"""Synthetic ABI/render probe; no task instructions, references or graders used."""
import importlib.metadata
import json
from pathlib import Path

import FreeCAD
import Part
import freecad_validator
import numpy as np
import pyvista as pv
import vtk
from PIL import Image
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

assert importlib.metadata.version('gnucleus-freecad-validator') == '0.1.3'
assert pv.__version__ == '0.48.4'
assert vtk.vtkVersion.GetVTKVersion() == '9.2.6'
values = np.arange(12, dtype=np.float64).reshape(4, 3)
assert np.array_equal(vtk_to_numpy(numpy_to_vtk(values)), values)
shape = Part.makeBox(1, 2, 3)
assert abs(shape.Volume - 6.0) < 1e-9
plotter = pv.Plotter(off_screen=True, window_size=(128, 128))
plotter.set_background('white')
plotter.add_mesh(pv.Cube(), color='cornflowerblue', show_edges=True)
target = Path('/tmp/tb4-synthetic-cad-render.png')
plotter.show(screenshot=str(target), auto_close=True)
assert target.exists() and target.stat().st_size > 100
assert np.asarray(Image.open(target)).std() > 1
print(json.dumps({'passed': True, 'synthetic_only': True, 'no_model_calls': True,
    'validator': importlib.metadata.version('gnucleus-freecad-validator'),
    'pyvista': pv.__version__, 'vtk': vtk.vtkVersion.GetVTKVersion(),
    'numpy': np.__version__, 'freecad': FreeCAD.Version()[:3],
    'numpy_vtk_roundtrip': True, 'box_volume': shape.Volume,
    'offscreen_render_bytes': target.stat().st_size}))
