from math import cos, radians


DATUM_LAT = 53.025
DATUM_LON = 4.800


def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    """Approximate local MOOS X/Y in metres relative to datum."""

    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * cos(radians(DATUM_LAT))

    x = (lon - DATUM_LON) * meters_per_deg_lon
    y = (lat - DATUM_LAT) * meters_per_deg_lat

    print(x, y)
    return x, y

if __name__=="__main__":
    latlon_to_xy(53.003300, 4.781767)