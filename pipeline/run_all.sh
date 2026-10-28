#!/bin/bash
# TidyQuest stage-1 full run, sequential (one process at a time: the 4GB
# GPU card cannot hold two SAM/VLM jobs at once).
cd ~/tidyquest-build
V=.venv/bin/python
$V pipeline/run_stage1.py test_01_car_backseat_BEFORE.jpg out/stage1_car_before_vlm --model sam2.1_b.pt --label vlm > /tmp/s1-car-before.log 2>&1
$V pipeline/run_stage1.py test_01_car_backseat_AFTER.jpg out/stage1_car_after_vlm --model sam2.1_b.pt --label vlm > /tmp/s1-car-after.log 2>&1
$V pipeline/run_stage1.py test_05_donna_bathroom_BEFORE.jpg out/stage1_donna_before_vlm --model sam2.1_b.pt --label vlm > /tmp/s1-donna-before.log 2>&1
$V pipeline/run_stage1.py test_05_donna_bathroom_AFTER_wideangle.jpg out/stage1_donna_after_vlm --model sam2.1_b.pt --label vlm > /tmp/s1-donna-after.log 2>&1
$V pipeline/differ.py out/stage1_car_before_vlm/inventory.json out/stage1_car_after_vlm/inventory.json out/stage1_car_differ.json > /tmp/s1-car-differ.log 2>&1
$V pipeline/differ.py out/stage1_donna_before_vlm/inventory.json out/stage1_donna_after_vlm/inventory.json out/stage1_donna_differ.json --angle-mismatch > /tmp/s1-donna-differ.log 2>&1
echo ALL_DONE
