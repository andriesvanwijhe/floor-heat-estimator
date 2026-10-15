# Floor Heat Estimator

An experimental Home Assistant custom integration for estimating heat released by an underfloor heating circuit during heating and cooldown.

Version 0.2.1 adds initial Recorder history import to the scheduled parameter fitting introduced in 0.2.0. Models can use manual coefficients or validated learned coefficients. This is the first modelling component of a future house thermal-analysis tool; it currently fits the floor circuit only.

## What it provides

- Linear and exponent-model heat output, in kW.
- Effective temperature for each model, in degrees C.
- Model status and time since the last valid temperature observation.
- Adjustable heat capacity, conductance, exponent, circulation threshold and settling delay.
- State restoration for short restarts, and local minute-sample CSV recording.
- Configurable rolling learning window and automatic refit interval (default 7 days).
- Fit-now button/action, preview mode, persistent learned parameters and fit-quality diagnostics.
- Initial detailed Recorder history import and an Import history and fit button/action.

## Model and assumptions

The temperature observation is `(supply + return) / 2`. It is accepted only in the configured space-heating mode, above the flow threshold, after continuous circulation for the settling period. Each accepted observation corrects both models to the measured mean-water temperature.

Between accepted observations:

```text
theta = effective_temperature - room_temperature
C * dT_effective/dt = heat_input - heat_output

Linear:    heat_output = K * max(theta, 0)
Power law: heat_output = Q5 * (max(theta, 0) / 5)^n
```

`C` is in kWh/K. Linear `K` is in kW/K. `Q5` is heat output in kW at a 5 K temperature difference; `n` is the exponent. The power-law coefficient is `Q5 / 5^n` in kW/K^n. Signed thermal input is preserved, including negative readings.

This is a heating-only model. Heat input is assumed zero outside the selected heating mode. It is unsuitable without modification if the circuit receives heat in other modes, or if measured heat-pump power includes buffer/distribution storage rather than heat delivered to the selected floor circuit.

**Mean water temperature is a proxy, not a measurement of slab temperature.** Estimated heat output includes the consequences of that approximation and any unmodelled downward/distribution losses. The initial defaults are provisional example fits from an early-season dataset and must be calibrated for each installation. Their presence does not establish accurate floor-to-room heat transfer.

Unknown and unavailable inputs are not replaced with zero. If an unobserved interval exceeds the maximum allowed data gap, the state is discarded until a valid temperature observation can initialize it again. Predictions become unavailable when the last observation exceeds the configured age limit. Short restart gaps use the last known input and room temperature; they cannot reconstruct changes that occurred while Home Assistant was stopped.

## Installation through HACS

1. In HACS, open Custom repositories.
2. Add `https://github.com/andriesvanwijhe/floor-heat-estimator`, category **Integration**.
3. Download **Floor Heat Estimator** and restart Home Assistant.
4. Under Settings > Devices & services, add **Floor Heat Estimator**.

The package targets Home Assistant 2025.1 or newer. Home Assistant framework checks are provided in GitHub Actions; local model tests do not replace an installation check on the actual Home Assistant version.

## Manual installation

Make a Home Assistant backup first. Copy the `custom_components/floor_heat_estimator` folder into `/config/custom_components/`, then restart Home Assistant and add the integration through Devices & services. Copy the folder's contents as supplied: `manifest.json` must be directly inside `/config/custom_components/floor_heat_estimator/`.

If using the release ZIP, extract its `custom_components` folder into `/config`.

## Setup

Select thermal heat input, room temperature, supply temperature, return temperature, circuit flow and operating-mode entities. Use actual thermal power, not electrical consumption. Enter the exact mode value for space heating, such as `hc`.

Accepted units:

| Input | Accepted units |
|---|---|
| Thermal power | W or kW |
| Circuit flow | l/h, l/min or m3/h, with common notation variants |
| Temperatures | degrees C or degrees F; missing units are interpreted as degrees C |

Flow thresholds are always entered in l/h; the integration converts source flow units. Temperature differences are always in K after conversion to degrees C.

The second setup page lets you edit model parameters and validity limits. Defaults include 100 l/h circulation, a 10-minute settling period, a 10-minute maximum data gap and a 24-hour maximum prediction age. Use the integration's options to change sources, the manual parameter baseline or learning settings later.

Model parameters appear as attributes on the heat-output entities and, since version 0.1.2, as separate diagnostic sensors:

| Sensor | Meaning | Unit |
|---|---|---|
| Linear conductance K | Heat-output slope of the linear model | kW/K |
| Linear thermal inertia C | Effective heat capacity for the linear model | kWh/K |
| Exponent thermal inertia C | Effective heat capacity for the exponent model | kWh/K |
| Heat output exponent | Fitted or configured power-law exponent n | dimensionless |
| Exponent heat output at 5 K | Q5, the normalized power-law gain | kW |
| Linear time constant | 60 * C / K, linear cooldown time scale | min |

