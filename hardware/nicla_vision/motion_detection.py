
import sensor
import time

# Camera setup
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QQVGA)
sensor.skip_frames(time=2000)

sensor.set_auto_gain(False)
sensor.set_auto_exposure(False)

MOTION_THRESHOLD = 30
MIN_PIXELS = 200

previous = sensor.snapshot().copy()

motion_start = None
movement_count = 0
moving = False

while True:
    img = sensor.snapshot()

    # Keep an unmodified frame for comparison
    current = img.copy()

    diff = current.copy()
    diff.difference(previous)

    blobs = diff.find_blobs(
        [(MOTION_THRESHOLD, 255)],
        pixels_threshold=MIN_PIXELS,
        area_threshold=MIN_PIXELS,
        merge=True
    )

    motion_pixels = sum(blob.pixels for blob in blobs)
    now = time.ticks_ms()

    detected = motion_pixels > MIN_PIXELS

    if detected and not moving:
        moving = True
        motion_start = now
        movement_count += 1
        print("MOVEMENT STARTED")

    elif not detected and moving:
        duration = time.ticks_diff(now, motion_start)
        moving = False
        print("MOVEMENT ENDED | Duration:", duration, "ms")

    # Draw rectangles around movement
    for blob in blobs:
        img.draw_rectangle(blob.rect, color=255)

    print("motion_pixels:", motion_pixels,
          "movement_count:", movement_count)

    previous = current
    time.sleep_ms(100)
