"""Offline SVG rendering benchmark, including mapped Tk UI heartbeat.

python scripts/benchmark_vector_preview.py INPUT.svg --output .local/tasks/TOPIC/benchmark.json
Use --baseline PYTHON_FILE to compare a saved pre-change vector_preview.py.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_preview import ImagePreview
from vector_preview import VectorImage


def measure(renderer, svg):
    started = time.perf_counter()
    vector = renderer(svg)
    initialization = (time.perf_counter()-started)*1000
    fit = min(1000/vector.width, 650/vector.height)
    frames = []
    for zoom in (1, 1.25, 1.5, 2, 4, 8, 32, 1, 1.25, 1.5, 2):
        started = time.perf_counter()
        vector.render(fit*zoom, (0, 0), (1000, 650))
        frames.append(round((time.perf_counter()-started)*1000, 3))
    return dict(initialization_ms=round(initialization, 3), frame_ms=frames,
                warm_median_ms=round(statistics.median(frames[1:]), 3),
                retained_picture=getattr(vector, '_picture', None) is not None)


def visible_ui(svg):
    # Construction is off-thread in LiveryApp; this measures actual mapped draw.
    vector = VectorImage(svg)
    root = tk.Tk()
    root.geometry('1000x750')
    viewer = ImagePreview(root)
    viewer.pack(fill='both', expand=True)
    root.update()
    errors, pulses, presents = [], [], []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    original = viewer._present
    def present(*args):
        started = time.perf_counter()
        original(*args)
        presents.append((time.perf_counter()-started)*1000)
    viewer._present = present
    timers = []
    def heartbeat():
        pulses.append(time.perf_counter())
        timers.append(root.after(10, heartbeat))
    heartbeat()
    viewer.set_image(None, vector)
    deadline = time.perf_counter()+6
    while viewer._frame is None and time.perf_counter() < deadline:
        root.update()
        time.sleep(.001)
    first_ready = viewer._frame is not None
    first_gaps = [(b-a)*1000 for a, b in zip(pulses, pulses[1:])]
    pulses.clear()
    started = time.perf_counter()
    fit = viewer.scale
    zooms_done = []
    def zoom(index=0):
        if index == 30:
            zooms_done.append(True)
            return
        viewer.set_scale(fit * 1.08 ** (index % 15))
        timers.append(root.after(20, zoom, index+1))
    zoom()
    while time.perf_counter()-started < 8:
        root.update()
        time.sleep(.001)
        if (zooms_done and viewer._frame is not None
                and viewer._frame[1:3] == (viewer.scale, viewer.offset)
                and viewer._render_wait_key is None):
            break
    final_ready = viewer._frame is not None and viewer._frame[1:3] == (viewer.scale, viewer.offset)
    gaps = [(b-a)*1000 for a, b in zip(pulses, pulses[1:])]
    for timer in timers:
        root.after_cancel(timer)
    worker = viewer._render_thread
    root.destroy()
    if worker is not None:
        worker.join(5)
    return dict(first_exact_ready=first_ready, final_exact_ready=final_ready,
                heartbeat_max_gap_ms=round(max(gaps), 3),
                heartbeat_median_gap_ms=round(statistics.median(gaps), 3),
                initial_heartbeat_max_gap_ms=round(max(first_gaps), 3),
                interaction_completed=bool(zooms_done), elapsed_ms=round((time.perf_counter()-started)*1000, 3),
                presents=len(presents), present_max_ms=round(max(presents), 3), errors=errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('svg', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    svg = args.svg.read_text(encoding='utf8')
    result = dict(input=args.svg.name, characters=len(svg), paths=svg.count('<path '),
                  viewport=[1000, 650], zooms=[1, 1.25, 1.5, 2, 4, 8, 32, 1, 1.25, 1.5, 2])
    if args.baseline:
        spec = importlib.util.spec_from_file_location('baseline_vector', args.baseline)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result['baseline'] = measure(module.VectorImage, svg)
    result['optimized'] = measure(VectorImage, svg)
    result['visible_ui'] = visible_ui(svg)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded+'\n', encoding='utf8')
    print(encoded)


if __name__ == '__main__':
    main()