These parameter sensors are read-only views of the active settings and remain available without live observations. They update after an accepted fit is applied or manual settings change. The `parameter_source` attribute reports `manual_configuration` or `learned_fit`. Manual baseline coefficients remain editable through integration options. Editing manual coefficients or source/measurement-gate settings supersedes stored learned coefficients; changing only learning duration or schedule preserves them.

The nonlinear coefficient can be calculated as `Q5 / 5^n` (also an attribute of the Q5 sensor). Its units depend on n, so Q5 is used as the parameter entity with a stable physical unit. There is no single constant nonlinear time constant: its response changes with temperature difference.

 Effective temperatures, status and observation age are diagnostic entities. Entity IDs are generated by Home Assistant from your configured device name.

## Data recording and privacy

If minute recording is enabled, daily UTC CSV files are written under:

```text
/config/floor_heat_estimator/<config-entry-id>/learning/<source-signature>/YYYY-MM-DD.csv
```

They include normalized source signals, validity, model status and estimates, preserving missing numeric readings as blank fields. Sampling occurs at approximately one-minute resolution and is separate from the more frequent source-change updates. Data is stored locally; the integration does not upload it. Recordings are not automatically deleted. Disable recording in options or manage old files when appropriate.

The integration does not change heat-pump controls or existing sensors. Fitting requires SciPy 1.18.1, installed automatically by Home Assistant. It requires no external database, MQTT broker or cloud account.

## Learning settings

| Setting | Default | Behavior |
|---|---|---|
| Enable scheduled learning | On | Queue a background fit when due; manual fitting also works when disabled |
| Training history window | 28 days | Rolling lookback, configurable from 2 to 90 days; not a required waiting period |
| Automatic refit interval | 7 days | 1 to 90 days between attempts, including unsuccessful attempts |
| Apply validated fitted parameters automatically | On | Off produces preview candidates without changing the active model |
| Maximum validation temperature error | 1.0 K | Maximum permitted RMSE on later, held-out circulation cycles |
| Record local minute samples | On | Required for scheduled learning; no estimates are used as training measurements |
| Import Recorder history once before the first automatic fit | On | Also runs once after upgrading to 0.2.1; imports only available detailed history |

The first automatic attempt is due about one minute after first setup with learning enabled. Subsequent attempts use the stored last-attempt time plus the configured interval. Restarts do not reset the schedule. An early insufficient-data attempt waits until the next interval; use **Fit now** after enough data has accumulated. Disabling recording also suspends automatic scheduling, including for configurations upgraded from older versions.

Fitting runs in an executor, with at most one numerical fit running per integration and one queued/running job per entry. The numerical worker has a 120-second budget and checks for unload/cancellation. It never changes heat-pump controls. Learned coefficients and reports are stored locally with Home Assistant's storage helper and restored after restart. Validated fits replace both models together; a rejected or interrupted attempt preserves the previously active coefficients. Live effective temperatures are preserved when new coefficients are applied.

### Manual fit and preview

Press **Fit now** on the Floor heat estimator device, or call the following action in Developer tools > Actions (also usable in automations):

```yaml
action: floor_heat_estimator.fit_now
data: {}
```

With multiple configurations, include `entry_id`, selectable in the action UI. The action queues a job and returns promptly. Read **Fit status** for completion; the optional action response only confirms queuing.

To fit without changing active coefficients:

```yaml
action: floor_heat_estimator.fit_now
data:
  apply: false
```

Omitting `apply` follows the configured application setting. Explicit `apply: true` applies candidates only if all validation checks pass. The **Fit status** report includes proposed coefficients, per-model training/validation error, baseline validation error, observation/cycle counts and rejection reasons. Parameter sensors continue showing the active values during a preview.

To return to the manually configured baseline:

```yaml
action: floor_heat_estimator.reset_parameters
data: {}
```

Reset does not remove training files or disable scheduled learning. Disable scheduled learning as well if you want to retain manual coefficients indefinitely.

### Existing recordings from version 0.1

Fitting uses normalized local minute CSV recordings, including imported local Recorder history when enabled (see Initial Recorder history import below). No cloud service is queried. Existing `0.1.x` recordings remain untouched in the parent folder but do not have source/measurement-gate provenance. They are excluded automatically. If their source entities and flow/mode/settling settings are unchanged, explicitly include them in a manual fit:

```yaml
action: floor_heat_estimator.fit_now
data:
  include_legacy: true
  apply: false
```

Inspect the report, then run with `apply: true` if appropriate. New recordings are partitioned by source entities and measurement gates so incompatible datasets cannot mix automatically. Changing those settings starts a separate training partition. Old files are retained and can be managed separately. A source changing its physical meaning or units without an entity-ID change cannot be detected from the signature; use a new configuration for a materially different circuit.

### Fit method and minimum evidence

The learner uses bounded, multi-start least squares on **open-loop predicted mean-water temperature** for both models. It recomputes measurement validity from raw flow, operating mode and continuous settling time; it never fits to the integration's own predicted heat output. Unknown driving inputs and gaps terminate a simulation segment. Signed heating input is retained; non-heating modes assume zero input to the floor as in the live model.

