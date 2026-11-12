# Floor Heat Estimator

## Dynamic prediction and standalone deployment (0.8.0)

The existing **tomorrow steady-state forecast and entity IDs are unchanged**.
The optional dynamic forecast couples the current two-node house model to the
exponent floor model. It starts from measured room and estimated envelope/floor
temperatures, optimizes up to 48 hours and publishes the next 24 hours. Solar
and stored heat may raise room temperature above the target. No future room
corrections or midnight resets occur inside the prediction.

On the House device, `Update dynamic forecast` runs a prediction manually.
House options configure automatic prediction (default every 15 minutes), target
temperature, maximum **thermal** pump power to the floor (default 8 kW), planning
horizon and runtime limit (up to 10 minutes). Configure the power limit for the
actual installation. This release provides forecasts, not actuator commands.

New sensors are `House dynamic forecast status`, `House dynamic average heat
load`, `House dynamic heating energy`, `House forecast minimum room temperature`
and `House forecast maximum room temperature`. The dynamic heat-load entity contains an
`hourly` attribute for plotting, including pump input, floor output, room/minimum
temperature, envelope/floor temperatures, outdoor losses and stored-energy change.
Totals describe the next 24 hours, not the extended optimization horizon. An
initial/future comfort deficit is reported as `comfort_shortfall`; failed or timed
out optimizations do not publish a new schedule. Results become unavailable when
stale. Dynamic prediction requires this integration's exponent floor heat source.

Example Plotly traces (select the actual entity ID from Developer tools):

```yaml
type: custom:plotly-graph
title: Dynamic heating forecast
raw_plotly_config: true
entities:
  - entity: sensor.floor_heat_estimator_house_dynamic_average_heat_load
    name: Pump thermal input
    x: $ex (hass.states['sensor.floor_heat_estimator_house_dynamic_average_heat_load']?.attributes.hourly || []).map(r => r.start)
    'y': $ex (hass.states['sensor.floor_heat_estimator_house_dynamic_average_heat_load']?.attributes.hourly || []).map(r => r.pump_heat_input_kW)
    mode: lines
    line:
      shape: hv
layout:
  yaxis:
    title: Thermal power (kW)
```

The exponent floor model is now the default live/learning path. It is no longer
rejected because an unused linear model fails validation. `Enable legacy linear
floor model` restores the older comparison entities and both-model acceptance.
Existing linear registry entries are not deleted; they are inactive while the
compatibility mode is off. Exponent state restoration works independently and
accepts existing snapshots. Manual linear coefficients remain stored for this
optional mode.

For a product without Home Assistant, see [standalone/README.md](standalone/README.md).
It builds a numerical-only distribution with JSON operations for steady/dynamic
prediction and house/exponent-floor fitting, plus an optional Nuitka build recipe.
No Home Assistant, browser, container or graphical desktop is needed. Fitting
limits can be set to 60 minutes; prediction has a 10-minute limit. The integration
serializes fitting/prediction workers and limits numerical-library threads.

Dynamic plans are conditional on the selected fit, floor calibration, forecast
and initial states. Hourly average thermal powers assume modulation/duty cycling;
compressor minimum run times, DHW interruptions and tariffs are not optimized.

An experimental Home Assistant custom integration for estimating heat released by an underfloor heating circuit during heating and cooldown.

Version 0.7.0 adds solar-uncertainty weighting for house thermal identification, a separate gain-only fitting stage, and a signed heat-imbalance diagnostic. House dynamics use continuous temperature simulations, fixed 30/60/180-minute temperature-change errors and daily energy balance. Total thermal resistance stays anchored to the measured energy signature. Floor fitting and live midnight correction are unchanged. The house model follows Bacher & Madsen (2011), Figure 1, with fitted constant internal heat gain (initially 500 W). It includes initial Recorder history import, separate house fitting settings, hidden envelope temperature and physical-parameter entities. Models can use manual coefficients or validated learned coefficients. Floor and house identification remain separate stages of the thermal-analysis tool.

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

The temperature observation is `(supply + return) / 2`. It is accepted only in the configured space-heating mode, above the flow threshold, after continuous circulation for the settling period. Since v0.5.0, a valid observation initializes a missing state only. Subsequent observations validate the predicted temperature; they do not reset the state or inject correction energy, including at heat-pump restarts. The status `observed` means a valid measurement is available, not that the prediction was overwritten.

The model runs continuously:

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

## Learning settings (v0.5.0)

Under **Configure > Underfloor heating**, continue past source selection to model and learning settings. The rolling training window is 2–90 days; automatic refitting defaults to every seven days. The fitted exponent range defaults to **1.0–1.6** and can be changed within 0.5–3.0 (minimum must be below maximum). The manual exponent is a starting/fallback value, independent of the learning bounds. A stored learned calibration outside the new learning range is not reactivated. Other compatible accepted coefficients survive failed fits. Both floor models must pass before either update is applied.

The background floor worker has a configurable time budget, initially **five minutes** (1–30 minutes). Larger data windows and slower machines can require a higher budget. A timeout preserves active parameters; it does not apply a partial exponent scan.

### Continuous identification and cycle validation

Water measurements are valid only in `hc` (or the configured active mode), above the flow threshold, after settling. Every valid heating measurement is scored. Room temperature and signed thermal input drive continuous prediction through settling, cooldown and restart. Off-period water temperatures are never targets. Histories are seeded at the first valid observation and after genuine missing-input gaps only; there are no periodic six-hour or per-reading resets. During off periods, inputs are integrated in energy-preserving bins up to five minutes long.

The least-squares objective is the **mean of the per-cycle mean squared temperature errors**. Each scored cycle therefore has equal total weight, without adding a separate restart penalty. Restart errors are the measured minus predicted mean water temperature at the first valid settled reading in a new circulation cycle. Heat delivered during settling is included. Positive error means the prediction was too cold.

