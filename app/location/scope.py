"""Geographic scope limits are independent of spectral evidence strength."""


def constrain_verdict(verdict: dict, location: dict | None, resolution: dict | None) -> dict:
    if resolution and (location or {}).get('boundary_status') != 'verified':
        return {**verdict, 'status': 'inconclusive', 'confidence': 'low', 'summary':
                'Inconclusive for the original claim: changes were assessed only within a provisional sampling area. '
                'The project/property boundary has not been verified, so project-wide conclusions are not established.'}
    return verdict
