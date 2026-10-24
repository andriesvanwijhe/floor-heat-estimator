"""Optional house-model configuration; existing floor entries stay unchanged."""

HOUSE_DEFAULTS = {
    "house_enabled": False,
    "house_heat_input": "sensor.floor_heat_estimator_exponent_heat_output",
    "house_outdoor_temperature": "sensor.ebusd_broadcast_outsidetemp_2",
    "house_solar": "sensor.open_meteo_global_solar_radiation",
    "house_ci": 5.0,
    "house_ce": 30.0,
    "house_rie": 1.0,
    "house_rea": 4.0,
    "house_solar_area": 10.0,
    "house_internal_gain": 500.0,
    "house_learning_days": 28.0,
    "house_refit_interval_days": 7.0,
    "house_max_fit_minutes": 15.0,
    "house_max_fit_rmse": 1.0,
    "house_max_gap_minutes": 10.0,
    "house_learning_enabled": True,
    "house_apply_fitted_parameters": True,
    "house_import_history_on_start": True,
}

HOUSE_KEYS = ("house_ci", "house_ce", "house_rie", "house_rea", "house_solar_area", "house_internal_gain")
HOUSE_SOURCES = ("house_heat_input", "house_outdoor_temperature", "house_solar")
HOUSE_LIMITS = {
    "house_ci": (0.02, 1000, "kWh/K"),
    "house_ce": (0.02, 1000, "kWh/K"),
    "house_rie": (0.01, 100, "K/kW"),
    "house_rea": (0.01, 100, "K/kW"),
    "house_solar_area": (0.001, 500, "m²"),
    "house_internal_gain": (0, 5000, "W"),
    "house_learning_days": (7, 90, "d"),
    "house_refit_interval_days": (1, 90, "d"),
    "house_max_fit_minutes": (1, 60, "min"),
    "house_max_fit_rmse": (0.05, 5, "K"),
    "house_max_gap_minutes": (5, 60, "min"),
}
