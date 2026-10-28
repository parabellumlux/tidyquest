#!/usr/bin/env python3
"""Regression tests for the stage-1 OOM handling (Diana's phone test,
2026-10-26: a full-resolution phone photo OOM'd the 4GB card and the
app showed a raw traceback).

The SAM model is mocked -- no GPU, no weights, no network. Verifies:
  1. A CUDA OOM on the GPU pass retries on CPU and reports "cpu".
  2. A non-OOM error propagates (no silent retry on real bugs).
  3. A clean GPU pass returns its masks and reports "cuda".

Run on luci inside the tidyquest venv (torch + ultralytics installed).
"""
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import segment


def _tiny_png():
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    Image.new("RGB", (4, 4), (255, 255, 255)).save(path)
    return path


class _FakeResult:
    def __init__(self, masks_tensor):
        if masks_tensor is None:
            self.masks = None
        else:
            self.masks = MagicMock()
            self.masks.data = masks_tensor


def _fake_sam_class(behavior):
    """behavior: dict device -> 'oom' | 'raise' | masks-tensor-or-None."""

    class FakeSAM:
        def __init__(self, path):
            pass

        def __call__(self, path, verbose=False, device=None):
            act = behavior.get(device)
            if act == "oom":
                raise torch.OutOfMemoryError("CUDA out of memory")
            if act == "raise":
                raise ValueError("boom")
            if act is None:
                return [_FakeResult(None)]
            return [_FakeResult(act)]

    return FakeSAM


def test_oom_falls_back_to_cpu():
    img = _tiny_png()
    try:
        with patch("ultralytics.SAM", _fake_sam_class({"cuda": "oom", "cpu": None})):
            masks, device = segment.run_sam(img, "sam2.1_b.pt")
        assert masks == [] and device == "cpu", (masks, device)
    finally:
        os.unlink(img)
    print("PASS oom_falls_back_to_cpu")


def test_non_oom_propagates():
    img = _tiny_png()
    try:
        with patch("ultralytics.SAM", _fake_sam_class({"cuda": "raise"})):
            try:
                segment.run_sam(img, "sam2.1_b.pt")
            except ValueError:
                print("PASS non_oom_propagates")
                return
            raise AssertionError("ValueError was swallowed")
    finally:
        os.unlink(img)


def test_gpu_success_reports_cuda():
    img = _tiny_png()
    try:
        t = torch.zeros((1, 4, 4), dtype=torch.bool)
        t[0, 1:3, 1:3] = True
        with patch("ultralytics.SAM", _fake_sam_class({"cuda": t})):
            masks, device = segment.run_sam(img, "sam2.1_b.pt")
        assert device == "cuda" and len(masks) == 1, (device, len(masks))
    finally:
        os.unlink(img)
    print("PASS gpu_success_reports_cuda")


if __name__ == "__main__":
    test_oom_falls_back_to_cpu()
    test_non_oom_propagates()
    test_gpu_success_reports_cuda()
    print("ALL OOM-FALLBACK TESTS PASSED")