Input power and room temperature are time-weighted into approximately five-minute bins. Only valid measured bin endpoints are scored. Traces initialize at a valid measured temperature and are reseeded at most every six hours, not at each scored point. The earliest cycles train the models; the latest approximately 25% of cycles are held out. The validation segment is independently initialized at its first valid measured temperature. No validation targets are used in optimization.

Minimum evidence is four circulation cycles, 120 valid minute observations spanning at least 24 hours, at least 24 training and six held-out five-minute measured endpoints, varied heat input (0.5 kW range) and at least 1.5 K observed temperature-difference variation. These thresholds are screening criteria, not proof of accurate physical identification.

Candidates are rejected if optimization fails, a parameter lies within 1% of its transformed bound, the optimizer Jacobian is poorly conditioned (condition number >= 10,000), validation RMSE exceeds the configured ceiling, or validation is worse than the active model beyond a 5% allowance / 0.05 K numerical-noise floor. Both models must pass before either changes. Sparse or weak early-season data therefore leaves existing parameters intact.

The parameter bounds are the same physical ranges as configuration (C 0.01-100 kWh/K, linear K 0.001-10 kW/K, exponent-model Q5 0.005-100 kW, n 0.5-3); the nonlinear optimizer rejects extremely stiff Q5/C > 150 per hour before simulation. Fits at a bound need investigation rather than automatic application.

Fit status, last attempt, last applied fit, next automatic fit, both validation errors, observation count and cycle count are additional diagnostic entities. Their IDs and the fit-now button use the existing config-entry ID, so upgrading preserves the original 12 sensor IDs.

## Integration type and future house models

Version 0.2.0 uses Home Assistant's **service integration** classification, rather than helper. It appears under **Settings > Devices & services > Integrations** after restart. Existing configuration entries, domain (`floor_heat_estimator`), devices and entity unique IDs remain the same. Do not remove and re-add the integration to upgrade.

A helper was viable for the original small derived-sensor tool. A normal integration better accommodates background learning, persistent model versions, commands and diagnostics. Collection, pure model/fitting code, lifecycle/storage and HA entities now live in separate modules so additional model families can reuse the workflow.

The current learned C and K belong to a **water-temperature proxy model**, not to the whole building. This release does not implement a house heat-loss or building-inertia fit. The intended next model is a room/house energy balance with metered heating input, room temperature and outdoor temperature; solar/internal gains and ventilation can be added as measured inputs or explicit uncertainty. A subsequent coupled floor-room model can represent separate floor and building heat stores. Each model needs independent chronological validation, parameter identifiability checks and clearly labelled units. Estimated floor output must not be treated as independently measured ground truth for a house fit.

## Development and verification

```shell
python -m pip install scipy==1.18.1
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```

GitHub Actions additionally runs Home Assistant hassfest and HACS validation. Personal exported histories, recordings, credentials, and fitted prediction files are excluded from the repository.

Before applying estimates to control decisions, compare predicted cooldown and subsequent valid water observations over varied heating cycles. The linear and exponent models are alternatives; their agreement alone is not independent validation.



### Initial Recorder history import (0.2.1)

Before its first automatic fit, the integration imports available **detailed Recorder history** for the configured learning window. This also runs once after upgrading an existing installation to 0.2.1, even if a prior fit reported insufficient data. It starts in the background about one minute after loading, when scheduled learning and local recording are enabled. The setting **Import Recorder history once before the first automatic fit** disables this behaviour if desired.

Use the device's **Import history and fit** button to import again, or call:

```yaml
action: floor_heat_estimator.import_history_and_fit
data:
  apply: false  # optional: preview without changing active coefficients
```

The action queues the work; inspect **Fit status** for completion. Its `history_import` attribute reports the requested window, reconstructed sample count, complete sample count, first/last complete sample and entities with no usable history. The fit report separately gives valid observations/cycles and the exact reason if fitting cannot proceed.

Only the six configured entities are read, one day at a time in Recorder's database executor. Values are reconstructed on a minute grid using recorded state changes and historical units. Unchanged values are held; unavailable states are never interpolated or replaced with zero. Brief circulation/availability interruptions conservatively invalidate a minute, restarting settling. Clean Recorder-run boundaries exclude downtime; runs marked as incorrectly closed are conservatively excluded because their actual end is unknown. Recorder cannot reveal unrecorded sensor failures or recover purged history. Sensor configuration/meaning must have remained consistent over the imported period.

Imported CSVs live in `learning/<source-signature>/imported/`, separately from live recordings. Files are replaced atomically and deduplicated by minute, with live samples winning overlaps. Completion (including empty history or errors) is remembered across restart; use the manual action to retry after fixing Recorder or increasing the learning window. Changing source sensors or measurement gates permits a fresh automatic import. Changing only manual model parameters does not repeat the import.

Import reads the local Recorder database through Home Assistant and does not change Recorder configuration or retention. The available period may be shorter than the learning window (Recorder defaults to ten days of detailed history); hourly statistics are insufficient for circulation/settling reconstruction. Missing Recorder or import errors are reported while fitting can still use existing local recordings. Imported history does not bypass fitting quality checks or automatically guarantee acceptance.
