"""Configuration defaults for the experimental floor heat estimator."""

DOMAIN = "floor_heat_estimator"
VERSION = "0.1.2"
SOURCE_KEYS = ("heat_input", "room_temperature", "supply_temperature", "return_temperature", "flow", "mode")
DEFAULTS = {
    "name": "Floor heat estimator",
    "active_mode": "hc",
    "flow_threshold": 100.0,
    "settling_minutes": 10.0,
    "linear_capacity": 0.208787,
    "linear_conductance": 0.387208,
    "nonlinear_capacity": 0.198873,
    "nonlinear_output_at_5k": 1.615004,
    "exponent": 1.500063,
    "maximum_gap_minutes": 10.0,
    "maximum_prediction_hours": 24.0,
    "record_samples": True,
}

