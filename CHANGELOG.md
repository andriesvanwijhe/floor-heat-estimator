# Changelog

## 0.5.1

- Recover bounded missing-input intervals between different known non-heating modes, such as domestic hot water and standby. Both have zero floor-circuit heat input under the heating-only model; their names need not match.
- Preserve known modes inside those intervals, never invent water observations, and record boundary modes plus the zero-input/held-room assumption in diagnostics. Unknown positive thermal input, a known heating mode inside the gap, heating/non-heating boundary transitions, long gaps and changing room temperatures remain rejected.
- Explain remaining initialization failures with the valid boundary states and the specific reason a gap cannot be bridged.
- Keep all floor-fit validation checks unchanged. A rejected calibration does not itself disable a valid running floor state.

## 0.5.0

- Preserve known heat/mode inputs at circulation transitions; invalidate water observations through the flow/settling gate instead. Version and refresh old Recorder imports.
- Run continuous floor temperatures in fitting, live estimation and house heat replay: initialize missing state only, with no resets at measurements, heat-pump restarts or arbitrary six-hour intervals. Preserve compatible startup state and recover missing state from the last valid raw measurement.
- Fit every valid heating observation with equal total weight per cycle. Hold out complete later cycles and validate temperature error, restart MAE, signed restart bias and restart RMSE before applying either floor model. Failed fits retain compatible active parameters.
- Add configurable exponent learning bounds (default 1.0–1.6), a seven-point profile with three starts per point, and bounded refinement. Report boundary-limited/weak exponent identification and publish a local SVG training-cost plot. Add a configurable floor fitting budget, initially five minutes.
- Add six floor temperature-error, last-restart-error and energy-balance diagnostic sensors. Report integrated heat input, emitted heat, storage changes and numerical balance residuals.
- Preserve interval heat energy into live house bins and historical house fitting with one frozen floor calibration. Flag existing house calibration for reassessment after this heat-input algorithm change.
- Add regression coverage for shutdown/restart import, continuous multi-cycle prediction, exponent bounds, equal cycle weights, startup snapshots, CSV upgrades and energy-preserving grid alignment.

## 0.4.1

- Fix underfloor startup replay getting permanently stuck on a transient unavailable input recorded during restart.
- Bridge short interior input gaps only when valid readings on both sides have identical operating mode, room temperature differs by no more than 0.5 K, and active-heating power agrees within the larger of 50 W and 5%. The total boundary-to-boundary interval must not exceed the configured maximum gap.
- Record every held-input estimation assumption in `initialization.estimated_gaps`. Never synthesize a measured water temperature, bridge an unknown tail, ignore contradictory known modes, or bridge long/changing-input gaps.
- Expose the exact failed timestamp, model status and missing fields when initialization cannot complete. Refresh diagnostic entities after failure.
- If Recorder import fails, still try retained local recordings; recovered history is not activated while current live inputs remain invalid.

## 0.4.0

- Initialize the underfloor model from its last valid measured supply/return mean and replay historical heat input and room temperature to now, including cooldown. Run recovery in a cancellable background task; report incomplete history instead of assuming zero power or a cold floor.
- Add sampling/record-length thermal-mode checks, configurable resistance/capacity priors, an independently fitted first-order validation benchmark, and an estimated segment-initial hidden-temperature offset. Normal second-order fits must improve held-out 6/24-hour predictions over the first-order benchmark.
- Provisional fits retain trusted prior dynamics when new capacities/resistance split are unidentified. Physical bounds and structural failures are no longer bypassed by Force; unsupported priors also prevent application.
- Add per-gain fixed/prior/free learning modes, defaulting to prior. Regularized gains can learn on short records and explicitly report prior dependence. Fixed gain intervals are not presented as learned confidence intervals.
- Split house entities/buttons onto a House device and keep floor entities on Underfloor. Preserve entity unique IDs. Add separate House/Underfloor configuration-menu paths that preserve the other function's settings.
- Save house state every minute and replay retained history/Recorder inputs on recovery, retaining midnight-only measurement correction. Initialization and missing-history diagnostics are exposed as attributes.
- Changing house inference settings invalidates older house calibrations while retaining raw history and floor calibration. Refit after updating. The heat-demand forecast remains steady-state; this release does not add heat-pump control.

## 0.3.3

- Anchor house heat loss to a matched 24/72-hour energy signature before fitting dynamics, including a room-storage nuisance term. Fit solar/internal gains only when the signature has sufficient duration and independent variation; otherwise provisional fits retain configured gains.
- Optimize overall heat loss and the resistance ratio directly within the energy support band. Add integrated physical energy-balance residuals to the dynamics objective so fitted process noise cannot conceal daily heat errors.
- Require withheld heating-energy accuracy as well as temperature validation for normal acceptance. Report the energy support assumptions, signed daily energy errors and conditional parameter uncertainty explicitly. Engineering support bands are not confidence intervals.
- Add an **Import and apply provisional house fit** device button. It imports history, forces fitting and applies a converged provisional result while preserving the energy anchor. No YAML is required.
- Keep old coefficients until a replacement is explicitly applied; retain history and existing entity IDs. The forecast remains steady-state in this release; hourly coupled heat-pump/floor scheduling is a separate extension.

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

