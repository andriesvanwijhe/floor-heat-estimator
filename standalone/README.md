# Standalone thermal-model service

This distribution runs **without Home Assistant**. It reuses the numerical code
from the integration and excludes HA adapters, entities, Recorder and UI code.
Default floor operation and identification use the exponent model only.

## Build and run

If you downloaded the standalone ZIP, install its `requirements.txt` and run
`python thermal_service.py request.json --output response.json --measure`
directly from the unpacked directory. The build command below is for source
checkouts of the repository.

Requires Python 3.12+ and SciPy 1.18.1 (which supplies NumPy). Use 64-bit Linux
and matched aarch64 wheels on an RK3326. No browser, desktop, GPU or container
is required. The service does not operate heat-pump outputs.

```sh
python standalone/build.py --output dist/standalone
python -m pip install -r dist/standalone/requirements.txt
python dist/standalone/thermal_service.py request.json --output response.json --measure
```

Operations are `dynamic`, `steady`, `fit_house`, and `fit_floor`. Each accepts
`{"operation": "dynamic", "request": {...}}`. The predictor's request contains:

- `house`: `ci`, `ce`, `rie`, `rea`, `solar_area`, `internal_gain`, `sigma_i`,
  `sigma_e`, `sigma_y`. Capacities are kWh/K, resistances K/kW, area m², gain kW.
- `floor`: `capacity` (kWh/K), `output_at_5k` (kW), `exponent`.
- `initial`: current `[room, envelope, floor]` temperatures in °C.
- `hours`: consecutive objects with `start` (UTC Unix seconds), `seconds`,
  `outdoor_C`, `solar_kW_m2`. Intervals are up to one hour and must be a multiple
  of five minutes. A partial first clock hour is supported.
- `target_C` (default 20), `maximum_power_kW` (default 8, **thermal**),
  `max_seconds` (default 600; maximum 600).

The planner optimizes up to 48 hours, reporting exactly the first 24 hours when
at least 24 hours were provided. `hourly` contains room/minimum/envelope/floor
temperatures, pump input, floor output, outdoor losses, solar/internal energy,
storage change, comfort shortfall and the energy-balance residual. Watts/m²
must be divided by 1000 for `solar_kW_m2`. A one-hour 3 kW input is 3 kWh;
partial intervals are integrated using their actual duration.

Run `standalone/benchmark.py` to create synthetic example requests and results.
`--private-history comparison.csv` additionally benchmarks house fitting on an
offline comparison export. That input and its benchmark artifacts stay private.
`fit_house` accepts the existing fitter request, capped at 3600 seconds.
`fit_floor` accepts `samples` (`Sample` fields), `now` (epoch), `settings` and
`max_seconds` (capped at 3600); it forces exponent-only operation.

## Numerical and operational behavior

- No future room assimilation, midnight resets or temperature clamping occurs
  inside a forecast. The initial state is supplied by the host application.
- Five-minute internal steps couple an exact constant-input house transition
  to a monotone implicit midpoint floor exchange. The same floor energy is
  removed from the floor and delivered to the room.
- The optimizer uses analytic control sensitivities and a bounded hourly
  heating schedule. Comfort below target has a large soft penalty. A cold
  start or insufficient capacity therefore returns `comfort_shortfall`, not a
  fictitious feasible schedule. `ready` permits at most 0.05 K model shortfall;
  this is a numerical planning tolerance, not an accuracy guarantee.
- Solar or stored heat can raise the room above target; no negative heating or
  artificial cooling is introduced. Signed outdoor exchange is retained.
- Runtime limits and failures return explicit non-ready results. Never execute
  a plan from `timeout`, `optimizer_failed`, or `error`.
- Run one fitting/prediction worker at a time, single-threaded BLAS. A supervisor
  should enforce the time limit externally, retain the last valid plan with its
  timestamp, and keep ordinary heat-pump control independent of this service.
- Present controls are hourly averages and assume modulation or duty cycling;
  compressor cycling, minimum output and DHW interruptions are not scheduled.

## Compilation

The generated `thermal_core` uses explicit imports and has an empty package
initializer. It can be compiled and bundled using Nuitka:

```sh
python -m pip install nuitka
python standalone/build.py --output dist/standalone --compile
```

Build on an **aarch64 Linux build machine** with a suitable C compiler for the
target Linux ABI. `--compile` uses standalone mode and one compilation job.
Compilation should happen on a development machine, not the memory-constrained
controller. No RK3326 executable is included: a target ABI/BSP has not yet been
selected and the compiled artifact has not been validated on that hardware.

Nuitka compilation retains the Python runtime and NumPy/SciPy dependencies; it
is **not a freestanding C/C++ firmware port** and should not be assumed to cut
RAM or speed up already-native SciPy operations. Remove Python/SciPy only after
a separately tested native optimizer/model port if the measured product budget
requires it. Compiler reference: https://nuitka.net/user-documentation/user-manual.html

## AIO26 hardware sizing

Specify a headless, maintained 64-bit Linux BSP, hardware watchdog/RTC, eMMC,
reliable networking, and the UART/USB interfaces required by the AIO26 design.
Thread still needs a suitable radio/host stack. Check actual pin multiplexing
and which ports are reserved by Bluetooth, console and boot configuration.

- **512 MB RAM:** recommended starting quotation for this standalone workload
  plus lean Linux, MQTT/Modbus, Thread and a lightweight LED/e-paper interface.
- **256 MB RAM:** a cost-down target requiring complete-system peak-RAM tests,
  including longest history, fitting, TLS connections and software updates.
- **1 GB RAM:** useful margin for additional services or a less-trimmed Linux
  image; no demonstrated requirement for the numerical model alone.
- **8 GB eMMC preferred, 4 GB conditional:** account for OS, native libraries,
  history, logs and two update slots. A stripped native-only future product can
  reduce storage, but cannot be sized from source-code file size alone.
- Use a binary ring log or SQLite on disk, and a bounded history window. Ninety
  days of one-minute records at 64 bytes/record is about 8 MiB of raw payload;
  Python objects, JSON, indexes and optimizer workspaces add memory.

RK3326 has four 64-bit Cortex-A35 cores up to 1.5 GHz; fitting/planning here
mainly uses one CPU core. Its GPU is not required. CPU feasibility is promising
under a 60-minute fit / 10-minute prediction allowance, but desktop timings do
not validate ARM timing, thermal throttling, long-term BSP maintenance or the
complete AIO application. Obtain an evaluation board and run the included
benchmark plus the complete workload before committing to a production BOM.

Official processor reference:
https://www.rock-chips.com/a/en/products/RK33_Series/2018/0514/900.html
