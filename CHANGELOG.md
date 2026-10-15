# Changelog

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

