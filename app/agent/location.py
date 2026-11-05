"""Location-discovery agent tool with persisted, user-visible research steps."""
from app.agent.state import Step
from app.database import now
from app.location.resolver import resolve


def resolve_location(st, save, use_llm=True):
    active = None

    def notify(action, summary, details):
        nonlocal active
        if active is not None:
            active.status, active.finished_at = 'completed', now()
        active = Step(index=len(st.steps)+1, action=action, summary=summary,
                      reason='Research the claim location using retrieved geographic evidence.',
                      decided_by='llm' if action in ('extract_geographic_clues', 'cross_check_location') else 'system',
                      details=details)
        st.steps.append(active)
        st.current_step = action
        save()

    parent = Step(index=len(st.steps)+1, action='resolve_location', summary='Discovering the claim location.',
                  reason='No predefined or manually supplied location was provided.', decided_by='system')
    st.steps.append(parent)
    st.tool_call_count += 1
    save()
    result = resolve(st.claim, notify=notify, use_llm=use_llm)
    st.location_resolution = result
    ok = result['status'] == 'resolved'
    if active:
        active.status, active.finished_at = ('completed' if ok else 'failed'), now()
    parent.status, parent.finished_at = ('completed' if ok else 'failed'), now()
    parent.summary = ('Geographic location identified — retrieving satellite imagery.' if ok else result['explanation'])
    parent.details = {'status': result['status'], 'attempts': len(result['attempts']), 'sources': result['sources']}
    st.current_step = None
    if ok:
        st.location = result['location']
        st.progress_percent = max(st.progress_percent, 6)
    else:
        st.status = 'awaiting_location' if result['candidates'] else 'location_unresolved'
        st.error = {'code': 'LOCATION_AMBIGUOUS' if result['candidates'] else 'LOCATION_UNRESOLVED',
                    'message': result['explanation']}
    save()
    return ok
