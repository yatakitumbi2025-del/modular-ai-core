# steam_props.py
from iapws import IAPWS97

def phase_state(T_initial_C, T_final_C, fill_fraction):
    """
    fill_fraction: fraction of container volume initially
    occupied by liquid water (0 to 1)
    Returns saturation pressure at both temps and whether
    liquid grows, shrinks, or vanishes.
    """
    T1 = T_initial_C + 273.15
    T2 = T_final_C + 273.15

    sat1 = IAPWS97(T=T1, x=0.5)  # saturation state at T1
    sat2 = IAPWS97(T=T2, x=0.5)  # saturation state at T2

    # critical density ~322 kg/m3 for water
    critical_density = 322.0
    # overall density if liquid fills fill_fraction, vapor the rest
    rho_liquid = sat1.Liquid.rho
    rho_vapor = sat1.Vapor.rho
    overall_density = fill_fraction * rho_liquid + (1 - fill_fraction) * rho_vapor

    if overall_density > critical_density:
        outcome = "liquid expands, vapor space shrinks, container approaches all-liquid"
    elif overall_density < critical_density:
        outcome = "liquid shrinks, vapor space grows, liquid may fully evaporate"
    else:
        outcome = "at critical density, liquid-vapor interface vanishes at critical point"

    return {
        "P_initial_bar": round(sat1.P * 10, 3),   # IAPWS97 P is in MPa
        "P_final_bar": round(sat2.P * 10, 3),
        "overall_density_kg_m3": round(overall_density, 1),
        "critical_density_kg_m3": critical_density,
        "outcome": outcome,
        "note": "System stays saturated throughout; liquid does not boil in a sealed rigid vessel."
    }

if __name__ == "__main__":
    import json
    result = phase_state(25, 200, fill_fraction=0.5)
    print(json.dumps(result, indent=2))
