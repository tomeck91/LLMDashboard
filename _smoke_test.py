"""Smoke test: verify all pynvml metrics the dashboard needs work on this machine."""
import pynvml

pynvml.nvmlInit()
count = pynvml.nvmlDeviceGetCount()
print(f"GPU count: {count}")
ok, fail = 0, 0

def check(label, fn):
    global ok, fail
    try:
        v = fn()
        print(f"  OK   {label}: {v}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        fail += 1

for i in range(count):
    h = pynvml.nvmlDeviceGetHandleByIndex(i)
    raw = pynvml.nvmlDeviceGetName(h)
    name = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    print(f"\nGPU {i}: {name}")
    check("uuid", lambda: pynvml.nvmlDeviceGetUUID(h))
    check("utilization.gpu %", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    check("utilization.memory %", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).memory)
    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
    check("mem used/total MiB", lambda: f"{mem.used // 1024**2} / {mem.total // 1024**2}")
    check("temperature C", lambda: pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
    check("power W", lambda: round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1))
    check("graphics clock MHz", lambda: pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS))
    check("memory clock MHz", lambda: pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM))
    try:
        check("fan speed %", lambda: pynvml.nvmlDeviceGetFanSpeed(h))
    except Exception as e:
        print(f"  SKIP fan speed (expected on some cards): {e}")

print(f"\nRESULT: {ok} ok, {fail} failed")