Eligibility requires at least **five complete circulation cycles**, 120 valid readings spanning 24 hours, 24 training and six held-out readings, varied input (0.5 kW) and at least 1.5 K observed temperature variation. A cycle must end before the data boundary. The latest cycles are held out, retaining at least three training and two validation cycles. At least one held-out restart must have a continuous prediction from the preceding cycle; a gap that causes initialization cannot be counted as a successful restart prediction.

For each model, both the cycle-balanced validation RMSE and restart RMSE must pass the configured temperature-error ceiling. Validation RMSE, restart MAE and absolute signed restart bias must also not worsen the active-parameter baseline by more than 5%, with a 0.05 K floor. All comparisons use the same held-out histories. Physical capacity/gain bounds and local capacity/gain conditioning remain mandatory. Validation data do not select the optimizer's starting point or exponent.

### Exponent scan and plot

Seven evenly spaced fixed exponent values cover the configured range. Capacity and heat output at 5 K are fitted from **three starting points** at each value, followed by a bounded refinement of the best training solution. This reduces dependence on a single local optimization; it is not a guarantee of a global optimum.

**Fit status** attributes contain `report.exponent_profile`, the best refined point, training and validation metrics, failed checks, and `report.exponent_profile_plot`. Open that relative plot URL on your Home Assistant host, for example `http://homeassistant.local:8123/local/floor_heat_estimator/<entry-id>/exponent-profile.svg`. The plot is replaced after a completed scan and contains derived calibration costs only. It is a local static file under `/config/www/floor_heat_estimator/<entry-id>/` and can also be used in a dashboard picture card.

`boundary_limited` means the optimum is within 1% of an exponent bound. `weakly_identified` means grid values within the larger of 0.02 K or 5% of the best training RMSE span at least half the configured range. Otherwise the profile is labeled `resolved`. These are engineering sensitivity labels, **not statistical confidence intervals**. A boundary-limited exponent can still be applied if every predictive validation check passes; the report continues to flag its uncertainty.

### Observation and energy diagnostics

Six additional Underfloor diagnostic entities show linear/exponent temperature errors, the last restart errors and energy-balance residuals. Temperature error compares measurements to prediction without changing the state. A small energy residual checks numerical bookkeeping, **not physical accuracy**. Mean water temperature remains an effective-floor proxy.

For an uninterrupted segment and fixed capacity:

```text
heat_pump_energy - floor_to_house_energy = C * (T_end - T_start)
```

The floor estimator integrates emitted energy alongside its temperature state. The house runtime receives mean power over the correct preceding interval, and house fitting reconstructs the full raw floor history with one frozen calibration. Floor and house time grids are split at interval boundaries before averaging, preserving reconstructed emitted energy. `House fit status > report.floor_replay_energy_balance` reports model totals across continuous segments. Missing driving history and expired observation periods do not become valid house heat inputs.

The algorithm change versions the floor heat-input calibration. Existing compatible floor coefficients are retained; existing house coefficients are flagged `floor_calibration_changed` and scheduled for reassessment. A rejected new house fit retains its previous compatible parameters and the warning. After upgrading, **Import history and fit** reconstructs old imported transitions with the corrected measurement gates; then **Import house history and fit** reconstructs house heat input and refits the house. With initial automatic import enabled, the reconstruction-version change schedules those imports automatically.

Startup still prefers a compatible stored continuous state. If unavailable, recovery finds the last valid raw water measurement and advances from there through historical drivers to now. This is an initialization after missing state, not a normal restart correction. Bounded input-gap estimates are explicitly reported; genuinely unavailable history is not invented.

## Integration type and future house models

Version 0.2.0 uses Home Assistant's **service integration** classification, rather than helper. It appears under **Settings > Devices & services > Integrations** after restart. Existing configuration entries, domain (`floor_heat_estimator`), devices and entity unique IDs remain the same. Do not remove and re-add the integration to upgrade.

A helper was viable for the original small derived-sensor tool. A normal integration better accommodates background learning, persistent model versions, commands and diagnostics. Collection, pure model/fitting code, lifecycle/storage and HA entities now live in separate modules so additional model families can reuse the workflow.

The current learned C and K belong to a **water-temperature proxy model**, not to the whole building. The optional house model below estimates house heat loss and inertia separately. A future coupled floor-room model could represent additional physical stores and losses. Each model needs independent chronological validation, parameter identifiability checks and clearly labelled units. Estimated floor output must not be treated as independently measured ground truth for a house fit.

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

Only the six configured entities are read, one day at a time in Recorder's database executor. Values are reconstructed on a minute grid using recorded state changes and historical units. Unchanged values are held; unavailable states are never interpolated or replaced with zero. Brief circulation interruptions conservatively invalidate a water-observation minute and restart settling, while retaining known mode and heat input. Actual unavailable driving inputs remain missing. Clean Recorder-run boundaries exclude downtime; runs marked as incorrectly closed are conservatively excluded because their actual end is unknown. Recorder cannot reveal unrecorded sensor failures or recover purged history. Sensor configuration/meaning must have remained consistent over the imported period.

Imported CSVs live in `learning/<source-signature>/imported/`, separately from live recordings. Files are replaced atomically and deduplicated by minute, with live samples winning overlaps. Completion (including empty history or errors) is remembered across restart; use the manual action to retry after fixing Recorder or increasing the learning window. Changing source sensors or measurement gates permits a fresh automatic import. Changing only manual model parameters does not repeat the import.

Import reads the local Recorder database through Home Assistant and does not change Recorder configuration or retention. The available period may be shorter than the learning window (Recorder defaults to ten days of detailed history); hourly statistics are insufficient for circulation/settling reconstruction. Missing Recorder or import errors are reported while fitting can still use existing local recordings. Imported history does not bypass fitting quality checks or automatically guarantee acceptance.


