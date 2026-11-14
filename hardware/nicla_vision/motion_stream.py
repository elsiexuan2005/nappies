# PodCare motion stream - Arduino Nicla Vision running OpenMV (MicroPython).
# Same camera motion detection as motion_detection.py (frame differencing + blobs),
# but sends ONE JSON line per second for the PodCare software:
#   {"timestamp_ms": 12345, "motion_pixels": 840, "moving": 1, "movement_count": 3,
#    "acc_mean": 1.0012, "acc_std": 0.0041, "gyro_mean": 0.62}
# motion_pixels = busiest frame in the last second. The acc_*/gyro_* fields are added only
# if the Nicla's on-board IMU (LSM6DSOX) can be read; otherwise the software converts camera
# motion into activity by itself.
#
# Privacy: images never leave the Nicla - only these numbers are sent.
# To run without the OpenMV IDE: save this file as main.py on the Nicla's USB drive and reset.
# Close the OpenMV IDE afterwards - the PodCare software needs the serial port.

import sensor
import time
import math

MOTION_THRESHOLD = 30
MIN_PIXELS = 200

sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QQVGA)
sensor.skip_frames(time=2000)
sensor.set_auto_gain(False)
sensor.set_auto_exposure(False)

# Optional IMU (OpenMV's lsm6dsox driver for the Nicla Vision). If this fails, camera only.
imu = None
try:
    from lsm6dsox import LSM6DSOX
    from machine import Pin, SPI
    imu = LSM6DSOX(SPI(5), cs=Pin("PF6", Pin.OUT_PP, Pin.PULL_UP))
except Exception:
    imu = None

def blob_pixels(b):
    p = b.pixels                       # a property on newer OpenMV firmware, a method on older
    return p() if callable(p) else p


previous = sensor.snapshot().copy()
moving = False
movement_count = 0
last_report = time.ticks_ms()
max_px = 0
acc_n = 0
acc_sum = 0.0
acc_sq = 0.0
gyro_sum = 0.0

while True:
    img = sensor.snapshot()
    current = img.copy()
    diff = current.copy()
    diff.difference(previous)
    blobs = diff.find_blobs([(MOTION_THRESHOLD, 255)], pixels_threshold=MIN_PIXELS,
                            area_threshold=MIN_PIXELS, merge=True)
    motion_pixels = sum(blob_pixels(b) for b in blobs)
    previous = current

    detected = motion_pixels > MIN_PIXELS
    if detected and not moving:
        moving = True
        movement_count += 1
    elif not detected and moving:
        moving = False
    if motion_pixels > max_px:
        max_px = motion_pixels

    if imu:
        try:
            ax, ay, az = imu.accel()
            gx, gy, gz = imu.gyro()
            a = math.sqrt(ax * ax + ay * ay + az * az)
            acc_sum += a
            acc_sq += a * a
            gyro_sum += math.sqrt(gx * gx + gy * gy + gz * gz)
            acc_n += 1
        except Exception:
            pass

    now = time.ticks_ms()
    if time.ticks_diff(now, last_report) >= 1000:
        last_report = now
        out = '{"timestamp_ms":%d,"motion_pixels":%d,"moving":%d,"movement_count":%d' % (
            now, max_px, 1 if moving else 0, movement_count)
        if acc_n:
            mean = acc_sum / acc_n
            var = max(0.0, acc_sq / acc_n - mean * mean)
            out += ',"acc_mean":%.4f,"acc_std":%.4f,"gyro_mean":%.2f' % (mean, math.sqrt(var), gyro_sum / acc_n)
        print(out + "}")
        max_px = 0
        acc_n = 0
        acc_sum = acc_sq = gyro_sum = 0.0

    time.sleep_ms(100)
