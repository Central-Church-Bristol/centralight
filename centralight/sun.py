"""Sunrise and sunset in Bristol, for the "Sunrise / sunset" automation condition.

The sunrise equation (the sun's centre 0.833° below the horizon, for refraction and the sun's size), good to about
two minutes here: plenty for the lights. Times come back in the PC's own time zone, so British Summer Time is
handled by Windows, like every other time in CentraLight.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from functools import lru_cache

BRISTOL = (51.4545, -2.5879)  # latitude, longitude (degrees; east is positive)

_J2000 = 2451545.0  # the Julian day of 1 Jan 2000, 12:00 UT
_UNIX_EPOCH = 2440587.5  # the Julian day of 1 Jan 1970, 00:00 UT


@lru_cache(maxsize=64)
def sun_times(day: date, latitude: float = BRISTOL[0], longitude: float = BRISTOL[1]) -> tuple[datetime, datetime] | None:
    """(sunrise, sunset) on `day`, as naive local datetimes; None where the sun doesn't rise or set that day."""
    n = day.toordinal() + 1721425.0 - _J2000 + 0.0008  # days since J2000 (at noon UT on `day`)
    mean_noon = n - longitude / 360.0
    anomaly = math.radians((357.5291 + 0.98560028 * mean_noon) % 360.0)
    centre = 1.9148 * math.sin(anomaly) + 0.0200 * math.sin(2 * anomaly) + 0.0003 * math.sin(3 * anomaly)
    ecliptic = math.radians((math.degrees(anomaly) + centre + 180.0 + 102.9372) % 360.0)
    transit = _J2000 + mean_noon + 0.0053 * math.sin(anomaly) - 0.0069 * math.sin(2 * ecliptic)
    sin_dec = math.sin(ecliptic) * math.sin(math.radians(23.4397))
    cos_dec = math.cos(math.asin(sin_dec))
    phi = math.radians(latitude)
    cos_hour = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_dec) / (math.cos(phi) * cos_dec)
    if not -1.0 <= cos_hour <= 1.0:
        return None
    half_day = math.degrees(math.acos(cos_hour)) / 360.0
    return _local(transit - half_day), _local(transit + half_day)


def _local(julian_day: float) -> datetime:
    return datetime.fromtimestamp(round((julian_day - _UNIX_EPOCH) * 86400.0))


def sun_minutes(day: date, event: str) -> int | None:
    """Minutes after local midnight of `day`'s sunrise or sunset ("sunrise" / "sunset")."""
    times = sun_times(day)
    if times is None:
        return None
    moment = times[0] if event == "sunrise" else times[1]
    return moment.hour * 60 + moment.minute
