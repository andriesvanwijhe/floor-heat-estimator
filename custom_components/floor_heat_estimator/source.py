"""Normalise live and historical sensor states using their own units."""

from .model import finite_number


def numeric_state(state, kind):
    if state is None:
        return None
    number = finite_number(state.state)
    if number is None:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
    if kind == "heat_input":
        if unit == "w":
            return number / 1000
        return number if unit == "kw" else None
    if kind == "flow":
        if unit in ("l/h", "l/hr", "l/hour"):
            return number
        if unit in ("l/min", "l/m"):
            return number * 60
        if unit in ("m³/h", "m3/h"):
            return number * 1000
        return None
    if unit in ("°f", "f"):
        return (number - 32) * 5 / 9
    return number if unit in ("°c", "c", "") else None