## House digital twin (0.3.0)

The optional house model uses the RC network in **Figure 1**, equations (1)–(3), of [Bacher & Madsen, Identifying suitable models for the heat dynamics of buildings (2011)](https://henrikmadsen.org/wp-content/uploads/2014/05/Journal_article_-_2011_-_Identifying_suitable_models_for_the_heat_dynamics_of_buildings.pdf). It is a two-state interior/envelope model, not the five-state model elsewhere in that paper and not an implementation of automatic model-structure selection.

```text
                 Q_floor + A_solar × G + Q_internal
                           ↓
                  Ti [Ci] ── Rie ── Te [Ce] ── Rea ── Ta

Ci dTi/dt = (Te - Ti)/Rie + Q_floor + A_solar G + Q_internal
Ce dTe/dt = (Ti - Te)/Rie + (Ta - Te)/Rea
observed room temperature = Ti + measurement noise
```

The model includes independent interior/envelope temperature process noise and room measurement noise. All three noise scales are fitted as nuisance parameters. Time is hours, power kW, capacities kWh/K and resistances K/kW. `G` is internally kW/m²; the fitted area is in m². Exact constant-input state transitions and process-noise covariance integrals are used rather than Euler integration.

### Enable after upgrading

1. Update in HACS and restart Home Assistant.
2. Open **Settings → Devices & services → Floor Heat Estimator → Configure**.
3. Keep your existing floor source sensors and settings, then continue to the new **House digital twin** page.
4. Enable the house model and confirm the heat, outdoor-temperature and solar entities. The existing room-temperature entity from the first page is the interior thermometer.
5. Save. After about two minutes the first background history import/fit is due when house learning and local recording are enabled. Inspect **House fit status**. Use **Import house history and fit** to retry manually.

Existing entries default to house disabled, preserving the existing floor behavior. The offered heat source is `sensor.floor_heat_estimator_exponent_heat_output`, outdoor source `sensor.ebusd_broadcast_outsidetemp_2`, and solar source `sensor.open_meteo_global_solar_radiation`; choose the actual entity IDs in your installation if renamed. Every configuration keeps its own parameters, histories and diagnostics.

The house model consumes the heat actually released by the floor, including cooldown. It does **not** apply another space-heating/flow gate to that output. If its selected heat source belongs to the same integration entry, live input uses the same newly calculated exponent output without a sensor-publication delay or state-change feedback subscription. Sources are recorded once per minute and live filtering processes completed five-minute bins.

### House settings

| Setting | Default | Meaning |
|---|---|---|
| House digital twin | Off | Opt-in for existing and new configurations |
| House training window | 28 days | Configurable 7–90 days |
| House refit interval | 7 days | Independent of floor refitting; configurable 1–90 days |
| House fitting time limit | 15 minutes | Configurable 1–60 minutes for the numerical worker |
| Maximum validation error | 1 K | Applied to both 6-hour and 24-hour held-out forecast RMSE |
| Maximum source gap | 10 minutes | Larger gaps break input sequences; incomplete driver bins are rejected |
| Scheduled house fitting | On | Requires local recording; can be disabled independently |
| Apply fitted house parameters | On | Disable for preview-only candidate reports |
| Initial house history import | On | Imports detailed local history once before first automatic fit |

Manual starting guesses are Ci=5 kWh/K, Ce=30 kWh/K, Rie=1 K/kW, Rea=4 K/kW, solar area=10 m², and combined internal heat gain=500 W. They are **not fitted properties of your house**. Parameter entities explicitly label `manual_configuration` until a validated fit is applied. Editing those manual values resets the active house calibration; editing only the learning window or schedule preserves it.

### History and heat-input provenance

Detailed Recorder history of room temperature, outdoor temperature, heating output and solar irradiance is imported in daily read-only batches. Recorder downtime and incorrectly closed runs are treated conservatively; missing values remain missing. Five-minute bins integrate held power/solar/outdoor values over actual seconds and use an endpoint room reading available by that endpoint. Missing solar is never interpreted as a dark night. Source gaps or calibration changes break usable sequences. Hourly statistics cannot reconstruct detailed source availability.

When the selected heating entity belongs to this entry, house fitting replays the stored **raw floor sensor history** with one frozen floor calibration, creating a consistent input across the window. Initial house import also attempts to import the necessary raw floor history. Raw replay requires that the configured floor sources/gates describe that historical period. Missing raw readings remain missing even if an old estimated-output sensor had a value. This avoids silently combining heating estimates produced by different floor calibrations.

For an external heating entity, use its recorded values. A `calibration_signature` attribute is honored if provided, with changes splitting bins. Unversioned external heat is explicitly reported as calibration-not-verified. Changing source sensors or gates partitions histories. A new floor calibration triggers a new house fit attempt when automatic learning is on. Until a replacement passes, the accepted model remains active and the entity attribute `floor_calibration_changed` flags the mismatch.

House minute CSVs live in `/config/floor_heat_estimator/<entry-id>/house/<source-signature>/`. Imported CSVs are in its `imported/` subfolder. Live samples win minute overlaps. No personal history is uploaded, no Recorder configuration is changed, and no automatic history-file retention is imposed. The available period is limited by Recorder retention (often ten days) and how long the new solar entity has existed. Failed/empty imports are remembered; use the import button/action to retry after fixing the data source.

### Fitting and validation

House fitting first estimates a low-frequency energy signature. The dynamic fit minimizes **continuous simulated temperature error**, **temperature-change error at fixed 30/60/180-minute lags**, and **signed daily heat-balance error**. Every trial uses the same chronological training data, availability masks, segment boundaries, time pairs, energy windows and calendar-day weights. No sunny/cloudy classification, heating-cycle detector, parameter-dependent data selection, or measurement/midnight correction is used in the fitting simulation. Missing room targets do not interrupt propagation; genuinely missing driving inputs or time gaps do.

The signature constrains **Rie + Rea = 1/H**, not Rea alone. The optimizer adjusts log H within its engineering support and log(Rie/Rea), alongside log Ci, log Ce, log effective solar area and linear internal heat gain (0–5 kW). H also has a soft prior centered on the signature. Short-period response supplies information about the fast dynamics; longer trajectories supply information about slower storage. These data do not necessarily identify each capacity and resistance independently. Solar/internal learning modes remain fixed/prior/free. Prior mode uses a soft configured-gain penalty; the former 50–150% solar and ±500 W gain constraints no longer restrict dynamic fitting. Physical bounds remain in force.

For each continuous segment, room temperature initializes once and the initial hidden-envelope offset is a regularized nuisance state (5 K prior scale). Its least-squares optimum is calculated analytically for each physical candidate. This avoids adding optimizer dimensions when history contains many gaps. Offsets above 15 K prevent application. No temperature is reset when heating starts, a measurement arrives, or midnight passes **during fitting**. Live operation still uses its existing midnight correction.

Objective scales are 0.25 K for absolute temperature, 0.10/0.15/0.25 K for 30/60/180-minute temperature changes, and 0.15 kW for mean heat-balance error. Each represented UTC calendar day has equal temperature weight, the three change terms share one group weight, and overlapping energy windows are weighted by represented duration. Scales are fixed engineering weights, reported in `objective_scales`; they are not independent measurement-noise estimates. A bounded trust-region least-squares optimizer runs from three fixed starting points with identical data; the best converged training objective wins. The configured fitting deadline and isolated worker process remain in effect.

Noise parameters are inherited from the active/configured model, **not fitted to absorb physical-model errors**. They continue to govern live Kalman state uncertainty and midnight corrections; this release does not newly calibrate that uncertainty.

Initial eligibility requires at least 6.9 days of span and 80% of seven days' measured five-minute observations. The latest 25% of the window, or at least two days, is withheld. There must be at least three training days and two validation days at 80% coverage. Training must include at least 0.5 K room variation, 3 K outdoor variation, 0.5 kW heat-input variation, 50 W/m² solar variation, and at least two hours each of sunny and dark bins. These are screening rules, not evidence of unique physical parameters by themselves.

Acceptance requires convergence, physical parameters away from bounds (except an admissible zero internal gain), adequate local residual sensitivity, supported two-state dynamics, and later-data validation. The same fixed chronological split is used for all candidates; validation never selects optimizer starts or initial hidden-state offsets. The full continuous validation trajectory reuses only initial offsets estimated on training data. A new genuine validation segment starts from its first room reading and equilibrium Te, without estimating an offset from future validation targets.

Reports expose `training_simulation`, `validation_simulation`, and `baseline_validation_simulation`. Each includes RMSE, bias, and `changes` keyed by minutes (`30`, `60`, `180`), with change-error RMSE, count and RMS amplitude ratio (predicted/observed). An amplitude ratio near one indicates matching variation magnitude; it is not a phase or accuracy score by itself. Changes are evaluated on fixed time pairs, not detected heating cycles. Normal acceptance requires continuous validation RMSE within the configured ceiling and no more than 5% worse than the active model (0.1 K floor), and change RMSE no more than 5% worse (0.05 K floor, at least 24 pairs per lag).

Existing conditional 1/6/24-hour forecast and signed heating-energy validation remain required. Forecasts use actual historical future drivers and no future room corrections; live-like forecast origins use the midnight-only correction policy. The first-order comparator now uses the same correction policy instead of seeding every forecast at the measured temperature. Each horizon needs four origins. Both 6/24-hour forecasts must meet the ceiling, active-model and persistence comparisons. Every failed check is listed in the rejection reason; a missing continuous validation day is not silently interpreted as successful energy or 24-hour validation.

Local column-scaled residual sensitivity is an identifiability diagnostic. The new composite objective uses correlated time differences, overlapping energy windows and engineering scales. Its curvature is **not a likelihood**, so new fits do not publish approximate statistical 95% parameter intervals. `approximate_confidence_intervals` is empty and the interpretation is explicit. Old accepted calibration intervals remain until a replacement is applied. State standard uncertainty remains a conditional Kalman quantity and excludes parameter/input uncertainty.

Rejected, insufficient, timed-out and failed fits preserve the active coefficients. Preview reports do not replace active coefficients or their confidence intervals. Learned values are verified in persistent storage before activation. A house fit is discarded if the floor calibration changed while it ran. Short-restart model state is saved separately; stale or incompatible snapshots are discarded.

### House entities

Enabling adds 24 diagnostic sensors, three tomorrow-demand sensors and four buttons on the existing device. Existing floor entity unique IDs are unchanged.

| Entity name | Unit / interpretation |
|---|---|
| House heat imbalance | W; unexplained observed interior heat balance over a complete 30/60-minute window |
| House envelope temperature | °C; inferred hidden Te, not a measured wall temperature |
| House estimated interior temperature | °C; filtered Ti, alongside the original thermometer |
| House envelope temperature standard uncertainty | K; conditional Kalman state standard deviation |
| House estimated solar gain | kW; effective area × irradiance |
| House interior to envelope heat flow | kW; (Ti−Te)/Rie, signed |
| House envelope to outdoor heat flow | kW; (Te−Ta)/Rea, signed |
| House interior / envelope heat capacity | kWh/K; Ci and Ce |
| House interior envelope / envelope outdoor resistance | K/kW; Rie and Rea |
| House effective solar area | m²; fitted solar-gain factor |
| House internal heat gain | W; fitted combined constant gain from occupants and electrical devices |
| House interior envelope / envelope outdoor transfer coefficient | W/K; 1000/Rie and 1000/Rea |
| House overall heat loss coefficient | W/K; 1000/(Rie+Rea) |
| House fit status | Latest attempt, reason, input provenance, coverage and candidate report |
| House last fit attempt / last applied fit / next automatic fit | UTC timestamps |
| House 1 / 6 / 24 hour validation error | K; temperature prediction RMSE |
| House fit observation count | Measured five-minute observations in the last attempt |

Each physical-parameter entity has `parameter_source` and last-applied-fit attributes. Approximate 95% intervals are attached to accepted physical parameters; transfer-coefficient intervals are transformed from the corresponding resistance intervals. The overall heat-loss coefficient is not a per-area U-value, and it does not separate fabric from ventilation losses. Heat capacities and Te are effective lumped quantities for this model.

### House actions

The device buttons are **Fit house now**, **Import house history and fit**, **Import and apply provisional house fit**, and **Restore manual house parameters**. Corresponding actions work in Developer tools and automations:

```yaml
action: floor_heat_estimator.import_house_history_and_fit
data:
  apply: false  # optional: preview; omit to use the house setting
```

```yaml
action: floor_heat_estimator.fit_house_now
data: {}
```

```yaml
action: floor_heat_estimator.reset_house_parameters
data: {}
```

Specify `entry_id` when multiple configurations are loaded. Actions queue work and return promptly; **House fit status** reports completion. House and floor fits share a per-integration lock. House numerical optimization runs in a separate Python process with numerical-library threads limited to one. Timeout and unload terminate the worker; live model updates remain in Home Assistant. The fitting limit excludes history import/preparation. Import errors are reported and fitting can still use existing local files.

### Interpretation and limitations

The model treats the thermostat as representative of the modelled interior zone. A single fitted constant now accounts for average occupancy and electrical-device heat. Time-varying gains from other heaters, cooking or occupants, open windows, changing ventilation and weather-data errors can still bias its coefficients. Heating output is itself a floor-model estimate that uses room temperature; this introduces input uncertainty and dependence that the independent-noise model only approximates. Kalman state uncertainty does not include every source of uncertainty; the new composite fit does not publish statistical parameter confidence intervals.

The effective solar area includes orientation, shading and transmission effects; it is not the actual window area. Confirm that the solar sensor reports irradiance with meaningful observation timestamps. W/m² and kW/m² are supported; energy totals such as kWh/m² are rejected. Values are held according to their recorded timestamps: preceding-hour averages are **not** automatically shifted or deconvolved, and coarse source timing can bias the fitted solar response. Purged or never-recorded solar history cannot be reconstructed by the integration.

Synthetic tests verify exact propagation against independent matrix calculations, continuous trajectories without observation resets, missing-input boundaries, analytic hidden-state initialization, physical recovery, short-cycle amplitude and held-out data isolation. These checks do not establish accuracy on your house. Runtime depends on coverage and convergence; use elapsed-time diagnostics and the configurable time limit on your installation.


### Constant internal gains (0.3.1)

`Q_internal` represents combined average heat released by occupants and electrical devices. It is connected directly to the **interior node Ti/Ci**, alongside floor heat and solar gain. The envelope receives that energy through Rie; there is no second gain applied directly to Ce.

The manual initial value is **500 W**. Configure it as **Manual occupancy and electrical heat gain (W)** on the house options page (0–5000 W). Internally the model uses kW. **House internal heat gain** exposes the active value in W, its parameter source and last-applied-fit time. Older likelihood-fit calibrations may also carry a conditional approximate 95% interval; v0.6.0 composite fits do not generate one. It is one constant over a fitted window, not a live occupancy sensor or an estimate of individual appliance consumption.

The fitter now identifies six physical coefficients jointly: Ci, Ce, Rie, Rea, effective solar area and internal gain. All existing data-coverage, excitation and held-out forecast requirements remain. The additional coefficient can be correlated with solar gain and heat loss; poorly identifiable fits are rejected while the prior/manual coefficients remain active.

The upgrade preserves floor calibration, manual house settings, recordings and source history. House calibrations learned with the old zero-internal-gain model are invalidated instead of silently adding 500 W on top of coefficients that may have absorbed those gains. The model starts from the configured manual house coefficients plus 500 W (unless overridden) and schedules a fresh fit when learning is enabled. Editing the manual internal-gain starting value also invalidates active learned house coefficients, like other manual house parameter edits.


### Midnight correction, provisional fits and tomorrow demand (v0.3.2)

The live house model still integrates measured heat, outdoor temperature and solar input every five minutes. It only performs a Kalman room-temperature correction at the first valid completed midnight bin in Home Assistant's configured timezone. If the midnight room measurement is missing, it retries during the midnight hour, once per local date. This is a weighted Kalman correction, not an exact reset to the thermostat. Startup and recovery after missing driving inputs initialize from the room reading, including during the day. A brief restart preserves the last correction; upgrades discard old five-minute-corrected live state but retain fitted coefficients and training history.

Fitting uses five-minute observations as targets of the continuous-simulation objective described above. Retrospective forecast validation starts from a twin replayed with midnight-only corrections, matching live operation. Future room readings never correct an individual forecast trajectory.

Both house fit actions accept `force: true`. Normal eligibility and quality gates are then bypassed and explicitly listed in `bypassed_checks`; parameters are **provisional**, not validated. The irreducible minimum now includes a complete measured 24-hour training window, 96 measured five-minute points, and 24 measured training targets after segment initialization. Since the latest quarter is withheld in forced fits, this normally requires at least 32 total hours of usable data. Optimization must converge to a finite solution within physical bounds and the configured time limit. A forced fit defaults to preview even if automatic application is enabled. To activate it explicitly:

```yaml
action: floor_heat_estimator.import_house_history_and_fit
data:
  force: true
  apply: true
```

Use `floor_heat_estimator.fit_house_now` to reuse already recorded/imported samples. Active provisional coefficients remain visibly labelled `forced_provisional_fit` after preview attempts or restarts. Scheduled fits always retain the normal evidence and validation gates. This force option applies to the house model; floor-model fitting is unchanged.

In the third integration options page select **Open-Meteo hourly forecast array sensor**, default `sensor.open_meteo_48h_hourly_forecast_raw`, and the desired room setpoint (default **20 °C**). No extra API call is made. The selected sensor must expose equal-length `time`, `temperature_2m` (°C) and `shortwave_radiation` (W/m²) arrays. An optional `timezone` attribute identifies offset-free timestamps; otherwise Home Assistant's timezone is assumed. Explicit offsets or Unix seconds are preferred, especially across daylight-saving changes. A forecast not updated for twelve hours is unavailable; a source refreshed with old data is only as reliable as that source.

The equation for each hour is:

`Q_required = max(0, (T_target - T_outdoor) / (Rie + Rea) - A_solar * GHI / 1000 - Q_internal)`

Power is kW. GHI is Open-Meteo's **preceding-hour mean**, paired with the average of outdoor temperature at both ends of that interval. Tomorrow is the full next local calendar day (23, 24 or 25 hours), including its final endpoint. Forecasts therefore need coverage through midnight after tomorrow. Missing, negative or invalid GHI is not replaced by zero; incomplete or ambiguous coverage makes the prediction unavailable.

New sensors: **House tomorrow average heat load** (kW), **House tomorrow peak heat load** (kW), and **House tomorrow heating energy** (kWh). Their attributes include the target date, setpoint, forecast status, hourly loads and Open-Meteo attribution. Values recalculate each minute and after parameter changes, using active learned/provisional coefficients or the manual starting coefficients when no fit is active. The prediction is required thermal heat delivered to the house, not heat-pump electrical consumption.

This steady-state calculation ignores the current interior/envelope state and heat capacities. It estimates the heating needed to hold the setpoint without stored-heat effects or a warm-up allowance; it does not guarantee the actual room remains at that temperature. Gains exceeding losses yield zero heating rather than negative demand. This is a forecast only and does not control the heat pump.

Forecast variable conventions: [Open-Meteo documentation](https://open-meteo.com/en/docs).


### Energy-anchored house fitting (v0.3.3)

This release changes house fitting, not floor calibration or the steady-state forecast method. Updating does not overwrite active coefficients; run a new fit after updating.

Stage 1 uses matched complete 24-hour windows starting every six hours and 72-hour windows starting every day, entirely within the training segment. Missing observations or input gaps invalidate a window; windows never cross the validation boundary. The regression is:

`mean(Qfloor) = H * mean(Troom - Toutdoor) - A * mean(GHI) - Qinternal + Cproxy * delta(Troom)/hours`

`H` is kW/K, power is kW, GHI is kW/m² and `Cproxy` is kWh/K. The last term allows observed room warming/cooling to affect the signature. This **storage proxy is an approximation**, not a separately identified envelope temperature or published heat capacity. A 72-hour window is not assumed perfectly steady.

Joint gain estimation requires at least five training days, twelve aggregate windows and normalized design-matrix condition below 100. If that evidence is absent, a normal fit stops with `poor_energy_identification`; a forced provisional fit instead fixes solar area and internal gain to the **configured manual values**, not the previous possibly unstable fit. It then estimates conditional heat loss and a storage proxy. This assumption is explicitly reported. More data cannot separate perfectly collinear weather and gains.

The heat-loss support half-width is the larger of 25% of estimated H and twice the aggregate residual/model-error floor divided by RMS indoor/outdoor temperature difference (minimum residual floor 0.1 kW). Overlapping windows are downweighted according to record duration. The band is an **engineering support band**, not a statistical confidence interval or proof of physical identification; room-only storage can miss slow envelope drift. In v0.6.0 the band also includes regression-design sensitivity weighted by effective record duration, allowing for coupling between heat loss, solar gain, internal gain and the storage proxy. The extra allowance is reported as `heat_loss_regression_allowance_kW_K`. The former solar/internal hard support bands have been replaced by soft priors for dynamic fitting. Explicitly fixed gains stay fixed.

Stage 2 optimizes heat capacities, resistance ratio, heat loss within that support, gains within their support and noise scales. It includes an independent energy residual obtained by driving the envelope equation with the measured room and outdoor temperature trajectory, without Kalman corrections. The integrated interior balance infers required heat from `Ci*delta(Troom) + integral((Troom-Te)/Rie) - integral(solar+internal gains)`. Envelope storage is therefore modeled explicitly at this stage. Envelope initialization assumes the steady temperature division at each segment start; very short records remain sensitive to this assumption. Signed required energy is retained rather than clipped to zero.

Normal acceptance also requires at least one complete withheld 24-hour energy window. Its RMS error in average thermal load must be no more than the larger of 0.15 kW and 25% of average measured heating load, and must not worsen the active model by more than 5% (0.15 kW floor). All previous temperature, physical and identifiability checks remain. Reaching the heat-loss support boundary prevents normal acceptance. Parameter intervals use local combined-objective curvature and remain conditional approximations.

The full House fit status report contains `energy_signature`, `energy_training`, `energy_validation`, `baseline_energy_validation`, `energy_validated` and `energy_anchor_enforced`. Energy validation evaluates heat needed for the measured room trajectory; it is distinct from open-loop temperature-forecast validation. Active heat-loss/solar/internal-gain entities retain their applied signature attributes across previews and restarts.

For a one-button override: **Settings → Devices & services → Floor Heat Estimator → device → Import and apply provisional house fit**. This imports available Recorder history, runs the forced fit and applies a converged candidate as provisional. It bypasses normal quality gates but **does not remove the energy-signature constraints or the need for a complete measured training day**. The existing manual house restore button remains available. Automatic fits do not force application.


## Restart recovery and identifiable fitting (v0.4.0)

### Underfloor initialization

When floor output has no usable state, a background task looks for the last **valid measured** water-temperature observation in retained local/Recorder history. The circulation threshold, space-heating mode and settling period still apply. It initializes both models to `(supply + return)/2` at that historical timestamp and advances them with the recorded heat input and room temperature, in chronological order, using current parameters. The result seeds the live model, including when heating is off and the floor is cooling. Supply/return measurements without valid flow are never used for initialization.

History imports use daily Recorder executor queries and a lookback sufficient for the configured maximum prediction age. Imported/local data are deduplicated with live recordings taking precedence. Missing mode, required power, room data, excessive gaps, expired observations or a missing history tail prevent initialization. Known non-heating mode permits zero heat input; unknown mode does not. No missing values are filled with zero. Failed recovery retries after five minutes, and normal fresh circulation observations can initialize immediately. Exposed `initialization` attributes show the recovered measurement time, replay count or reason recovery was incomplete. Unload cancels recovery work.

### House state and midnight correction

Midnight corrects the running model; it does not reset it. Short compatible states remain useful, and house state is now saved every minute instead of every fifteen minutes. Startup replays retained source history; missing/stale state schedules a background Recorder/history recovery, replaying raw floor inputs with the current floor calibration where applicable. A gap lacking driving inputs cannot be reconstructed by holding current power backwards. Recovery reports incomplete history if it cannot reach the current timestep. Initial cold states still use the resistance-weighted room/outdoor guess; the first valid room measurement can initialize at any time of day. Missing inputs may require another seed; state validity is never fabricated.

The fit estimates a shared initial hidden-temperature offset across training segments, bounded to ±15 K relative to their equilibrium starting guess, as a nuisance parameter. That offset is included in filtering/validation and final fitted-state replay. This is an approximation for multiple disconnected segments, not a separate free initial temperature for every segment.

### Physical structure and model order

Overall heat loss remains anchored to the measured energy signature. Configurable log-resistance-ratio and log-capacity prior penalties (strength 0–10, default 1) reduce compensation between the split and capacities; zero disables the corresponding soft prior. Trusted accepted dynamics are preferred as prior values; otherwise the configured manual capacities and split are used.

Structure checks reject resistance fractions outside 0.02–0.98, modes faster than twice the five-minute sampling interval, slow modes longer than the larger of one week and three times the training span, modal time-scale ratios below two, and capacities at their physical limits. These engineering screens are explicit assumptions, not a proof of physical identification. The fit also trains a separate 1R1C baseline on training data. Normal two-state acceptance requires at least 5% lower 6-hour and 24-hour validation RMSE than that model, alongside all energy/temperature/curvature checks. Reports include `candidate_structure`, `active_structure`, `first_order_validation` and `second_order_supported`.

Force can relax evidence/forecast-coverage gates but cannot apply physically bounded or collapsed dynamics. If newly fitted dynamics are unresolved, a provisional candidate retains trusted prior capacities and resistance split while keeping the energy-anchored heat loss and gain estimates. If those prior dynamics fail the structural checks too, application is refused and the existing model is retained. `dynamics_prior_dominated` explicitly labels retained dynamics. A weakly identified hidden-node temperature is an estimate under those priors, not a measured wall temperature.

### Gain learning

Solar area and internal gain each have `fixed`, `prior`, and `free` choices. Defaults are `prior`. Fixed preserves the configured parameter. Prior learns with a regularization scale of the larger of 2 m² and half the prior solar area, or 250 W for internal gain; reliable previous accepted values are used as gain priors where available. Free removes the gain-prior penalty while preserving physical bounds. The signature and dynamic stages honor each choice independently. Short records no longer automatically fix both gains. Per-gain `learning_status` attributes distinguish fixed, prior-dominated, learned-with-prior and weakly identified/free values. Conditional curvature intervals still exclude energy-support/prior uncertainty.

### Function grouping and upgrade

One integration now has **Underfloor** and **House** device pages, each with its own controls and diagnostics. Existing entity unique IDs remain unchanged; Home Assistant retains existing entity IDs and dashboard references. Configuration opens a menu for Underfloor or House; editing one preserves the other's settings. Floor source selection and floor model/learning remain sequential under Underfloor. The House page contains its own sources, model, learning and forecast settings.

House learning-policy changes invalidate incompatible saved house calibration, without deleting recorded history or floor calibration. After updating/restarting, review priors and gain modes and run the appropriate house fit. The existing Import and apply provisional house fit button remains available. This release does not change your Codex model setting, install itself on Home Assistant, or implement dynamic hourly heat-pump scheduling.


## Restart-gap recovery correction (v0.4.1)

Version 0.4.0 rejected every missing required input during replay. A temporary startup `unavailable` record could therefore prevent every later retry from reaching now, even when live inputs had recovered.

Replay now permits a bounded **held-input estimate** for an interior gap when both boundary samples are valid, their mode matches exactly, room temperatures differ by at most 0.5 K, and active-heating power differs by at most the larger of 0.05 kW and 5% of the preceding power. The entire boundary interval must be within the configured maximum gap. Known contradictory modes inside the gap prevent reconstruction. The model uses the preceding room/power values through that gap; these are assumptions, not measured data. In known non-heating mode the modeled heat input remains zero.

Each such gap appears in `initialization.estimated_gaps` with timestamps, duration, sample count and assumption text. Supply/return/flow are cleared on estimated rows so they cannot become synthetic water-temperature observations. Original recordings and fitting data are unchanged. Missing tails, long gaps, changed mode/power or larger room changes still block recovery. Failure diagnostics include the first failed timestamp, missing input names and underlying model status.

This repairs the initialization path; it does not guarantee that an arbitrary history gap can be reconstructed. After updating and restarting/reloading the integration, allow the background recovery to finish. It retries every five minutes if current inputs are still unavailable. No recalibration is required for this patch.


## Non-heating recovery gaps (v0.5.1)

A short interruption can end in a different non-heating mode: for example, `dhw` before a restart and `standby` afterward. Since this heating-only integration assumes zero floor heat outside `hc` (or the configured heating mode), those boundaries are equivalent for floor cooldown. Recovery now allows a bounded hold-input estimate between them when room temperatures differ by no more than 0.5 K, the interval stays within the configured maximum gap, and no recorded heating contradicts that assumption. Known modes are retained; missing water measurements are never synthesized. A positive thermal-power reading above 50 W with unknown mode prevents this non-heating estimate.

`initialization.estimated_gaps` lists the boundary modes and the zero-input/held-room assumption. Remaining failures include `initialization.unbridged_gap` with boundary timestamps, temperatures, modes and the rejection reason. Genuine heating/non-heating transitions and long or unbounded gaps still require usable history or a fresh valid measurement. Fit rejection and missing runtime initialization are separate statuses; validation criteria are unchanged.


## Continuous house fitting (v0.6.0)

Update through HACS, restart Home Assistant, then use **Fit house now** (or import history and fit if needed). Existing accepted/provisional coefficients, source choices, recorded history, live correction policy and floor calibration are preserved. Updating does not force a fit or replace parameters before validation. The default refit schedule remains unchanged. New diagnostics appear under **House fit status → report**.

Measured heat-pump heat input remains a valid configurable source; its history is used as recorded. This fit then represents the combined heating-system/house response at that input boundary. The integration does not switch it to estimated floor output. The solar input remains the configured GHI sensor; window-oriented direct/diffuse solar modeling is a separate future change.


## Solar uncertainty and heat imbalance (v0.7.0)

In **Settings → Devices & services → Floor Heat Estimator → Configure → House**, three settings are added:

| Setting | Default | Meaning |
|---|---:|---|
| Solar uncertainty for thermal fitting | 5 m² | Uncertain effective solar area, 0–100 m²; larger values reduce solar-period influence on RC identification |
| Solar influence decay after sunset | 3 h | Exponential memory of GHI used only for fitting weights, 0.25–48 h |
| Heat imbalance window | 60 min | Complete rolling observed-energy window; choose 30 or 60 minutes |

This does not change your configured solar sensor, heat input, two-temperature model, floor model or midnight correction. The new settings preserve active coefficients, history and live model state across reloads. They take effect on the next fit; press **Fit house now** after updating/restarting. A rejected fit still retains the old calibration. The default weekly refit schedule remains in effect.

### Solar influence on fitting

At each training timestamp, compute an exponential GHI average with the configured memory. Let `G_eff = sqrt(GHI² + average_GHI²)` (GHI in kW/m²). The fixed residual multiplier is `w = 1/sqrt(1 + (uncertain_area * G_eff / 0.15 kW)²)`. Thus strong sun reduces influence immediately and prior solar exposure decays after sunset. This is an input-uncertainty proxy, not a simulated extra thermal store or a claim to know actual solar gain.

The multiplier applies to temperature, fixed-lag change and energy residuals during RC identification. Change terms use RMS exposure across their whole time interval; energy terms use mean exposure and do not assume solar errors average away as independent samples. Weights, data selection and exposure memory are computed once from training timestamps/GHI, independent of all trial capacities, resistances, gains and temperatures. The energy-signature support still anchors total resistance; weighting does not make heat-loss inference completely independent of solar assumptions.

With nonzero solar uncertainty, the best converged thermal candidate is followed by a bounded linear least-squares **gain-only** stage. This stage uses unweighted training residuals and the existing fixed/prior/free gain modes. Both capacities, both resistances and the initial hidden-state offsets are frozen. Solar/internal gains cannot move the RC split again. Validation, daily energy acceptance and reported 30/60/180-minute errors remain fully unweighted. Neither training weights nor gain selection uses future validation targets.

Set solar uncertainty to **0** to disable both weighting and the separate gain stage, retaining the v0.6.0 joint objective. The nonzero default is intended to reduce sensitivity to an imperfect GHI model, not guarantee that the solar peak or every heating cycle will be reproduced. Fit diagnostics expose `solar_weighting`, `rc_identification_parameters`, `rc_identification_objective`, and `gain_fit_stage`.

### Heat imbalance sensor

**House heat imbalance** is signed power in W, calculated from measured room temperature and the modeled envelope temperature:

`imbalance energy = Ci * measured room-temperature change + integral((measured Ti - estimated Te)/Rie) - integral(heating + solar gain + internal gain)`

Divide by the configured 30/60-minute interval to obtain mean power. Positive means additional heat would be required to explain the observed temperature trajectory; negative means extra cooling or less input. This residual can also reflect wrong parameters, heat-input timing, weather errors or thermostat measurement effects. It is not a direct measurement of occupancy, ventilation or solar gains, and no fitted heat-imbalance input is added to the state equations.

Room temperature is integrated by trapezoids, rather than a noisy instantaneous derivative. The envelope integral follows the exact constant-input model transition **before** midnight Kalman correction. Correction jumps are never counted as physical stored heat. Missing room/driver intervals, genuine gaps and parameter changes invalidate the averaging window. The sensor initially needs one complete 30/60-minute window and may remain unavailable when inputs are incomplete. A restart retains compatible house state but rebuilds this diagnostic window.

Sensor attributes include the interval endpoints and bias/RMS of available overlapping rolling windows ending within the last 24 hours. These are descriptive statistics, not independent samples or confidence intervals. `House fit status → report.heat_imbalance.training` and `.validation` expose **unweighted** bias, RMS and mean absolute imbalance in W for both 30- and 60-minute windows. Fit diagnostics use the continuous simulated envelope trajectory; the live diagnostic uses the midnight-corrected envelope estimate. Heat imbalance never corrects model temperatures or changes predicted heat demand.
