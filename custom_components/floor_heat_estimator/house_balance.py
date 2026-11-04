"""Observed interior heat imbalance; diagnostics never feed heat into a model."""

from collections import deque
import math


def interval_imbalance(before, predicted, room_before, room_after, outdoor,
                       heat, solar, parameters, hours):
    """kWh residual before any Kalman correction, with exact envelope integral.

    Positive means observed warming needs additional heat beyond modeled inputs.
    Room temperature is integrated by trapezoids. Global model energy balance
    gives integral(Te) exactly for the freely propagated, constant-input step.
    """
    if room_before is None or room_after is None:
        return None
    p = parameters
    power = heat+p.solar_area*solar+p.internal_gain
    envelope_integral = outdoor*hours+p.rea*(power*hours
        -p.ci*(predicted[0]-before[0])-p.ce*(predicted[1]-before[1]))
    room_integral = (room_before+room_after)*hours/2
    return (p.ci*(room_after-room_before)+(room_integral-envelope_integral)/p.rie
            -power*hours)


class HeatBalanceTracker:
    """Only complete, contiguous windows with one physical calibration."""

    def __init__(self):
        self.intervals = deque(maxlen=300)
        self.calibration = None
        self._cache = {}

    def clear(self):
        self.intervals.clear()
        self.calibration = None
        self._cache.clear()

    def push(self, interval, parameters):
        calibration = tuple(parameters.to_dict().items())
        if interval is None:
            self.clear()
            return
        start, end, residual = interval
        if (not all(math.isfinite(v) for v in interval) or abs(end-start-300)>1):
            self.clear()
            return
        if self.calibration != calibration or (self.intervals and abs(start-self.intervals[-1][1])>1):
            self.clear()
        self.calibration = calibration
        self.intervals.append((start,end,residual))
        self._cache.clear()

    def data(self, minutes):
        if minutes in self._cache:
            return self._cache[minutes].copy()
        count = int(minutes/5)
        intervals = list(self.intervals)
        if len(intervals)<count:
            return {}
        # The 24-hour statistics describe overlapping rolling windows; they do
        # not imply independent evidence or a statistical confidence interval.
        cutoff = intervals[-1][1]-86400
        values = [1000*sum(r[2] for r in intervals[i-count:i])/(minutes/60)
                  for i in range(count,len(intervals)+1)
                  if intervals[i-1][1]>=cutoff]
        result = {'value_W': values[-1], 'window_minutes': minutes,
                'start_utc_epoch': intervals[-count][0], 'end_utc_epoch': intervals[-1][1],
                'rolling_window_count':len(values), 'bias_W':sum(values)/len(values),
                'rms_W':math.sqrt(sum(v*v for v in values)/len(values))}
        self._cache[minutes] = result
        return result.copy()
