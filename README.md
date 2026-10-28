# Floor Heat Estimator

An experimental Home Assistant custom integration for estimating heat released by an underfloor heating circuit during heating and cooldown.

Version 0.3.1 adds a fitted constant internal heat gain (initially 500 W) to the optional, automatically fitted house digital twin using the two-state model in Bacher & Madsen (2011), Figure 1. It includes initial Recorder history import, separate house fitting settings, hidden envelope temperature and physical-parameter entities. Models can use manual coefficients or validated learned coefficients. Floor and house identification remain separate stages of the thermal-analysis tool.

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

Maximum likelihood is calculated from Kalman innovations. Strictly positive parameters are optimized in log coordinates with multiple starts. The internal heat gain is optimized linearly in kW with bounds 0–5 kW, allowing a genuine zero-gain fit. Physical dynamics are initialized with fixed noise scales before optimizing the physical and noise parameters together; this reduces convergence to a poor RC model masked by high process noise. Each segment starts with a diffuse envelope-state covariance, and its first six hours are filtered but excluded from the fitting likelihood.

Initial eligibility requires at least 6.9 days of span and 80% of seven days' measured five-minute observations. The latest 25% of the window, or at least two days, is withheld. There must be at least three training days and two validation days at 80% coverage. Training must include at least 0.5 K room variation, 3 K outdoor variation, 0.5 kW heat-input variation, 50 W/m² solar variation, and at least two hours each of sunny and dark bins. These are screening rules, not evidence of unique physical parameters by themselves.

Acceptance requires convergence, physical parameters away from bounds (except an admissible zero internal gain), adequate observed-information identifiability, and passing later-day forecast checks. Parameter covariance is approximated from the numerical likelihood Hessian in optimizer coordinates, marginalizing the nuisance noise scales. Internal-gain curvature uses one-sided differences at its zero bound; other physical parameters use log coordinates. Ill-conditioned/non-positive information or log standard errors above 1.1 reject a fit rather than publish precise-looking learned values. The internal-gain standard error must be no more than the larger of 100 W and twice its fitted value. Internal-gain intervals are approximate Wald intervals clipped at zero; they are not profile-likelihood intervals at the boundary.

Forecasts are launched hourly from filter states using only room observations available at each origin. They then run **without future room-temperature corrections** over 1, 6 and 24 hours. Historical future heat/outdoor/solar inputs are used: these are conditional retrospective validation errors, not the error expected with uncertain future weather/heating forecasts. Each horizon needs at least four valid forecasts. Both 6-hour and 24-hour RMSE must pass the ceiling, not exceed the active-model RMSE by more than 5% (with a 0.1 K floor), and beat or match a persistence baseline (also with a 0.1 K floor). Signed bias and standardized innovation lag-one correlation are reported too.

Rejected, insufficient, timed-out and failed fits preserve the active coefficients. Preview reports do not replace active coefficients or their confidence intervals. Learned values are verified in persistent storage before activation. A house fit is discarded if the floor calibration changed while it ran. Short-restart model state is saved separately; stale or incompatible snapshots are discarded.

### House entities

Enabling adds 23 diagnostic sensors, three tomorrow-demand sensors and three buttons on the existing device. Existing floor entity unique IDs are unchanged.

| Entity name | Unit / interpretation |
|---|---|
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

The device buttons are **Fit house now**, **Import house history and fit**, and **Restore manual house parameters**. Corresponding actions work in Developer tools and automations:

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

The model treats the thermostat as representative of the modelled interior zone. A single fitted constant now accounts for average occupancy and electrical-device heat. Time-varying gains from other heaters, cooking or occupants, open windows, changing ventilation and weather-data errors can still bias its coefficients. Heating output is itself a floor-model estimate that uses room temperature; this introduces input uncertainty and dependence that the independent-noise model only approximates. The reported confidence intervals and state uncertainty therefore do not include every source of uncertainty and are not a calibration certificate.

The effective solar area includes orientation, shading and transmission effects; it is not the actual window area. Confirm that the solar sensor reports irradiance with meaningful observation timestamps. W/m² and kW/m² are supported; energy totals such as kWh/m² are rejected. Values are held according to their recorded timestamps: preceding-hour averages are **not** automatically shifted or deconvolved, and coarse source timing can bias the fitted solar response. Purged or never-recorded solar history cannot be reconstructed by the integration.

Synthetic tests verify exact state/diffusion propagation against an independent matrix exponential and recover known stochastic model parameters, including the constant internal heat gain. These checks do not establish the accuracy of a fit on your house. Runtime depends on training coverage and convergence. A synthetic nine-day fit took about 14 seconds with three starts on the development computer; this is not a benchmark of the HP thin client. Use the elapsed-time diagnostics and configurable time limit to assess your own installation.


### Constant internal gains (0.3.1)

`Q_internal` represents combined average heat released by occupants and electrical devices. It is connected directly to the **interior node Ti/Ci**, alongside floor heat and solar gain. The envelope receives that energy through Rie; there is no second gain applied directly to Ce.

The manual initial value is **500 W**. Configure it as **Manual occupancy and electrical heat gain (W)** on the house options page (0–5000 W). Internally the model uses kW. **House internal heat gain** exposes the active value in W, its parameter source, last-applied-fit time and approximate 95% interval in W after a successful fit. It is one constant over a fitted window, not a live occupancy sensor or an estimate of individual appliance consumption.

The fitter now identifies six physical coefficients jointly: Ci, Ce, Rie, Rea, effective solar area and internal gain. All existing data-coverage, excitation and held-out forecast requirements remain. The additional coefficient can be correlated with solar gain and heat loss; poorly identifiable fits are rejected while the prior/manual coefficients remain active.

The upgrade preserves floor calibration, manual house settings, recordings and source history. House calibrations learned with the old zero-internal-gain model are invalidated instead of silently adding 500 W on top of coefficients that may have absorbed those gains. The model starts from the configured manual house coefficients plus 500 W (unless overridden) and schedules a fresh fit when learning is enabled. Editing the manual internal-gain starting value also invalidates active learned house coefficients, like other manual house parameter edits.


### Midnight correction, provisional fits and tomorrow demand (v0.3.2)

The live house model still integrates measured heat, outdoor temperature and solar input every five minutes. It only performs a Kalman room-temperature correction at the first valid completed midnight bin in Home Assistant's configured timezone. If the midnight room measurement is missing, it retries during the midnight hour, once per local date. This is a weighted Kalman correction, not an exact reset to the thermostat. Startup and recovery after missing driving inputs initialize from the room reading, including during the day. A brief restart preserves the last correction; upgrades discard old five-minute-corrected live state but retain fitted coefficients and training history.

Fitting still uses five-minute observations for maximum-likelihood identification. Retrospective forecast validation starts from a twin replayed with midnight-only corrections, matching live operation. Future room readings never correct an individual forecast trajectory.

Both house fit actions accept `force: true`. Normal eligibility and quality gates are then bypassed and explicitly listed in `bypassed_checks`; parameters are **provisional**, not validated. The irreducible minimum is twelve hours of usable inputs, 96 measured five-minute points, and 24 measured training observations after a continuous six-hour warm-up. Optimization must converge to a finite solution within physical bounds and the configured time limit. A forced fit defaults to preview even if automatic application is enabled. To activate it explicitly:

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
