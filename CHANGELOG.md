# Changelog

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

