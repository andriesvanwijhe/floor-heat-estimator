"""Local, steady-state demand calculation from existing Open-Meteo arrays."""

from datetime import UTC, datetime, time, timedelta
import math
from zoneinfo import ZoneInfo


def weather_intervals(attributes, start, timezone, maximum_hours=48, minimum_hours=24):
    """Rolling five-minute-aligned horizon; radiation is preceding-hour mean.

    Only consecutive real forecast hours are used. Never extrapolate a weather
    source or substitute zeros for missing radiation. Partial first/last clock
    hours retain their actual duration, including daylight-saving transitions.
    """
    times=attributes.get('time',[])
    temperatures=attributes.get('temperature_2m',[])
    radiation=attributes.get('shortwave_radiation',[])
    if not times or not len(times)==len(temperatures)==len(radiation):
        return []
    zone=ZoneInfo(attributes.get('timezone') or timezone)
    rows={}
    for raw,temperature,ghi in zip(times,temperatures,radiation):
        try:
            if isinstance(raw,bool): return []
            if isinstance(raw,(int,float)): stamp=float(raw)
            else:
                value=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                if value.tzinfo is None:
                    if value.replace(tzinfo=zone,fold=0).utcoffset()!=value.replace(tzinfo=zone,fold=1).utcoffset():
                        return []
                    value=value.replace(tzinfo=zone)
                stamp=value.timestamp()
            if not math.isfinite(stamp) or stamp in rows: return []
            rows[stamp]=(temperature,ghi)
        except (TypeError,ValueError,OverflowError): return []
    result=[];cursor=start;finish=start+maximum_hours*3600
    while cursor<finish:
        boundary=math.floor(cursor/3600)*3600
        current,following=rows.get(boundary),rows.get(boundary+3600)
        if current is None or following is None:break
        t0,t1,ghi=current[0],following[0],following[1]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in (t0,t1,ghi)) or ghi<0:
            break
        end=min(boundary+3600,finish)
        midpoint=((cursor+end)/2-boundary)/3600
        result.append({'start':float(cursor),'seconds':float(end-cursor),
            'outdoor_C':float(t0+(t1-t0)*midpoint),'solar_kW_m2':float(ghi/1000)})
        cursor=end
    return result if cursor-start>=minimum_hours*3600 else []


def tomorrow_demand(attributes, parameters, now, timezone, target=20):
    """Require full local-day coverage; GHI is the preceding hour's mean.

    Open-Meteo temperature is instantaneous, so average both endpoints of
    each hour to pair it with the preceding-hour radiation value. UTC epoch
    timestamps preserve the 23/25-hour days at daylight-saving transitions.
    """
    zone = ZoneInfo(timezone)
    day = now.astimezone(zone).date()+timedelta(days=1)
    start = datetime.combine(day, time(), zone).timestamp()
    end = datetime.combine(day+timedelta(days=1), time(), zone).timestamp()
    result = {"status": "incomplete_forecast", "date": day.isoformat(),
              "target_temperature_C": target, "timezone": timezone,
              "source": "Open-Meteo", "attribution": "https://open-meteo.com/",
              "method": "hourly steady state; no thermal storage or warm-up allowance",
              "hourly": []}
    try:
        times = attributes["time"]
        temperatures = attributes["temperature_2m"]
        radiation = attributes["shortwave_radiation"]
        if not (len(times) == len(temperatures) == len(radiation)):
            return result
        source_zone = ZoneInfo(attributes.get("timezone") or timezone)
        rows = {}
        for raw, temperature, ghi in zip(times, temperatures, radiation):
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                stamp = float(raw)
            else:
                stamp_date = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if stamp_date.tzinfo is None:
                    first = stamp_date.replace(tzinfo=source_zone, fold=0)
                    second = stamp_date.replace(tzinfo=source_zone, fold=1)
                    # Offset-free ambiguous times cannot reliably identify an
                    # interval; fail closed rather than invent duplicate hours.
                    if first.utcoffset() != second.utcoffset():
                        continue
                    stamp_date = first
                stamp = stamp_date.timestamp()
            if stamp in rows:
                return result
            rows[stamp] = (temperature, ghi)
        hours = []
        cursor = start
        while cursor < end:
            current, following = rows.get(cursor), rows.get(cursor+3600)
            if current is None or following is None:
                return result
            t0, t1, ghi = current[0], following[0], following[1]
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                   for v in (t0, t1, ghi)) or ghi < 0:
                return result
            outside = (t0+t1)/2
            signed = (target-outside)/(parameters.rie+parameters.rea)-parameters.solar_area*ghi/1000-parameters.internal_gain
            hours.append({"start": datetime.fromtimestamp(cursor, UTC).isoformat(),
                          "outdoor_temperature_C": outside, "ghi_W_m2": ghi,
                          "heat_load_kW": max(0, signed)})
            cursor += 3600
        energy = sum(row["heat_load_kW"] for row in hours)
        result.update(status="ready", hourly=hours, energy_kWh=energy,
                      average_load_kW=energy/len(hours),
                      peak_load_kW=max(row["heat_load_kW"] for row in hours), hours=len(hours))
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return result
