# Changelog

## 0.3.2

- Add explicit forced house fitting with provisional diagnostics, relaxed data requirements and preview by default. Activation requires `force: true` and `apply: true`; scheduled fits retain normal checks.
- Advance the live house twin every five minutes but correct from the thermostat only at local midnight. Initialize at startup or after missing drivers. Persist the last correction across restarts; validate forecasts using the same midnight correction policy.
- Estimate tomorrow's average and peak heating demand (kW) and energy (kWh) at a configurable room setpoint, default 20 °C. Reuse the existing Open-Meteo hourly forecast sensor; subtract solar and internal gains using the active house coefficients.
- Require complete tomorrow coverage, align preceding-hour mean GHI with temperature endpoints, handle 23/25-hour local days, and expose hourly predictions and forecast status as sensor attributes. Missing, stale or ambiguous forecasts remain unavailable.

## 0.3.1

- Add a fitted constant occupancy/electrical-device heat gain directly to the house interior node, with a 500 W manual starting value.
- Expose House internal heat gain in W and its approximate fitted confidence interval; add the manual value to house options (0–5000 W).
- Extend joint maximum-likelihood fitting and identifiability checks to six physical coefficients, allowing zero internal gain.
- Invalidate prior house calibrations under the changed heat-balance model, preserving floor operation and all training recordings.

## 0.3.0

- Add an optional house digital twin using the interior/envelope RC model in Bacher & Madsen (2011), Figure 1.
- Fit interior/envelope heat capacities, two thermal resistances, effective solar area and process/measurement noise by Kalman innovations maximum likelihood.
- Add hidden envelope temperature, filtered interior temperature, signed heat flows, solar gain, all five physical parameters and three transfer/loss coefficients as entities.
- Add approximate parameter intervals, conditional envelope-state uncertainty and independent 1/6/24-hour held-out forecast validation.
- Add separate house learning window, weekly refits, preview/manual/reset actions and a configurable isolated-process fitting time limit (default 15 minutes).
- Import house Recorder history and replay raw floor history under one frozen calibration for consistent heating input when using this entry's exponent output.
- Reject insufficient excitation, poorly identified parameters and failed baseline/forecast comparisons; retain accepted coefficients on errors and timeouts.
- Keep the house feature opt-in, existing floor entity IDs and configuration; add a third setup/options page.
- Use unique atomic temporary files for history imports and avoid subscribing to the integration's own calculated heat output.

## 0.2.1

- Import detailed Recorder history once before the first scheduled fit, including existing installations upgrading from 0.2.0.
- Add an Import history and fit button/action, with preview support and import coverage diagnostics.
- Query six configured sources in daily read-only batches, preserving historical units, unavailable states, settling and clean Recorder-run boundaries.
- Persist import completion by source/gate signature; local minute recordings take precedence over imported samples.
- Keep live estimation running when Recorder history is absent or import fails.

## 0.2.0

- Add configurable rolling training duration and scheduled refits (default every 7 days).
- Add a Fit now button/action, preview mode, manual-parameter reset and persistent learned coefficients.
- Validate both models on later circulation cycles before applying either candidate.
- Expose fit status, timestamps, validation errors and data-coverage diagnostics.
- Isolate recording history by source/gate signature; legacy recordings require explicit manual opt-in.
- Reclassify as a service integration while preserving existing config entries and entity IDs.
- Add SciPy 1.18.1 for executor-based nonlinear least-squares fitting.
- Restart settling after observation gaps; accept explicitly null Celsius units during configuration.


## 0.1.2

- Add diagnostic entities for conductance K, both thermal inertias C, exponent,
  exponent-model output at 5 K, and the linear time constant.
- Parameter entities remain available while source readings or estimates are unavailable.
- Show manual configuration as the parameter source; automatic fitting is not implemented.

## 0.1.1

- Keep the model-status sensor textual, without numeric display precision.
- Accept temperature sensors whose unit attribute is explicitly null as Celsius.

## 0.1.0

- Initial experimental linear and power-law heat-output estimators.
- UI configuration and options for source sensors and parameters.
- Circulation/mode gating, settling, missing-data handling and observation expiry.
- Short-gap model restoration and local minute CSV recording.
- Pure-model tests and Home Assistant/HACS validation workflows.

