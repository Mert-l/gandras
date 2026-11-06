"""Geographic scope limits are independent of spectral evidence strength."""


def constrain_verdict(verdict: dict, location: dict | None, resolution: dict | None) -> dict:
    """Force "inconclusive" when the system itself picked an unverified sampling area.

    An automatically discovered point plus a provisional window may not be the claimed site at all,
    so its measurements cannot settle the claim. Once the user confirms a candidate or enters
    coordinates (selection == "user"), the area is treated like a manually supplied `custom` AOI:
    the measured verdict stands, and the location's uncertainty notes stay in the limitations.
    A published, validated project boundary is never constrained.
    """
    if not resolution or (location or {}).get('boundary_status') == 'verified' or resolution.get('selection') == 'user':
        return verdict
    return {**verdict, 'status': 'inconclusive', 'confidence': 'low', 'summary':
            'Inconclusive for the original claim: changes were assessed only within an automatically selected, '
            'provisional sampling area. Confirm or correct the location to evaluate the claim there. '
            'The project/property boundary has not been verified, so project-wide conclusions are not established.'}
