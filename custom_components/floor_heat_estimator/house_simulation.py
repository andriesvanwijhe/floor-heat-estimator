"""Fixed-data continuous simulation objective; no observation corrections.

NumPy/SciPy are imported only by the isolated fitting worker. Temperatures
are propagated exactly over completed five-minute, constant-input bins.
"""

import math

if __package__:
    from .house_energy import energy_windows
    from .house_model import initial_state, transition
else:
    from house_energy import energy_windows
    from house_model import initial_state, transition


CHANGE_SCALES = {6: .10, 12: .15, 36: .25}  # 30, 60, 180 minutes; K
TEMPERATURE_SCALE = .25  # K
ENERGY_SCALE = .15  # kW mean over a complete 24/72-hour window
INITIAL_OFFSET_SCALE = 5.0  # K, regularized nuisance initial envelope state


class SimulationData:
    """Prepare all segments, targets, pairs, windows and weights ONCE.

    Selection depends only on timestamps/data availability, never parameters,
    heating mode or sunshine thresholds. A missing room target does not break
    propagation. Missing drivers or a genuine time gap do.
    """

    def __init__(self, points, check=lambda: None):
        import numpy as np
        self.check = check
        self.points = points
        self.rows = np.asarray(points, dtype=float).reshape((-1, 5))
        self.segments = []
        self.segment_id = np.full(len(points), -1, dtype=int)
        start = previous = None
        for i, row in enumerate(self.rows):
            if i % 256 == 0:
                check()
            drivers_ok = np.isfinite(row[[0, 2, 3, 4]]).all()
            continuous = previous is not None and abs(row[0]-previous-300) <= 1
            if start is not None and (not drivers_ok or not continuous):
                self.segments.append((start, i))
                start = None
            if drivers_ok and start is None and math.isfinite(row[1]):
                start = i
            previous = row[0] if drivers_ok else None
        if start is not None:
            self.segments.append((start, len(points)))
        for j, (left, right) in enumerate(self.segments):
            self.segment_id[left:right] = j
        seeds = {left for left, _ in self.segments}
        self.targets = np.array([i for i, r in enumerate(self.rows)
                                 if self.segment_id[i] >= 0 and i not in seeds
                                 and math.isfinite(r[1])], dtype=int)
        self.days = len(set(int(self.rows[i, 0]//86400) for i in self.targets))
        self.target_weights = self._day_weights(self.targets)/TEMPERATURE_SCALE
        self.pairs = {}
        for lag, scale in CHANGE_SCALES.items():
            right = np.array([i for i in self.targets if i >= lag
                and self.segment_id[i-lag] == self.segment_id[i]
                and math.isfinite(self.rows[i-lag, 1])], dtype=int)
            self.pairs[lag] = (right-lag, right,
                self._day_weights(right)/(scale*math.sqrt(len(CHANGE_SCALES))))
        # energy_windows checks measured-room coverage and exact timestamps.
        self.windows = [w for w in energy_windows(points)
                        if self.segment_id[w['start']] >= 0
                        and self.segment_id[w['start']] == self.segment_id[w['end']]]
        self.window_weights = np.full(len(self.windows),
            math.sqrt(max(1, self.days)/max(1, len(self.windows)))/ENERGY_SCALE)

    def _day_weights(self, indices):
        import numpy as np
        if not len(indices):
            return np.array([], dtype=float)
        days = (self.rows[indices, 0]//86400).astype(int)
        _, inverse, counts = np.unique(days, return_inverse=True, return_counts=True)
        # Each represented calendar day has equal weight, regardless of gaps.
        return np.sqrt(max(1, self.days)/(len(counts)*counts[inverse]))

    def trajectories(self, parameters):
        """Return zero-offset trajectory and d(state)/d(initial Te) per row."""
        import numpy as np
        from scipy.signal import lfilter
        p = parameters
        _, _, gains = transition(p, 1/12)
        # Diagonalize the continuous generator, whose eigenvalues are distinct,
        # rather than Phi (which can underflow to repeated zero eigenvalues).
        a = np.array([[-1/(p.rie*p.ci), 1/(p.rie*p.ci)],
                      [1/(p.rie*p.ce), -(1/p.rie+1/p.rea)/p.ce]])
        # Capacity scaling makes the generator symmetric, guaranteeing real
        # eigenvectors even with extreme capacity/resistance ratios.
        capacity_root = np.sqrt([p.ci, p.ce])
        symmetric = a*capacity_root[:, None]/capacity_root[None, :]
        rates, orthogonal = np.linalg.eigh(symmetric)
        vectors = orthogonal/capacity_root[:, None]
        decays = np.exp(rates/12)
        inverse = orthogonal.T*capacity_root[None, :]
        states = np.full((len(self.rows), 2), np.nan)
        sensitivity = np.zeros_like(states)
        for left, right in self.segments:
            self.check()
            block = self.rows[left:right]
            seed = initial_state(block[0, 1], block[0, 2], p)[0]
            forcing = (np.outer(block[:, 2], gains[:2])
                       + np.outer(block[:, 3]+p.solar_area*block[:, 4]+p.internal_gain, gains[2:]))
            forcing = forcing@inverse.T
            modes = np.empty((len(block), 2))
            modes[0] = inverse@seed
            for j in range(2):
                if len(block) > 1:
                    modes[1:, j], _ = lfilter([1.0], [1.0, -decays[j]],
                        forcing[1:, j], zi=[decays[j]*modes[0, j]])
            states[left:right] = modes@vectors.T
            response = decays[None, :]**np.arange(len(block))[:, None]
            sensitivity[left:right] = (response*(inverse@np.array([0., 1.]))[None, :])@vectors.T
        return states, sensitivity

    def interval_energy(self, parameters, states, sensitivity):
        """Required heat for observed Ti and freely simulated Te, exact Te integral.

        Integrate the measured room trajectory by the trapezoidal rule. The
        hidden state integral uses A^-1*(x_end-x_start-b*dt), not Euler steps.
        Returns kWh per interval and its linear initial-Te sensitivity.
        """
        import numpy as np
        p = parameters
        a = np.array([[-1/(p.rie*p.ci), 1/(p.rie*p.ci)],
                      [1/(p.rie*p.ce), -(1/p.rie+1/p.rea)/p.ce]])
        inverse = np.linalg.inv(a)
        energy = np.full(len(self.rows), np.nan)
        derivative = np.zeros(len(self.rows))
        for left, right in self.segments:
            block = self.rows[left:right]
            dt = 1/12
            power = block[1:, 3]+p.solar_area*block[1:, 4]+p.internal_gain
            driving = np.column_stack((power/p.ci, block[1:, 2]/(p.rea*p.ce)))
            integrals = (np.diff(states[left:right], axis=0)-driving*dt)@inverse.T
            d_integrals = np.diff(sensitivity[left:right], axis=0)@inverse.T
            room_integral = (block[:-1, 1]+block[1:, 1])*dt/2
            energy[left+1:right] = (p.ci*np.diff(block[:, 1])
                +(room_integral-integrals[:, 1])/p.rie
                -(p.solar_area*block[1:, 4]+p.internal_gain)*dt)
            derivative[left+1:right] = -d_integrals[:, 1]/p.rie
        return energy, derivative

    def evaluate(self, parameters, offsets=None, profile=False):
        """Profile independent initial Te offsets analytically, without reseeding.

        For fixed physical parameters all residuals are affine in each segment's
        initial Te. Their regularized least-squares minimizers are therefore
        analytic; many history gaps do not add optimizer dimensions. Complete
        energy windows and change pairs never cross segment boundaries.
        """
        import numpy as np
        states, sensitivity = self.trajectories(parameters)
        error = states[:, 0]-self.rows[:, 1]
        energy, d_energy = self.interval_energy(parameters, states, sensitivity)
        pieces = []
        pieces.append((error[self.targets]*self.target_weights,
            sensitivity[self.targets, 0]*self.target_weights, self.segment_id[self.targets]))
        for left, right, weight in self.pairs.values():
            pieces.append(((error[right]-error[left])*weight,
                (sensitivity[right, 0]-sensitivity[left, 0])*weight, self.segment_id[right]))
        energy_errors = np.array([(np.sum(energy[w['start']+1:w['end']+1])/w['hours']-w['heat_kW'])
                                  for w in self.windows])
        energy_slopes = np.array([np.sum(d_energy[w['start']+1:w['end']+1])/w['hours']
                                  for w in self.windows])
        window_ids = np.array([self.segment_id[w['start']] for w in self.windows], dtype=int)
        pieces.append((energy_errors*self.window_weights, energy_slopes*self.window_weights, window_ids))
        base = np.concatenate([v[0] for v in pieces])
        slope = np.concatenate([v[1] for v in pieces])
        groups = np.concatenate([v[2] for v in pieces])
        values = np.zeros(len(self.segments))
        if profile:
            numerator = np.bincount(groups, weights=base*slope, minlength=len(values))
            denominator = np.bincount(groups, weights=slope*slope, minlength=len(values))
            values = -numerator/(denominator+1/INITIAL_OFFSET_SCALE**2)
        elif offsets:
            values = np.array([offsets.get(self.points[left][0], 0.) for left, _ in self.segments])
        residual = base+slope*values[groups]
        # Bounds are checked for acceptance, not used to switch observations.
        residual = np.concatenate((residual, values/INITIAL_OFFSET_SCALE))
        valid = self.segment_id >= 0
        states[valid] += sensitivity[valid]*values[self.segment_id[valid], None]
        error = states[:, 0]-self.rows[:, 1]
        energy[valid] += d_energy[valid]*values[self.segment_id[valid]]
        return {'residual': residual, 'data_residual': residual[:-len(values)] if len(values) else residual,
                'states': states, 'errors': error, 'energy': energy,
                'offsets': {self.points[left][0]: float(v) for (left, _), v in zip(self.segments, values)}}

    def metrics(self, result, after=None):
        """Fixed-lag accuracy and amplitude diagnostics on chronological data."""
        import numpy as np
        def eligible(indices):
            return indices if after is None else indices[self.rows[indices, 0] >= after]
        targets = eligible(self.targets)
        errors = result['errors'][targets]
        report = {'count': len(targets), 'rmse_K': float(np.sqrt(np.mean(errors**2))) if len(errors) else None,
                  'bias_K': float(np.mean(errors)) if len(errors) else None, 'changes': {}}
        for lag, (left, right, _) in self.pairs.items():
            mask = np.ones(len(right), dtype=bool) if after is None else self.rows[left, 0] >= after
            left, right = left[mask], right[mask]
            difference = result['errors'][right]-result['errors'][left]
            observed = self.rows[right, 1]-self.rows[left, 1]
            predicted = result['states'][right, 0]-result['states'][left, 0]
            norm = float(np.linalg.norm(observed))
            report['changes'][str(lag*5)] = {'count': len(right),
                'rmse_K': float(np.sqrt(np.mean(difference**2))) if len(right) else None,
                'rms_amplitude_ratio': float(np.linalg.norm(predicted)/norm) if norm > 1e-9 else None}
        return report

    def energy_metrics(self, result, after=None):
        import numpy as np
        records = []
        for w in self.windows:
            if after is not None and self.points[w['start']][0] < after:
                continue
            predicted = float(np.sum(result['energy'][w['start']+1:w['end']+1]))
            measured = w['heat_kW']*w['hours']
            records.append({'hours': w['hours'], 'start_utc_epoch': self.points[w['start']][0],
                            'measured_kWh': measured, 'predicted_kWh': predicted,
                            'error_kWh': predicted-measured})
        if not records:
            return {'count': 0, 'windows': []}
        errors = [w['error_kWh']/w['hours'] for w in records]
        return {'count': len(records), 'rmse_mean_load_kW': math.sqrt(sum(v*v for v in errors)/len(errors)),
                'bias_mean_load_kW': sum(errors)/len(errors),
                'mean_measured_load_kW': sum(abs(w['measured_kWh'])/w['hours'] for w in records)/len(records),
                'windows': records}
